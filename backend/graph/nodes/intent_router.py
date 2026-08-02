"""LangGraph 意图路由节点与条件边。

路由只做通用分支（薄）：
  - intent: chat | rag | media_gen | clarify
  - standalone_query / desired_count / clarify_question / confidence

产品模式（xhs_pack 等）由 tools 包声明 PRODUCT_HINT，经 client_intent 交给 agent。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal, Mapping, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from backend.common.llm import build_llm_fast
from backend.common.messages import (
    extract_text,
    last_user_image_count,
    last_user_text,
    recent_dialog_text,
)
from backend.common.stream import emit_status, thread_id_from_config
from backend.common.usage import record_llm_usage
from backend.graph.guard import fresh_guard_state
from backend.tools import PRODUCT_HINTS
from backend.tools.hints import client_hint_ids, map_client_to_route, match_product_hint
from backend.tools.public.search_kb import _MAX_TOP_K, extract_rag_query

logger = logging.getLogger(__name__)

# ---- 图分支意图（路由唯一合法输出）----

INTENT_CHAT = "chat"
INTENT_RAG = "rag"
INTENT_MEDIA_GEN = "media_gen"
INTENT_CLARIFY = "clarify"

INTENTS: tuple[str, ...] = (
    INTENT_CHAT,
    INTENT_RAG,
    INTENT_MEDIA_GEN,
    INTENT_CLARIFY,
)
DEFAULT_INTENT = INTENT_CHAT
INTENT_SET = frozenset(INTENTS)

# 图分支意图 ∪ 产品 hint id（前端按钮）
CLIENT_INTENT_SET = frozenset(INTENT_SET | client_hint_ids())

Confidence = Literal["high", "medium", "low"]


class RouteDecision(TypedDict):
    intent: str
    desired_count: int | None
    standalone_query: str
    clarify_question: str
    confidence: Confidence


_INTENT_SYSTEM_PROMPT = """你是用户意图理解器。根据「近期对话」与「用户最新一句话」，输出一个 JSON。
只输出 JSON，不要 markdown、不要解释。

字段（全部必填）：
{
  "intent": "chat" | "rag" | "media_gen" | "clarify",
  "desired_count": number | null,
  "standalone_query": string,
  "clarify_question": string,
  "confidence": "high" | "medium" | "low"
}

意图：
- chat: 闲聊/常识/翻译等，不必查用户知识库
- rag: 需检索知识库，且已能写成可检索的 standalone_query
- media_gen: 要生成图/视频/海报/批量图文等多媒体（需要文字素材时写 standalone_query）
- clarify: 仅当「最新一句 + 近期对话」仍无法确定主题时反问；禁止罗列已被后续轮次取代的旧主题

