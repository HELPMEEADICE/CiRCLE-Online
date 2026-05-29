import time
from datetime import datetime
from typing import Optional, Callable
from backend.models import ChatMessage
from backend.utils import (
    get_logger,
    parse_message_text,
    extract_image_urls,
    normalize_message_segments_for_dedup,
    normalize_raw_message_for_dedup,
)

logger = get_logger("napcat_handler")


class NapCatMessageHandler:
    # message_id 去重：同一 message_id 在 TTL 内只处理一次
    _DEDUP_TTL = 10.0  # 秒
    _CONTENT_DEDUP_TTL = 2.0  # 秒，同一 QQ 消息会几乎同时从多个端口到达

    def __init__(self):
        self.on_group_message: Optional[Callable] = None
        self.on_private_message: Optional[Callable] = None
        self.on_any_message: Optional[Callable] = None
        self._message_log: list[ChatMessage] = []
        self._max_log_size = 100
        self._message_buffer = None
        self._seen_msg_ids: dict[int, float] = {}  # message_id -> first_seen_time
        self._seen_message_keys: dict[tuple, float] = {}  # message signature -> first_seen_time
        self._message_id_aliases: dict[tuple, dict[int, int]] = {}  # message signature -> port -> message_id

    def _build_message_key(self, data: dict, raw_message: str) -> tuple:
        message_segments = data.get("message", [])
        message_text = parse_message_text(message_segments) if message_segments else normalize_raw_message_for_dedup(raw_message)
        return (
            data.get("message_type", ""),
            str(data.get("group_id", "")),
            str(data.get("user_id", "")),
            data.get("time", ""),
            message_text,
            normalize_message_segments_for_dedup(message_segments),
        )

    def set_message_buffer(self, buffer):
        """设置消息缓冲区"""
        self._message_buffer = buffer

    def _is_duplicate(self, message_id: int) -> bool:
        """检查 message_id 是否重复，并清理过期条目"""
        if not message_id:
            return False
        now = time.monotonic()
        # 清理过期条目（批量清理避免每次调用都遍历）
        if len(self._seen_msg_ids) > 100:
            cutoff = now - self._DEDUP_TTL
            self._seen_msg_ids = {k: v for k, v in self._seen_msg_ids.items() if v > cutoff}
        # 检查是否重复
        if message_id in self._seen_msg_ids:
            if now - self._seen_msg_ids[message_id] < self._DEDUP_TTL:
                return True
        self._seen_msg_ids[message_id] = now
        return False

    def _is_duplicate_event(self, data: dict, raw_message: str) -> bool:
        """检查跨端口重复事件。

        NapCat 多端点连接同一群时，同一条 QQ 消息可能以不同 message_id
        同时推送到每个端口；用短时间窗口内的消息内容签名兜底去重。
        """
        now = time.monotonic()
        if len(self._seen_message_keys) > 500:
            cutoff = now - self._CONTENT_DEDUP_TTL
            self._seen_message_keys = {
                k: v for k, v in self._seen_message_keys.items() if v > cutoff
            }

        key = self._build_message_key(data, raw_message)
        if len(self._message_id_aliases) > 500:
            cutoff = now - self._CONTENT_DEDUP_TTL
            self._message_id_aliases = {
                k: v for k, v in self._message_id_aliases.items()
                if self._seen_message_keys.get(k, 0) > cutoff
            }

        aliases = self._message_id_aliases.setdefault(key, {})
        message_id = data.get("message_id", 0)
        if message_id:
            aliases[data.get("_port", 0)] = message_id
            data["_message_ids_by_port"] = aliases

        first_seen = self._seen_message_keys.get(key)
        if first_seen is not None and now - first_seen < self._CONTENT_DEDUP_TTL:
            return True

        self._seen_message_keys[key] = now
        return False

    async def handle_event(self, port: int, data: dict):
        post_type = data.get("post_type", "")

        if post_type == "message":
            await self._handle_message(port, data)
        elif post_type == "meta_event":
            await self._handle_meta_event(port, data)
        elif post_type == "notice":
            await self._handle_notice(port, data)
        elif post_type == "request":
            await self._handle_request(port, data)

    async def _handle_message(self, port: int, data: dict):
        data["_port"] = port
        message_type = data.get("message_type", "")
        message_id = data.get("message_id", 0)

        message_segments = data.get("message", [])
        raw_message = data.get("raw_message", "")
        user_id = str(data.get("user_id", ""))
        group_id = str(data.get("group_id", ""))
        sender = data.get("sender", {})
        sender_name = sender.get("card", "") or sender.get("nickname", "")

        if not raw_message and message_segments:
            raw_message = parse_message_text(message_segments)

        # 去重：同一条消息通过多个端口到达时只处理一次
        if self._is_duplicate_event(data, raw_message):
            logger.debug(f"[Port {port}] Duplicate message event, skipping")
            return
        if self._is_duplicate(message_id):
            logger.debug(f"[Port {port}] Duplicate message_id {message_id}, skipping")
            return

        chat_msg = ChatMessage(
            role="user",
            content=raw_message,
            qq_id=user_id,
        )
        self._add_to_log(chat_msg)

        logger.info(f"[Port {port}] {message_type} msg from {sender_name}({user_id}): {raw_message[:50]}")

        if self.on_any_message:
            await self.on_any_message(port, data)

        if message_type == "group":
            # 如果启用了消息缓冲区，推入缓冲区
            if self._message_buffer:
                from backend.message_buffer import BufferedMessage
                buffered_msg = BufferedMessage(
                    port=port,
                    data=data,
                    timestamp=datetime.now(),
                    group_id=group_id,
                    user_id=user_id,
                    raw_message=raw_message,
                    sender_name=sender_name,
                    message_segments=message_segments,
                    image_urls=extract_image_urls(message_segments),
                )
                await self._message_buffer.push(group_id, buffered_msg)
            elif self.on_group_message:
                # Fallback：如果没有缓冲区，直接调用原有回调
                await self.on_group_message(port, data)
        elif message_type == "private" and self.on_private_message:
            await self.on_private_message(port, data)

    async def _handle_meta_event(self, port: int, data: dict):
        meta_type = data.get("meta_event_type", "")
        if meta_type == "heartbeat":
            logger.debug(f"[Port {port}] Heartbeat")
        elif meta_type == "lifecycle":
            sub_type = data.get("sub_type", "")
            logger.info(f"[Port {port}] Lifecycle: {sub_type}")

    async def _handle_notice(self, port: int, data: dict):
        notice_type = data.get("notice_type", "")
        logger.debug(f"[Port {port}] Notice: {notice_type}")

    async def _handle_request(self, port: int, data: dict):
        request_type = data.get("request_type", "")
        logger.info(f"[Port {port}] Request: {request_type}")

    def _add_to_log(self, message: ChatMessage):
        self._message_log.append(message)
        if len(self._message_log) > self._max_log_size:
            self._message_log = self._message_log[-self._max_log_size:]

    def get_recent_messages(self, count: int = 20) -> list[ChatMessage]:
        return self._message_log[-count:]
