"""主对话 agent 节点。"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Mapping

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from backend.common.llm import build_llm
from backend.common.messages import (
    extract_text,
    last_user_audio_urls,
    last_user_image_urls,
    last_user_text,
    sanitize_messages_for_llm,
    trim_messages_for_llm,
)
from backend.config import settings
from backend.common.stream import emit_status, get_token_queue, is_cancel_requested
from backend.common.usage import record_llm_usage
from backend.db.rbac_store import get_permissions_for_role
from backend.common.tool_outcome import AGENT_ACTION_RULES, INTERNAL_HINT_PREFIX
from backend.graph.guard import BLOCKED_HINT
from backend.tools import TOOLS, filter_tools_for_permissions
from backend.tools.hints import HintBuildContext, match_product_hint

logger = logging.getLogger(__name__)


def _recent_tool_names(state: Mapping[str, Any]) -> set[str]:
    """若上一跳刚跑完 tools，取出对应 tool 名（用于状态文案）。"""
    names: set[str] = set()
    for msg in reversed(state.get("messages") or []):
        if isinstance(msg, ToolMessage) or getattr(msg, "type", "") == "tool":
            n = getattr(msg, "name", None)
            if n:
                names.add(str(n))
            continue
        if isinstance(msg, AIMessage) or getattr(msg, "type", "") == "ai":
            for tc in getattr(msg, "tool_calls", None) or []:
                if isinstance(tc, dict) and tc.get("name"):
                    names.add(str(tc["name"]))
                else:
                    n = getattr(tc, "name", None)
                    if n:
                        names.add(str(n))
            break
        break
    return names


# 短确认句（「好/要/导出」等）； alone 不够，须结合上一轮助手是否在问导出
_SHORT_CONFIRM_RE = re.compile(
    r"^\s*(?:请)?(?:帮我)?"
    r"(?:整理|导出|下载|保存|要|好|行|可以|确认|生成(?:成)?(?:文档|word|docx|excel|xlsx|表格)?)"
    r"(?:一下|吧|啊|呀|文档|成文档|成\s*word|成\s*docx|成\s*excel|成表格)?"
    r"[!！。.?？~～\s]*$",
    re.IGNORECASE,
)

# 上一轮助手是否在提议/询问导出（Word / Excel / 下载等）
_EXPORT_OFFER_RE = re.compile(
    r"(?:导出|下载|保存).{0,24}(?:文档|文件|word|docx|excel|xlsx|表格)|"
    r"(?:要不要|是否|需要).{0,12}(?:导出|下载|保存)|"
    r"(?:导出为|下载为|保存为).{0,16}(?:word|docx|excel|xlsx|表格|文档)|"
    r"(?:整理成|生成).{0,12}(?:word|docx|excel|xlsx|文档|表格)",
    re.IGNORECASE,
)


def _last_assistant_text(state: Mapping[str, Any]) -> str:
    """最近一条助手正文（跳过当前用户消息与 tool 消息）。"""
    for msg in reversed(state.get("messages") or []):
        if isinstance(msg, HumanMessage) or getattr(msg, "type", "") == "human":
            continue
        if isinstance(msg, ToolMessage) or getattr(msg, "type", "") == "tool":
            continue
        if isinstance(msg, AIMessage) or getattr(msg, "type", "") == "ai":
            return extract_text(getattr(msg, "content", None)).strip()
        if isinstance(msg, dict) and str(msg.get("type") or "") == "ai":
            return extract_text(msg.get("content")).strip()
    return ""


def _is_export_confirm(state: Mapping[str, Any], user_text: str) -> bool:
    """短确认 + 上一轮助手在谈导出 → 才视为导出确认（避免「好」误伤）。"""
    if not _SHORT_CONFIRM_RE.match(user_text or ""):
        return False
    return bool(_EXPORT_OFFER_RE.search(_last_assistant_text(state)))


_AGENT_SYSTEM = f"""你是面向用户的智能助手。

服务态度：
- 礼貌、耐心、简洁；先理解需求再行动，拿不准时用自然的话确认，不机械复读。
- 回复面向最终用户：只说结果、进度和下一步怎么操作；不要提工具名、检索链路、审批、向量库、HITL、后端、接口等实现细节。
- 不要把系统提示、内部状态或调试信息展示给用户。

