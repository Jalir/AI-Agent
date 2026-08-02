"""按 thread 汇总本轮 LLM token 用量，供 SSE 推送给前端。"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StepUsage:
    name: str
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += max(0, int(prompt or 0))
        self.completion_tokens += max(0, int(completion or 0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class UsageSession:
    steps: dict[str, StepUsage] = field(default_factory=dict)
    estimated: bool = False

    def record(self, step: str, prompt: int, completion: int, *, estimated: bool = False) -> None:
        if estimated:
            self.estimated = True
        key = (step or "llm").strip() or "llm"
        if key not in self.steps:
            self.steps[key] = StepUsage(name=key)
        self.steps[key].add(prompt, completion)

    def snapshot(self) -> dict[str, Any]:
        ordered = list(self.steps.values())
        prompt = sum(s.prompt_tokens for s in ordered)
        completion = sum(s.completion_tokens for s in ordered)
        return {
            "type": "usage",
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "estimated": self.estimated,
            "steps": [s.to_dict() for s in ordered],
        }


_lock = threading.Lock()
# thread_id -> (run_id, session)；run_id 防止旧流清理误删新一轮用量
_sessions: dict[str, tuple[str, UsageSession]] = {}


def register_usage(thread_id: str, run_id: str = "") -> UsageSession:
    tid = (thread_id or "").strip()
    session = UsageSession()
    if not tid:
        return session
    rid = (run_id or "").strip() or "_"
    with _lock:
        _sessions[tid] = (rid, session)
    return session


def unregister_usage(thread_id: str, run_id: str = "") -> None:
    tid = (thread_id or "").strip()
    if not tid:
        return
    rid = (run_id or "").strip()
    with _lock:
        cur = _sessions.get(tid)
        if cur is None:
            return
        if rid and cur[0] != rid:
            return
        _sessions.pop(tid, None)


def get_usage(thread_id: str | None) -> UsageSession | None:
    tid = (thread_id or "").strip()
    if not tid:
        return None
    with _lock:
        cur = _sessions.get(tid)
        return cur[1] if cur else None


def pop_usage_snapshot(
    thread_id: str | None,
    run_id: str = "",
) -> dict[str, Any] | None:
    """取出并移除本轮用量快照（用于 SSE）。"""
    tid = (thread_id or "").strip()
    if not tid:
        return None
    rid = (run_id or "").strip()
    with _lock:
        cur = _sessions.get(tid)
        if cur is None:
            return None
        if rid and cur[0] != rid:
            return None
        _sessions.pop(tid, None)
        session = cur[1]
    snap = session.snapshot()
    if snap["total_tokens"] <= 0 and not snap["steps"]:
        return None
    return snap


def extract_token_counts(message: Any) -> tuple[int, int]:
    """从 LangChain AIMessage / chunk 提取 (prompt, completion)。"""
    if message is None:
        return 0, 0

    um = getattr(message, "usage_metadata", None)
    if isinstance(um, dict):
        prompt = um.get("input_tokens")
        if prompt is None:
            prompt = um.get("prompt_tokens")
        completion = um.get("output_tokens")
        if completion is None:
            completion = um.get("completion_tokens")
        if prompt is not None or completion is not None:
            return int(prompt or 0), int(completion or 0)

    # 部分版本把 usage 挂在 response_metadata
    meta = getattr(message, "response_metadata", None) or {}
    if isinstance(meta, dict):
        tu = meta.get("token_usage") or meta.get("usage") or {}
        if isinstance(tu, dict):
            prompt = tu.get("prompt_tokens") or tu.get("input_tokens") or 0
            completion = tu.get("completion_tokens") or tu.get("output_tokens") or 0
            if prompt or completion:
                return int(prompt), int(completion)

    return 0, 0


def _estimate_tokens(text: str) -> int:
    """无官方 usage 时的粗估：中文约 1.5 字/token，英文约 4 字符/token。"""
    t = text or ""
    if not t:
        return 0
    # 简易：按字符，CJK 权重更高
    cjk = sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")
    other = max(0, len(t) - cjk)
    return max(1, int(cjk / 1.5 + other / 4))


def record_llm_usage(
    thread_id: str | None,
    step: str,
    message: Any = None,
    *,
    prompt_text: str = "",
    completion_text: str = "",
) -> None:
    """记录一次调用；无官方数字时用文本粗估并标记 estimated。"""
    tid = (thread_id or "").strip()
    if not tid:
        return

    prompt, completion = extract_token_counts(message)
    estimated = False
    if prompt <= 0 and completion <= 0:
        prompt = _estimate_tokens(prompt_text) if prompt_text else 0
        completion = _estimate_tokens(completion_text) if completion_text else 0
        if prompt or completion:
            estimated = True
        else:
            return

    with _lock:
        cur = _sessions.get(tid)
        if cur is None:
            return
        cur[1].record(step, prompt, completion, estimated=estimated)

    logger.debug(
        "Usage %s step=%s prompt=%d completion=%d estimated=%s",
        tid,
        step,
        prompt,
        completion,
        estimated,
    )
