import json
from typing import Optional, Callable
from backend.models import MessageEvent, ChatMessage
from backend.utils import get_logger, parse_message_text

logger = get_logger("napcat_handler")


class NapCatMessageHandler:
    def __init__(self):
        self.on_group_message: Optional[Callable] = None
        self.on_private_message: Optional[Callable] = None
        self.on_any_message: Optional[Callable] = None
        self._message_log: list[ChatMessage] = []
        self._max_log_size = 100

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
        message_type = data.get("message_type", "")
        message_segments = data.get("message", [])
        raw_message = data.get("raw_message", "")
        user_id = str(data.get("user_id", ""))
        group_id = str(data.get("group_id", ""))
        sender = data.get("sender", {})
        sender_name = sender.get("card", "") or sender.get("nickname", "")

        if not raw_message and message_segments:
            raw_message = parse_message_text(message_segments)

        chat_msg = ChatMessage(
            role="user",
            content=raw_message,
            qq_id=user_id,
        )
        self._add_to_log(chat_msg)

        logger.info(f"[Port {port}] {message_type} msg from {sender_name}({user_id}): {raw_message[:50]}")

        if self.on_any_message:
            await self.on_any_message(port, data)

        if message_type == "group" and self.on_group_message:
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