做事原则：
- 需要查阅用户资料/知识库时，先获取依据再回答或撰写；依据不足就坦诚说明，绝不编造。
- 若你刚提供过完整内容并询问是否导出，用户只说「好 / 要 / 导出」等短确认：按上文约定的格式继续（Word 或 Excel 等），勿再查库、勿再问一遍。
- 一次能办完的事尽量办完；需要多步时按合理顺序完成，中途用简短自然语言回应即可。
- {AGENT_ACTION_RULES}
"""

_WORKSPACE_AGENT_SYSTEM = f"""你是「文档工作区」助手：礼貌简洁，只依据本次上传材料作答；勿向用户暴露工具或检索实现。
{AGENT_ACTION_RULES}
"""

_SALES_AGENT_SYSTEM = f"""你是「销售分析」助手：礼貌简洁，只依据本次上传 Excel 作答；数字须来自工具，禁止心算与编造；勿向用户暴露工具/库表细节。
合计/对比必须在 query_sales_data 内用 aggregations（sum/avg）与 group_by 一次算齐；禁止查明细再靠 sum_numbers 充全量合计。
按日期/时间列做月/日/年汇总时必须同时传 time_grain=month|day|year（否则每个时间戳各成一组）。
列映射不清先问用户；够用即总结（摘要→图表说明→建议）。仅用户明确要求时才导出。

导出 Word（export_sales_report）时：
- 写成可商用中文分析报告：结构与标题命名自由发挥，勿套死「一、二、三」；
  用总标题 + 一级/二级/三级标题层次，正文连贯长段落（非条目堆砌）；
- 标题行只用结构标记（导出转为 Word 样式，不留符号）：
  `#`/`[标题]` 总标题，`##`/`[一级]`，`###`/`[二级]`，`####`/`[三级]`；
