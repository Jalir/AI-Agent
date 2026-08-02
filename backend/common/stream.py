"""SSE token / status 队列：供 LangGraph 节点写入、HTTP 流式端点消费。

并发约定：
- 每个 thread_id 同一时刻最多一个 ActiveRun（run_id 标识所有权）
- 同会话重入时 begin_run 会取消旧 run；旧流 finally 只在仍持有 run_id 时清理
- 不同 thread_id 可真正并行（asyncio 多任务）

取消约定：
- 前端 AbortController 断开 SSE → 服务端检测 disconnect / CancelledError
- 可选 POST /api/chat/stop 设置 cancel Event，协同取消 graph 任务
- 节点在流式循环中协作检查 is_cancel_requested，尽快停 LLM / 工具
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

STREAM_DONE = object()


@dataclass
class ActiveRun:
    """单轮对话运行句柄（按 thread_id 注册，run_id 防清理竞态）。"""

    run_id: str
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None


_runs: dict[str, ActiveRun] = {}
_registry_lock = asyncio.Lock()


async def begin_run(thread_id: str) -> ActiveRun:
    """为 thread 开启新一轮运行；若已有活跃 run 则先取消。"""
    tid = (thread_id or "").strip()
    if not tid:
        raise ValueError("thread_id required")

    new = ActiveRun(run_id=uuid.uuid4().hex)
    async with _registry_lock:
        old = _runs.get(tid)
        _runs[tid] = new

    if old is not None:
        old.cancel.set()
        if old.task is not None and not old.task.done():
            old.task.cancel()
        logger.info(
            "Replaced active run for thread %s (old=%s new=%s)",
            tid,
            old.run_id[:8],
            new.run_id[:8],
        )
    return new


def bind_run_task(thread_id: str, run_id: str, task: asyncio.Task) -> None:
    """把 graph Task 挂到指定 run（仅当仍是当前所有者）。"""
    cur = _runs.get(thread_id)
    if cur is not None and cur.run_id == run_id:
        cur.task = task


def end_run(thread_id: str, run_id: str) -> None:
    """结束本轮：仅当 run_id 仍匹配时移除注册，避免误清后来者。"""
    cur = _runs.get(thread_id)
    if cur is not None and cur.run_id == run_id:
        _runs.pop(thread_id, None)


def get_active_run(thread_id: str | None) -> ActiveRun | None:
    if not thread_id:
        return None
    return _runs.get(thread_id)


# ---- 兼容旧 API（节点侧仍用 get_token_queue / is_cancel_requested）----


def register_token_queue(thread_id: str) -> asyncio.Queue:
    """同步注册队列（无取消旧 run）。优先使用 begin_run。"""
    run = ActiveRun(run_id=uuid.uuid4().hex)
    _runs[thread_id] = run
    return run.queue


def unregister_token_queue(thread_id: str) -> None:
    _runs.pop(thread_id, None)


def get_token_queue(thread_id: str | None) -> asyncio.Queue | None:
    run = get_active_run(thread_id)
    return run.queue if run else None


def register_cancel(thread_id: str) -> asyncio.Event:
    run = get_active_run(thread_id)
    if run is None:
        run = ActiveRun(run_id=uuid.uuid4().hex)
        _runs[thread_id] = run
    return run.cancel


def unregister_cancel(thread_id: str) -> None:
    # 由 end_run 统一清理；保留空实现以免旧调用报错
    return


def is_cancel_requested(thread_id: str | None) -> bool:
    run = get_active_run(thread_id)
    return bool(run and run.cancel.is_set())


def request_cancel(thread_id: str) -> bool:
    """请求停止指定会话的进行中生成。返回是否找到活跃取消句柄。"""
    run = get_active_run(thread_id)
    if run is None:
        return False
    found = False
    if not run.cancel.is_set():
        run.cancel.set()
        found = True
    if run.task is not None and not run.task.done():
        run.task.cancel()
        found = True
    if found:
        logger.info("Cancel requested for thread %s run=%s", thread_id, run.run_id[:8])
    return found


def register_active_run(thread_id: str, task: asyncio.Task) -> None:
    run = get_active_run(thread_id)
    if run is not None:
        run.task = task


def unregister_active_run(thread_id: str) -> None:
    # 由 end_run 统一清理
    return


async def emit_status(thread_id: str | None, status: str) -> None:
    """向 SSE 队列推送进度状态（type=status）。"""
    text = (status or "").strip()
    if not text:
        return
    q = get_token_queue(thread_id)
    if q is None:
        return
    await q.put({"type": "status", "content": text})


async def emit_file(thread_id: str | None, file_meta: dict) -> None:
    """向 SSE 队列推送可下载文件（type=file），供前端展示下载卡片。"""
    if not isinstance(file_meta, dict):
        return
    url = str(file_meta.get("url") or "").strip()
    if not url:
        return
    q = get_token_queue(thread_id)
    if q is None:
        return
    await q.put(
        {
            "type": "file",
            "name": str(file_meta.get("name") or "download.docx"),
            "url": url,
            "object_key": str(file_meta.get("object_key") or ""),
            "mime_type": str(file_meta.get("mime_type") or ""),
            "file_size": int(file_meta.get("file_size") or 0),
        }
    )


async def emit_chart(thread_id: str | None, chart: dict) -> None:
    """向 SSE 推送 ECharts 配置（type=chart），供前端渲染并可落库。"""
    if not isinstance(chart, dict):
        return
    option = chart.get("option")
    if not isinstance(option, dict) or not option:
        return
    q = get_token_queue(thread_id)
    if q is None:
        return
    await q.put(
        {
            "type": "chart",
            "chart_id": str(chart.get("chart_id") or ""),
            "title": str(chart.get("title") or ""),
            "option": option,
            "evidence": chart.get("evidence")
            if isinstance(chart.get("evidence"), dict)
            else {},
        }
    )


async def emit_xhs_card(thread_id: str | None, card: dict) -> None:
    """向 SSE 推送一条有序小红书图文卡片（type=xhs_card）。"""
    if not isinstance(card, dict):
        return
    try:
        index = int(card.get("index") or 0)
    except (TypeError, ValueError):
        index = 0
    if index <= 0:
        return
    q = get_token_queue(thread_id)
    if q is None:
        return
    tags = card.get("tags") or []
    if not isinstance(tags, list):
        tags = [str(tags)]
    await q.put(
        {
            "type": "xhs_card",
            "index": index,
            "title": str(card.get("title") or ""),
            "body": str(card.get("body") or ""),
            "tags": [str(t) for t in tags if str(t).strip()],
            "image_url": str(card.get("image_url") or "").strip(),
            "error": str(card.get("error") or "").strip(),
        }
    )


def make_sync_status_emitter(thread_id: str | None) -> Callable[[str], None]:
    """供同步代码（如 to_thread 内的检索）安全推送 status。

    通过 call_soon_threadsafe 把 put_nowait 调度到事件循环线程，避免跨线程写 asyncio.Queue。
    """
    if not thread_id:
        return lambda _msg: None

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return lambda _msg: None

    def emit(msg: str) -> None:
        text = (msg or "").strip()
        if not text:
            return
        q = get_token_queue(thread_id)
        if q is None:
            return

        def _put() -> None:
            try:
                q.put_nowait({"type": "status", "content": text})
            except Exception:
                pass

        try:
            loop.call_soon_threadsafe(_put)
        except Exception:
            pass

    return emit


def thread_id_from_config(config: Any) -> str | None:
    if not config:
        return None
    if isinstance(config, dict):
        return (config.get("configurable") or {}).get("thread_id")
    configurable = getattr(config, "get", None)
    if callable(configurable):
        return (config.get("configurable") or {}).get("thread_id")
    return None
