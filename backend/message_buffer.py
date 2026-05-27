"""消息缓冲区：替代固定延迟，实现动态消息聚合"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Callable, Awaitable
from backend.utils import get_logger

logger = get_logger("message_buffer")


@dataclass
class BufferedMessage:
    """缓冲区中的消息"""
    port: int
    data: dict
    timestamp: datetime
    group_id: str
    user_id: str
    raw_message: str
    sender_name: str
    message_segments: list = field(default_factory=list)
    image_urls: list = field(default_factory=list)


class MessageBuffer:
    """按群分组的消息缓冲区，替代固定延迟
    
    工作原理：
    - 消息到达时推入缓冲区，重置 flush 定时器
    - 定时器到期（默认3秒无新消息）时触发 flush
    - flush 时批量取出消息交给分配器处理
    
    优势：
    - 替代固定的 10 秒延迟
    - 连续消息会持续重置定时器，等待消息流平息
    - 不同群的消息独立处理，互不阻塞
    """
    
    def __init__(
        self,
        window_ms: int = 3000,
        max_size: int = 50,
        on_flush: Optional[Callable[[str, list[BufferedMessage]], Awaitable[None]]] = None,
    ):
        self._window_ms = window_ms
        self._max_size = max_size
        self._on_flush = on_flush
        
        # 按群分组的缓冲区
        self._buffers: dict[str, list[BufferedMessage]] = {}
        
        # 每个群的 flush 定时器
        self._timers: dict[str, asyncio.TimerHandle] = {}
        
        # 锁：防止同一群并发 flush
        self._locks: dict[str, asyncio.Lock] = {}
        
        # 统计
        self._stats: dict[str, dict] = {}
    
    async def push(self, group_id: str, message: BufferedMessage):
        """推入消息，重置 flush 定时器"""
        if group_id not in self._buffers:
            self._buffers[group_id] = []
            self._stats[group_id] = {"total": 0, "flushed": 0, "dropped": 0}
        
        # 检查缓冲区大小限制
        if len(self._buffers[group_id]) >= self._max_size:
            logger.warning(f"Buffer full for group {group_id}, forcing flush")
            await self._flush(group_id)
        
        # 添加到缓冲区
        self._buffers[group_id].append(message)
        self._stats[group_id]["total"] += 1
        
        logger.debug(f"Buffered message for group {group_id} "
                     f"(buffer size: {len(self._buffers[group_id])})")
        
        # 重置 flush 定时器
        self._reset_timer(group_id)
    
    def _reset_timer(self, group_id: str):
        """重置该群的 flush 定时器"""
        # 取消现有定时器
        if group_id in self._timers:
            self._timers[group_id].cancel()
        
        # 创建新定时器
        try:
            loop = asyncio.get_running_loop()
            self._timers[group_id] = loop.call_later(
                self._window_ms / 1000,
                lambda: asyncio.create_task(self._flush(group_id))
            )
        except RuntimeError:
            logger.warning(f"No event loop running, cannot set timer for group {group_id}")
    
    async def _flush(self, group_id: str):
        """flush 指定群的消息"""
        # 获取锁
        if group_id not in self._locks:
            self._locks[group_id] = asyncio.Lock()
        
        async with self._locks[group_id]:
            # 取出所有消息
            messages = self._buffers.get(group_id, [])
            if not messages:
                return
            
            # 清空缓冲区
            self._buffers[group_id] = []
            self._timers.pop(group_id, None)
            
            # 更新统计
            self._stats[group_id]["flushed"] += len(messages)
            
            logger.info(f"Flushing {len(messages)} messages for group {group_id}")
            
            # 调用回调
            if self._on_flush:
                try:
                    await self._on_flush(group_id, messages)
                except Exception as e:
                    logger.error(f"Flush callback error for group {group_id}: {e}")
    
    async def flush_all(self):
        """flush 所有群的消息（用于关闭时）"""
        for group_id in list(self._buffers.keys()):
            if self._buffers.get(group_id):
                await self._flush(group_id)
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "buffer_sizes": {gid: len(msgs) for gid, msgs in self._buffers.items()},
            "stats": self._stats.copy(),
        }
    
    def clear(self):
        """清空所有缓冲区"""
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()
        self._buffers.clear()
        logger.info("Message buffer cleared")


# 全局实例
_message_buffer: Optional[MessageBuffer] = None


def init_message_buffer(
    window_ms: int = 3000,
    max_size: int = 50,
    on_flush: Optional[Callable] = None,
) -> MessageBuffer:
    """初始化全局消息缓冲区"""
    global _message_buffer
    _message_buffer = MessageBuffer(
        window_ms=window_ms,
        max_size=max_size,
        on_flush=on_flush,
    )
    return _message_buffer


def get_message_buffer() -> Optional[MessageBuffer]:
    """获取全局消息缓冲区"""
    return _message_buffer