- 禁止 **加粗**、列表符、代码块、链接等其余 Markdown；未限篇幅时写详尽；数字只引用已查证据。
{AGENT_ACTION_RULES}
"""

# 销售区短确认（列映射/过滤确认后继续），与导出确认分开
_SALES_AFFIRM_RE = re.compile(
    r"^\s*(?:对的?|是的?|没错|嗯+|好的?|可以|行|确认|没问题)"
    r"[!！。.?？~～\s]*$",
    re.IGNORECASE,
)

# 用户要生成/导出正式报告（Word 长文，非聊天摘要）
_SALES_REPORT_RE = re.compile(
    r"(?:可行性|分析)?报告|导出|下载|(?:生成|整理|保存).{0,8}(?:报告|文档|word|docx)",
    re.IGNORECASE,
)

_SALES_REPORT_WRITE_HINT = (
    f"{INTERNAL_HINT_PREFIX}用户要正式 Word 报告：调用 export_sales_report；"
    "写成可商用分析报告（结构自由，勿套死「一、二、三」）："
    "须有总标题与一/二/三级标题，正文连贯长段落；"
    "标题行用 #/##/###/#### 或 [标题]/[一级]/[二级]/[三级]（导出转 Word 样式）；"
    "禁止 **、列表符、代码块等其余 Markdown；未限篇幅则写详尽；"
    "数字只引用已有查询证据；filename 用业务名。"
)


def _is_sales_affirm(user_text: str) -> bool:
    return bool(_SALES_AFFIRM_RE.match(user_text or ""))

# 文档工作区仅开放检索 + 导出，避免与主对话工具面混淆
_WORKSPACE_TOOL_NAMES = frozenset(
    {"search_knowledge_base", "export_docx", "export_excel"}
)

# 销售分析：结构化查询 + 出图 + 报告/表格导出 + 精确算术
_SALES_TOOL_NAMES = frozenset(
    {
        "list_sales_tables",
        "query_sales_data",
        "make_sales_chart",
        "export_sales_report",
        "export_excel",
        "sum_numbers",
        "average_numbers",
    }
)

# 主对话 intent=chat：不含搜库（需前端点亮「知识库」→ rag）
_CHAT_TOOL_NAMES = frozenset(
    {
        "generate_image",
        "image_edit",
        "speech_recognize",
        "export_docx",
        "export_excel",
        "send_email",
        "resolve_recipient",
    }
)

# rag：chat 能力 + 知识库；勿绑销售等重 schema
_RAG_TOOL_NAMES = _CHAT_TOOL_NAMES | {"search_knowledge_base"}

# media_gen：可查库取文案 + 小红书/生图；仍不绑销售工具
_MEDIA_GEN_TOOL_NAMES = _RAG_TOOL_NAMES | {
    "make_xhs_pack",
    "write_xhs_copy",
    "generate_image",
}

# 产品 hint id → 需额外放开的工具（防 intent 与按钮不一致）
_PRODUCT_EXTRA_TOOLS: dict[str, frozenset[str]] = {
    "xhs_pack": frozenset({"make_xhs_pack", "write_xhs_copy", "generate_image"}),
    "image_edit": frozenset({"image_edit"}),
    "speech_recognize": frozenset({"speech_recognize"}),
}


async def agent_node(state: Mapping[str, Any], config: RunnableConfig) -> dict:
    """LLM 节点：流式调用模型，token 写入 SSE 队列；熔断时不绑定 tools。"""
    conf = (config or {}).get("configurable") or {}
    thread_id = conf.get("thread_id")
    queue = get_token_queue(thread_id)
    tools_blocked = bool(state.get("tools_blocked"))
    workspace_mode = conf.get("workspace_id") is not None
    sales_mode = conf.get("sales_workspace_id") is not None
    if sales_mode:
        system_prompt = _SALES_AGENT_SYSTEM
    elif workspace_mode:
        system_prompt = _WORKSPACE_AGENT_SYSTEM
    else:
        system_prompt = _AGENT_SYSTEM

    messages = list(state["messages"])
    # 每轮以系统提示为唯一 SystemMessage（覆盖历史/种子中的首条）
    if messages and isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=system_prompt)] + list(messages[1:])
    else:
        messages = [SystemMessage(content=system_prompt)] + messages

    suggested = (state.get("suggested_kb_query") or "").strip()
    suggested_k = state.get("suggested_kb_top_k")
    intent = (state.get("intent") or "").strip()
    client_hint = (state.get("client_intent") or "").strip().lower()
    user_text = last_user_text(state)
    # 仅「短确认 + 上文在谈导出」才算导出确认；单独一个「好」不算
    export_confirm = _is_export_confirm(state, user_text or "")
    audio_urls = last_user_audio_urls(state)
    image_urls = last_user_image_urls(state)
    # 上一跳是否刚跑完工具（用于避免销售/工作区提示每轮重复驱使调工具）
    recent_tools = _recent_tool_names(state)

    # 工作区/销售模式：不走生图/语音等产品 hint
    product = None
    if not workspace_mode and not sales_mode:
        product = match_product_hint(
            client_hint=client_hint,
            user_text=user_text or "",
            audio_urls=audio_urls,
            image_urls=image_urls,
            export_confirm=export_confirm,
        )
        # client 按钮命中但附件未齐时，仍交给 hint.build（可补问用户）
        if product is None and client_hint:
            from backend.tools import PRODUCT_HINTS

            product = PRODUCT_HINTS.get(client_hint)

    drop_all_media = bool(product and product.needs_audio)

    if tools_blocked:
        messages = messages + [HumanMessage(content=BLOCKED_HINT)]
        logger.info("agent tools blocked: force text-only reply")
    elif product and product.build_agent_hint:
        hint_ctx = HintBuildContext(
            user_text=user_text or "",
            client_hint=client_hint,
            audio_urls=list(audio_urls or []),
            image_urls=list(image_urls or []),
            suggested_kb_query=suggested,
            suggested_kb_top_k=int(suggested_k) if suggested_k else None,
            export_confirm=export_confirm,
        )
        # 无附件时：client 按钮仍注入补问；话语匹配已在 match 时要求附件
        text = product.build_agent_hint(hint_ctx)
        if text:
            messages = messages + [HumanMessage(content=text)]
            logger.info("agent product hint id=%s: %s", product.id, text[:180])
    elif sales_mode and not export_confirm and (user_text or "").strip():
        last_q = state.get("sales_last_query")
        has_query_cache = isinstance(last_q, dict) and bool(last_q.get("rows"))
        want_report = bool(_SALES_REPORT_RE.search(user_text or ""))
        if recent_tools:
            just = ", ".join(sorted(recent_tools))
            if "export_sales_report" in recent_tools:
                hint = (
                    f"{INTERNAL_HINT_PREFIX}报告已导出，勿再 export；"
                    "用一两句告知用户可点击下载卡片，勿粘贴全文或 Markdown。"
                    f" 刚执行：{just}。"
                )
            elif want_report and "query_sales_data" in recent_tools:
                hint = (
                    f"{INTERNAL_HINT_PREFIX}已有查询结果，禁止再 query/list；"
                    "可选用 make_sales_chart(use_last_query=true) 出图，"
                    "然后必须调用 export_sales_report。"
                    f" {_SALES_REPORT_WRITE_HINT.removeprefix(INTERNAL_HINT_PREFIX)}"
                    f" 刚执行：{just}。"
                )
            elif "query_sales_data" in recent_tools:
                hint = (
                    f"{INTERNAL_HINT_PREFIX}已有查询结果，禁止再 query/list；"
                    "若已是 aggregate：直接 make_sales_chart(use_last_query=true) 并写摘要；"
                    "长商品名/类目用 chart_type=hbar；"
                    "sum_numbers 仅可对少量汇总行做二次计算，勿对明细预览列充全量合计。"
                    f" 刚执行：{just}。"
                )
            elif want_report and (
                "make_sales_chart" in recent_tools or has_query_cache
            ):
                hint = (
                    f"{INTERNAL_HINT_PREFIX}数据已就绪，禁止再 query/list；"
                    "立即调用 export_sales_report。"
                    f" {_SALES_REPORT_WRITE_HINT.removeprefix(INTERNAL_HINT_PREFIX)}"
                    f" 刚执行：{just}。"
                )
            elif want_report:
                hint = (
                    f"{INTERNAL_HINT_PREFIX}已有工具结果；映射不清则停工具问用户，"
                    "否则一次 query（务必 aggregations/group_by；按日期列汇总加 time_grain）后，"
                    "再 export_sales_report 写可商用 Word 报告，勿多次 query。"
                    f" {_SALES_REPORT_WRITE_HINT.removeprefix(INTERNAL_HINT_PREFIX)}"
                    f" 刚执行：{just}。"
                )
            else:
                hint = (
                    f"{INTERNAL_HINT_PREFIX}已有工具结果；映射不清则停工具问用户，"
                    "否则一次 query（务必 aggregations/group_by；按日期列汇总加 time_grain）后出图，"
                    "勿多次 query。"
                    f" 刚执行：{just}。"
                )
        elif _is_sales_affirm(user_text or ""):
            hint = (
                f"{INTERNAL_HINT_PREFIX}用户已确认映射：勿再 list；"
                "只 query 一次且必须用 aggregations_json（sum/avg）+ 按需 group_by；"
                "按日期列汇总须带 time_grain=month|day|year；"
                "再 use_last_query 出图；勿查明细后靠 sum_numbers 充总额。"
            )
        elif want_report and has_query_cache:
            hint = (
                f"{_SALES_REPORT_WRITE_HINT}"
                " 已有查询缓存，勿再 list/query，直接撰写并导出。"
            )
        elif want_report:
            hint = (
                f"{INTERNAL_HINT_PREFIX}用户要正式报告：先 list 一次核对列名；"
                "映射齐后只 query 一次（aggregations/group_by；日期列加 time_grain），"
                "再 export_sales_report。"
                f" {_SALES_REPORT_WRITE_HINT.removeprefix(INTERNAL_HINT_PREFIX)}"
            )
        elif has_query_cache:
            hint = (
                f"{INTERNAL_HINT_PREFIX}会话中已有查询缓存。"
                "仅改图表类型/标题时可直接 make_sales_chart(use_last_query=true)；"
                "过滤条件变了才重新 query 一次；勿重复 list。"
            )
        else:
            hint = (
                f"{INTERNAL_HINT_PREFIX}先 list 一次核对列名；映射齐后只 query 一次，"
                "分析类问题务必带 aggregations/group_by；"
                "按交易时间等日期列汇总须加 time_grain（月=month/日=day/年=year）；"
                "再 use_last_query 出图；不清先问，未要求勿 export。"
            )
        messages = messages + [HumanMessage(content=hint)]
        logger.info("agent sales hint: %s", hint[:160])
    elif workspace_mode and not export_confirm and (user_text or "").strip():
        if recent_tools:
            hint = f"{INTERNAL_HINT_PREFIX}已有检索结果，直接作答；不足再检索一次。"
        else:
            hint = f"{INTERNAL_HINT_PREFIX}先检索本次上传材料再作答。"
            if suggested:
                hint += f" 建议检索词：{suggested!r}"
        messages = messages + [HumanMessage(content=hint)]
        logger.info("agent workspace hint: %s", hint[:160])
    elif suggested and intent in ("rag", "media_gen") and not export_confirm:
        hint = (
            f"{INTERNAL_HINT_PREFIX}建议查阅知识库："
            f"query={suggested!r}"
        )
        if suggested_k:
            hint += f"，条数约 {int(suggested_k)}"
        if intent == "media_gen":
            hint += (
                "；若是多条小红书图文，查库只取文字素材后调用 make_xhs_pack；"
                "勿搜配图，勿用一张合集图代替。"
            )
        messages = messages + [HumanMessage(content=hint)]
        logger.info("agent hint for tool: %s", hint[:160])
    elif export_confirm:
        if sales_mode:
            messages = messages + [HumanMessage(content=_SALES_REPORT_WRITE_HINT)]
            logger.info("agent sales export confirm: inject Word report write hint")
        else:
            logger.info(
                "agent export confirm (skip kb hint, rely on history): user=%r",
                (user_text or "")[:40],
            )

    llm_messages = sanitize_messages_for_llm(
        messages,
        drop_all_media=drop_all_media,
        keep_last_user_images=not drop_all_media,
    )
    if settings.llm_context_trim_enabled:
        before_n = len(llm_messages)
        llm_messages = trim_messages_for_llm(
            llm_messages,
            max_user_turns=int(settings.llm_context_max_user_turns or 0),
            tool_max_chars=int(settings.llm_context_tool_max_chars or 0),
            recent_tool_max_chars=int(settings.llm_context_recent_tool_max_chars or 0),
        )
        after_n = len(llm_messages)
        if after_n != before_n:
            logger.info(
                "llm context trimmed: %d → %d messages (max_user_turns=%s)",
                before_n,
                after_n,
                settings.llm_context_max_user_turns,
            )
    if tools_blocked:
        llm = build_llm()
    else:
        role = str(conf.get("user_role") or "user").strip().lower() or "user"
        try:
            perms = await get_permissions_for_role(role)
        except Exception:
            logger.exception("load permissions for bind_tools failed role=%s", role)
            perms = frozenset()
        allowed_tools = filter_tools_for_permissions(perms, tools=TOOLS)
        if sales_mode:
            allowed_tools = [
                t for t in allowed_tools if getattr(t, "name", None) in _SALES_TOOL_NAMES
            ]
        elif workspace_mode:
            allowed_tools = [
                t for t in allowed_tools if getattr(t, "name", None) in _WORKSPACE_TOOL_NAMES
            ]
        else:
            # 主对话按 intent 收窄；未知 intent 按 chat，避免误绑全量工具
            if intent == "rag":
                name_set = set(_RAG_TOOL_NAMES)
            elif intent == "media_gen":
                name_set = set(_MEDIA_GEN_TOOL_NAMES)
            else:
                name_set = set(_CHAT_TOOL_NAMES)
            if product is not None:
                name_set |= _PRODUCT_EXTRA_TOOLS.get(product.id, frozenset())
            allowed_tools = [
                t for t in allowed_tools if getattr(t, "name", None) in name_set
            ]
        if len(allowed_tools) != len(TOOLS):
            logger.info(
                "bind_tools filtered role=%s intent=%s workspace=%s sales=%s %d→%d tools=%s",
                role,
                intent or "-",
                workspace_mode,
                sales_mode,
                len(TOOLS),
                len(allowed_tools),
                [t.name for t in allowed_tools],
            )
        llm = (
            build_llm().bind_tools(allowed_tools)
            if allowed_tools
            else build_llm()
        )

    if product and product.id == "speech_recognize" and "speech_recognize" not in recent_tools:
        await emit_status(thread_id, product.agent_status or "正在识别…")
    elif "speech_recognize" in recent_tools:
        await emit_status(thread_id, "正在整理…")
    elif product and product.agent_status:
        await emit_status(thread_id, product.agent_status)
    else:
        await emit_status(thread_id, "正在回复…")

    full = None
    prompt_chars = ""
    try:
        parts = []
        for m in llm_messages:
            parts.append(extract_text(getattr(m, "content", None)))
        prompt_chars = "\n".join(p for p in parts if p)
    except Exception:
        prompt_chars = ""

    try:
        async for chunk in llm.astream(llm_messages):
            if is_cancel_requested(thread_id):
                raise asyncio.CancelledError()
            text = extract_text(getattr(chunk, "content", None))
            if text and queue is not None:
                await queue.put(text)
            full = chunk if full is None else full + chunk
    except asyncio.CancelledError:
        logger.info("Agent LLM stream cancelled thread=%s", thread_id)
        raise

    if full is None:
        full = AIMessage(content="")
    else:
        record_llm_usage(
            thread_id,
            "agent",
            full,
            prompt_text=prompt_chars,
            completion_text=extract_text(getattr(full, "content", None)),
        )
    return {
        "messages": [full],
        "suggested_kb_query": "",
        "suggested_kb_top_k": None,
        "client_intent": "",
        "pending_audio_urls": [],
    }