规则：
- 省略/跟进句（再来一份、生成图文、那个也行…）未给新主题 → 继承对话中**最近**明确的用户主题，写成 standalone_query；优先 rag 或 media_gen，不要 clarify
- 批量图文/小红书图文/每条配图 → media_gen；standalone_query 写主题
- 基于知识库写纯文案/列表仍是 rag，不是 media_gen
- desired_count：用户明确条数则填整数，否则 null
- standalone_query：rag 必填；media_gen 需要库内文字时填写；做查询归一（纠拼音/消指代），去掉套话
- clarify_question：仅 clarify 时填一句短反问；其它为 ""
- 不确定是否查库 → 偏 chat；查库但主题补不出 → clarify
- 本轮附带图片且在问图/识图 → chat
"""

_FOLLOWUP_RE = re.compile(
    r"(?:再来|继续|那个|上面|刚才|同上|同样|还有呢|也行|就这个|按这个|"
    r"生成图文|做图文|出图文|图文包|小红书图文|批量图文|"
    r"整理|导出|下载)",
)

_COUNT_RE = re.compile(
    r"(?:"
    r"(?:列出|给出|返回|推荐|找|查|要|需要|给我|帮我)\s*(\d{1,2})\s*(?:条|个|道|份|种|项|篇|款|例|味)?"
    r"|"
    r"(?:top|前)\s*-?\s*(\d{1,2})"
    r"|"
    r"(\d{1,2})\s*(?:条|个|道|份|种|项|篇|款|例)"
    r")",
    re.IGNORECASE,
)


def _normalize_intent(raw: str) -> str:
    """收敛到图分支意图；旧名 xhs_pack → media_gen。"""
    text = (raw or "").strip().strip("\"'`").lower()
    text = text.replace(" ", "").replace("\n", "")
    text = re.sub(r"[^a-z_]", "", text)
    if text in PRODUCT_HINTS:
        return PRODUCT_HINTS[text].route_intent
    if text in ("xhs", "xiaohongshu"):
        return INTENT_MEDIA_GEN
    if text in INTENT_SET:
        return text
    for name in INTENTS:
        if name in text:
            return name
    return DEFAULT_INTENT


def _parse_json_object(raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        return {}
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}


def _clamp_count(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if n < 1:
        return None
    return min(n, _MAX_TOP_K)


def extract_desired_count(user_text: str) -> int | None:
    t = (user_text or "").strip()
    if not t:
        return None
    m = _COUNT_RE.search(t)
    if not m:
        return None
    for g in m.groups():
        if g:
            return _clamp_count(g)
    return None


def _looks_like_followup(user_text: str) -> bool:
    t = (user_text or "").strip()
    return bool(t) and bool(_FOLLOWUP_RE.search(t))


def _product_from_utterance(user_text: str) -> str:
    hit = match_product_hint(user_text=user_text or "")
    return hit.id if hit else ""


def _last_user_topic_from_dialog(dialog_history: str) -> str:
    if not (dialog_history or "").strip():
        return ""
    users: list[str] = []
    for line in dialog_history.splitlines():
        line = line.strip()
        if line.startswith("用户："):
            users.append(line[3:].strip())
    for u in reversed(users):
        if not u:
            continue
        if _looks_like_followup(u):
            stripped = _FOLLOWUP_RE.sub("", u)
            stripped = re.sub(r"[\s，。！？、,.!?;；:：的了吗呢啊哦]+", "", stripped)
            if len(stripped) < 2:
                continue
        return u
    return ""


def _map_client_hint(client: str | None) -> tuple[str | None, str]:
    return map_client_to_route(client, graph_intents=INTENT_SET)


def _decision(
    *,
    intent: str,
    desired_count: int | None = None,
    standalone_query: str = "",
    clarify_question: str = "",
    confidence: Confidence = "high",
) -> RouteDecision:
    intent = _normalize_intent(intent)

    if intent == INTENT_CLARIFY:
        q = (clarify_question or "").strip()
        return {
            "intent": INTENT_CLARIFY,
            "desired_count": None,
            "standalone_query": (standalone_query or "").strip(),
            "clarify_question": q or "您想查的具体是什么？可以说得更清楚一些吗？",
            "confidence": confidence,
        }

    if intent in (INTENT_RAG, INTENT_MEDIA_GEN):
        return {
            "intent": intent,
            "desired_count": _clamp_count(desired_count),
            "standalone_query": (standalone_query or "").strip(),
            "clarify_question": "",
            "confidence": confidence,
        }

    return {
        "intent": INTENT_CHAT,
        "desired_count": None,
        "standalone_query": "",
        "clarify_question": "",
        "confidence": confidence,
    }


def normalize_route_decision(data: dict, *, user_text: str) -> RouteDecision:
    intent = _normalize_intent(str(data.get("intent") or ""))
    conf_raw = str(data.get("confidence") or "medium").strip().lower()
    confidence: Confidence = (
        conf_raw if conf_raw in ("high", "medium", "low") else "medium"  # type: ignore[assignment]
    )

    count = _clamp_count(data.get("desired_count")) or extract_desired_count(user_text)
    standalone = str(data.get("standalone_query") or "").strip()
    clarify_q = str(data.get("clarify_question") or "").strip()

    if intent == INTENT_RAG and not standalone:
        intent = INTENT_CLARIFY
        if not clarify_q:
            clarify_q = "我想确认一下：您具体想在知识库里查什么？"

    if intent == INTENT_CLARIFY and not clarify_q:
        clarify_q = "您的问题有点不清楚，能再说具体一点吗？"

    return _decision(
        intent=intent,
        desired_count=count,
        standalone_query=standalone,
        clarify_question=clarify_q,
        confidence=confidence,
    )


def _slots_from_text(user_text: str, *, intent: str) -> RouteDecision:
    count = extract_desired_count(user_text)
    standalone = ""
    if intent == INTENT_RAG:
        standalone = extract_rag_query(user_text) or (user_text or "").strip()
    return _decision(
        intent=intent,
        desired_count=count,
        standalone_query=standalone,
        confidence="medium",
    )


def _finalize_decision(
    decision: RouteDecision,
    *,
    user_text: str,
    dialog_history: str,
    forced: str | None,
    product_hint: str,
) -> RouteDecision:
    topic = _last_user_topic_from_dialog(dialog_history)
    count = decision.get("desired_count") or extract_desired_count(user_text)
    standalone = (decision.get("standalone_query") or "").strip()
    intent = decision["intent"]
    conf: Confidence = decision.get("confidence") or "medium"  # type: ignore[assignment]

    product = PRODUCT_HINTS.get(product_hint) if product_hint else None
    if product is None:
        product = match_product_hint(user_text=user_text or "")
    if product and product.route_intent == INTENT_MEDIA_GEN:
        intent = INTENT_MEDIA_GEN
        standalone = standalone or topic
        conf = "medium"
        product_hint = product.id

    if forced == INTENT_CHAT and intent != INTENT_CLARIFY:
        return _decision(intent=INTENT_CHAT, confidence="high")
    if forced == INTENT_MEDIA_GEN and intent not in (INTENT_CLARIFY,):
        return _decision(
            intent=INTENT_MEDIA_GEN,
            desired_count=count,
            standalone_query=standalone or topic,
            confidence="high" if product_hint else conf,
        )
    if forced == INTENT_RAG:
        if intent == INTENT_MEDIA_GEN:
            return _decision(
                intent=INTENT_MEDIA_GEN,
                desired_count=count,
                standalone_query=standalone or topic,
                confidence=conf,
            )
        if intent != INTENT_CLARIFY:
            intent = INTENT_RAG
            standalone = standalone or topic

    use_media = bool(
        (product and product.route_intent == INTENT_MEDIA_GEN)
        or (
            product_hint in PRODUCT_HINTS
            and PRODUCT_HINTS[product_hint].route_intent == INTENT_MEDIA_GEN
        )
    )
    if intent == INTENT_CLARIFY and topic and (
        _looks_like_followup(user_text) or use_media
    ):
        logger.info(
            "Context inherit: rescue clarify → %s topic=%r",
            "media_gen" if use_media else "rag",
            topic[:60],
        )
        return _decision(
            intent=INTENT_MEDIA_GEN if use_media else INTENT_RAG,
            desired_count=count,
            standalone_query=topic,
            confidence="medium",
        )

    if intent in (INTENT_RAG, INTENT_MEDIA_GEN) and not standalone and topic:
        standalone = topic
        conf = "medium"

    if intent == INTENT_RAG and not standalone:
        return _decision(
            intent=INTENT_CLARIFY,
            clarify_question="您想在知识库里查什么？请用更具体的关键词说一下。",
            confidence="low",
        )

    return _decision(
        intent=intent,
        desired_count=count,
        standalone_query=standalone,
        clarify_question=decision.get("clarify_question") or "",
        confidence=conf,
    )


async def classify_route(
    user_text: str,
    dialog_history: str = "",
    *,
    forced_intent: str | None = None,
    product_hint: str = "",
    thread_id: str | None = None,
    image_count: int = 0,
) -> RouteDecision:
    if not user_text and image_count <= 0:
        return _decision(intent=DEFAULT_INTENT, confidence="high")

    if image_count > 0 and not (forced_intent or product_hint):
        logger.info(
            "Route short-circuit: %d image(s) → chat <- %r",
            image_count,
            (user_text or "")[:80],
        )
        return _decision(intent=INTENT_CHAT, confidence="high")

    forced = (forced_intent or "").strip().lower()
    if forced and forced not in INTENT_SET:
        forced = ""
    hint = (product_hint or "").strip().lower()

    parts: list[str] = []
    if dialog_history:
        parts.append(f"【近期对话】\n{dialog_history}")
    latest = user_text or "（用户未输入文字）"
    if image_count > 0:
        latest = f"{latest}\n（用户本轮附带了 {image_count} 张图片）"
    parts.append(f"【用户最新一句话】\n{latest}")

    if forced == INTENT_RAG:
        parts.append(
            "【约束】用户偏向查知识库：优先 rag + standalone_query；"
            "省略句继承最近主题；图文类用 media_gen；仅无法定主题时 clarify。"
        )
    elif forced == INTENT_CHAT:
        parts.append("【约束】用户偏向普通对话：intent=chat。")
    elif forced == INTENT_MEDIA_GEN:
        parts.append(
            "【约束】用户偏向多媒体/图文：intent=media_gen；"
            "主题可从上文写入 standalone_query（不要写成配图检索）。"
        )

    product = PRODUCT_HINTS.get(hint)
    if product and product.router_extra:
        parts.append(product.router_extra)

    if image_count > 0 and not forced:
        parts.append(
            f"【约束】本轮附带 {image_count} 张图；问图/识图 → chat。"
        )
    human = "\n\n".join(parts)

    try:
        llm = build_llm_fast(max_tokens=320)
        response = await llm.ainvoke(
            [
                SystemMessage(content=_INTENT_SYSTEM_PROMPT),
                HumanMessage(content=human),
            ]
        )
        record_llm_usage(
            thread_id,
            "intent_router",
            response,
            prompt_text=_INTENT_SYSTEM_PROMPT + "\n" + human,
            completion_text=extract_text(response.content),
        )
        raw = extract_text(response.content)
        data = _parse_json_object(raw)
        if not data:
            intent = _normalize_intent(raw)
            if forced:
                intent = forced
            decision = _slots_from_text(user_text, intent=intent)
        else:
            decision = normalize_route_decision(data, user_text=user_text)
    except Exception:
        logger.exception("Route classification failed, fallback")
        topic = _last_user_topic_from_dialog(dialog_history)
        uttered = _product_from_utterance(user_text)
        fb = forced or (
            PRODUCT_HINTS[uttered].route_intent
            if uttered in PRODUCT_HINTS
            else None
        ) or (
            INTENT_MEDIA_GEN if hint and PRODUCT_HINTS.get(hint, None)
            and PRODUCT_HINTS[hint].route_intent == INTENT_MEDIA_GEN
            else None
        ) or DEFAULT_INTENT
        if fb == INTENT_RAG and not topic:
            decision = _decision(
                intent=INTENT_CLARIFY,
                clarify_question="刚才没理解清楚，您具体想在知识库里查什么？",
                confidence="low",
            )
        else:
            decision = _decision(
                intent=fb if fb in INTENT_SET else DEFAULT_INTENT,
                desired_count=extract_desired_count(user_text),
                standalone_query=topic,
                confidence="low",
            )

    decision = _finalize_decision(
        decision,
        user_text=user_text,
        dialog_history=dialog_history,
        forced=forced or None,
        product_hint=hint,
    )

    if image_count > 0 and decision["intent"] == INTENT_CLARIFY and forced != INTENT_RAG:
        decision = _decision(intent=INTENT_CHAT, confidence="high")

    logger.info(
        "Route classified: intent=%s count=%s conf=%s query=%r clarify=%r hint=%s images=%s <- %r",
        decision["intent"],
        decision["desired_count"],
        decision["confidence"],
        (decision["standalone_query"] or "")[:80],
        (decision["clarify_question"] or "")[:60],
        hint or "-",
        image_count,
        user_text[:80],
    )
    return decision


def _state_update_from_decision(
    decision: RouteDecision,
    *,
    product_hint: str = "",
    allow_kb_suggest: bool = False,
) -> dict:
    intent = decision["intent"]
    hint_ids = client_hint_ids()
    out: dict[str, Any] = {
        "intent": intent,
        "client_intent": product_hint if product_hint in hint_ids else "",
        "clarify_question": "",
        "suggested_kb_query": "",
        "suggested_kb_top_k": None,
    }

    if intent == INTENT_CLARIFY:
        out["clarify_question"] = (decision.get("clarify_question") or "").strip()
        out["client_intent"] = ""
        return out

    # 仅前端点亮「知识库」(forced=rag) 时写入检索建议；普通 chat / 自动判 rag 不注入
    if allow_kb_suggest and intent in (INTENT_RAG, INTENT_MEDIA_GEN):
        standalone = (decision.get("standalone_query") or "").strip()
        if standalone:
            out["suggested_kb_query"] = standalone
        count = decision.get("desired_count")
        if count:
            out["suggested_kb_top_k"] = int(count)
    return out


_INTENT_STATUS = {
    INTENT_CHAT: "正在准备…",
    INTENT_RAG: "正在准备…",
    INTENT_MEDIA_GEN: "正在准备…",
    INTENT_CLARIFY: "正在确认…",
}


async def intent_router_node(state: Mapping[str, Any], config: RunnableConfig) -> dict:
    """意图理解：写入通用 intent；产品 hint 经 client_intent 传给 agent。"""
    tid = thread_id_from_config(config)
    await emit_status(tid, "正在分析…")

    # 刷新动态集合（热加载场景极少，但保持与注册表一致）
    global CLIENT_INTENT_SET
    CLIENT_INTENT_SET = frozenset(INTENT_SET | client_hint_ids())

    client = (state.get("client_intent") or "").strip().lower().replace("-", "_")
    user_text = last_user_text(state)
    history = recent_dialog_text(state, max_turns=4)
    image_count = last_user_image_count(state)

    forced, product_hint = _map_client_hint(client if client in CLIENT_INTENT_SET else None)
    uttered = _product_from_utterance(user_text)
    if uttered:
        product_hint = product_hint or uttered
    if forced or product_hint:
        logger.info(
            "Client hint: forced=%s product=%s (still run understanding LLM)",
            forced or "-",
            product_hint or "-",
        )

    decision = await classify_route(
        user_text,
        history,
        forced_intent=forced,
        product_hint=product_hint,
        thread_id=tid,
        image_count=image_count,
    )
    # 未点亮「知识库」时禁止自动走 rag（避免绑搜库 / 烧 token）
    if decision["intent"] == INTENT_RAG and forced != INTENT_RAG:
        logger.info(
            "KB gate: demote rag→chat (client did not force rag) query=%r",
            (decision.get("standalone_query") or "")[:60],
        )
        decision = {
            **decision,
            "intent": INTENT_CHAT,
            "standalone_query": "",
            "desired_count": None,
        }
    status = _INTENT_STATUS.get(decision["intent"], "正在准备…")
    product = PRODUCT_HINTS.get(product_hint)
    if (
        product
        and product.status
        and decision["intent"] != INTENT_CLARIFY
    ):
        status = product.status
    await emit_status(tid, status)
    return {
        **_state_update_from_decision(
            decision,
            product_hint=product_hint,
            allow_kb_suggest=(forced == INTENT_RAG),
        ),
        **fresh_guard_state(),
    }


def route_by_intent(
    state: Mapping[str, Any],
) -> Literal["agent", "clarify"]:
    """条件边：仅 clarify 单独反问；其余进 agent。"""
    intent = state.get("intent") or DEFAULT_INTENT
    if intent == INTENT_CLARIFY:
        return "clarify"
    return "agent"
