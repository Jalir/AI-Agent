"""进程内并发闸：限制同时进行的 graph 运行数，避免 LLM/内存被打满。

说明：
- SSE token 队列是进程内内存结构，部署时应单 worker（或会话粘滞），靠 asyncio 并发。
- 本闸门控制「同时跑多少轮对话」，与 uvicorn 连接数上限互补。
"""

from __future__ import annotations

import asyncio
import logging

from backend.config import settings

logger = logging.getLogger(__name__)

_sem: asyncio.Semaphore | None = None
_sem_limit: int | None = None


class ServerBusyError(Exception):
    """并发槽已满且等待超时。"""


def _get_semaphore() -> asyncio.Semaphore:
    global _sem, _sem_limit
    limit = max(1, int(settings.max_concurrent_runs or 32))
    if _sem is None or _sem_limit != limit:
        _sem = asyncio.Semaphore(limit)
        _sem_limit = limit
    return _sem


class RunSlot:
    """可显式 release 的并发槽（供 StreamingResponse 生命周期使用）。"""

    __slots__ = ("_acquired",)

    def __init__(self) -> None:
        self._acquired = False

    @property
    def acquired(self) -> bool:
        return self._acquired

    def mark_acquired(self) -> None:
        self._acquired = True

    def release(self) -> None:
        if not self._acquired:
            return
        self._acquired = False
        _get_semaphore().release()


async def acquire_run_slot() -> RunSlot:
    """申请一个运行槽；超时则抛 ServerBusyError。"""
    sem = _get_semaphore()
    wait = max(0.001, float(settings.max_concurrent_runs_wait_sec or 0) or 0.001)
    slot = RunSlot()
    try:
        await asyncio.wait_for(sem.acquire(), timeout=wait)
    except asyncio.TimeoutError as exc:
        logger.warning(
            "Run slot busy: limit=%s wait=%ss",
            _sem_limit,
            wait,
        )
        raise ServerBusyError("服务繁忙，请稍后重试。") from exc
    slot.mark_acquired()
    return slot
