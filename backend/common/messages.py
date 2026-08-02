"""消息内容提取与对话历史截取。"""

from __future__ import annotations

from typing import Any, Mapping

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from backend.common.oss import resolve_attachment_url
from backend.common.tool_outcome import INTERNAL_HINT_MARK, INTERNAL_HINT_PREFIX

_TRUNCATE_SUFFIX = "\n…[已截断，原文更长]"


def _message_kind(msg: Any) -> str:
    """归一消息角色：system / human / ai / tool / other。"""
    if isinstance(msg, SystemMessage) or getattr(msg, "type", "") == "system":
        return "system"
    if isinstance(msg, HumanMessage) or getattr(msg, "type", "") == "human":
        return "human"
    if isinstance(msg, ToolMessage) or getattr(msg, "type", "") == "tool":
        return "tool"
    if isinstance(msg, AIMessage) or getattr(msg, "type", "") == "ai":
        return "ai"
    if isinstance(msg, dict):
        role = str(msg.get("role") or msg.get("type") or "").strip().lower()
        if role in ("system", "human", "user", "ai", "assistant", "tool"):
            if role == "user":
                return "human"
            if role == "assistant":
                return "ai"
            return role
    return "other"


def _copy_message_content(msg: Any, new_content: Any) -> Any:
    try:
        return msg.model_copy(update={"content": new_content})
    except Exception:
        try:
            return type(msg)(content=new_content)
        except Exception:
            return msg


def _truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    keep = max(0, max_chars - len(_TRUNCATE_SUFFIX))
    return text[:keep] + _TRUNCATE_SUFFIX


def _truncate_message_body(msg: Any, max_chars: int) -> Any:
    """按字符上限截断消息正文（str 或 content blocks→文本）。"""
    if max_chars <= 0:
        return msg
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        new = _truncate_text(content, max_chars)
        return msg if new == content else _copy_message_content(msg, new)
    if isinstance(content, list):
        text = extract_text(content)
        if len(text) <= max_chars:
            return msg
        # 工具结果多为纯文本块；超长时压成截断字符串，避免把巨型 JSON 块继续塞进上下文
        return _copy_message_content(msg, _truncate_text(text, max_chars))
    return msg


def _is_internal_hint_human(msg: Any) -> bool:
    """agent 追加的内部提示不算用户轮次，也不应把「最近一轮」起点挪到提示上。"""
    if _message_kind(msg) != "human":
        return False
    text = extract_text(getattr(msg, "content", None)).lstrip()
    return text.startswith(INTERNAL_HINT_PREFIX) or text.startswith(INTERNAL_HINT_MARK)


def trim_messages_for_llm(
    messages: list,
    *,
    max_user_turns: int = 12,
    tool_max_chars: int = 6000,
    recent_tool_max_chars: int = 12000,
) -> list:
    """喂主 LLM 前的上下文投影：按用户轮滑动窗口 + 大工具/长回复截断。

    - 保留首条 SystemMessage
    - 按真实用户 HumanMessage 划分轮次，只留最近 max_user_turns 轮（含其后 ai/tool）
    - 内部 hint Human 不计入轮次，始终跟着最近用户轮保留
    - 在用户 Human 边界裁切，天然保证 tool_calls 与 ToolMessage 成对
    - 最近一轮（最后一个真实用户 Human 起）工具结果用更高字符上限
    """
    if not messages:
        return messages

    system_prefix: list = []
    body = list(messages)
    if _message_kind(body[0]) == "system":
        system_prefix = [body[0]]
        body = body[1:]
        # 多余的历史 system 不进窗口（agent 每轮已用当前系统提示覆盖首条）
        while body and _message_kind(body[0]) == "system":
            body = body[1:]

    if not body:
        return system_prefix

    # 按真实用户消息切轮（忽略内部 hint）
    turn_starts: list[int] = [
        i
        for i, m in enumerate(body)
        if _message_kind(m) == "human" and not _is_internal_hint_human(m)
    ]
    if max_user_turns > 0 and len(turn_starts) > max_user_turns:
        keep_from = turn_starts[-max_user_turns]
        body = body[keep_from:]

    # 最近一轮起点：窗口内最后一条真实用户 Human
    last_human = 0
    for i, m in enumerate(body):
        if _message_kind(m) == "human" and not _is_internal_hint_human(m):
            last_human = i

    out: list = list(system_prefix)
    for i, msg in enumerate(body):
        kind = _message_kind(msg)
        in_recent = i >= last_human
        if kind == "tool":
            cap = recent_tool_max_chars if in_recent else tool_max_chars
            out.append(_truncate_message_body(msg, cap))
        elif kind == "ai":
            # 旧轮长回复也截；最近一轮 assistant 正文通常不长，仍套 recent 上限防爆
            cap = recent_tool_max_chars if in_recent else tool_max_chars
            out.append(_truncate_message_body(msg, cap))
        else:
            out.append(msg)
    return out


def extract_text(content: Any) -> str:
    """兼容 str / content blocks 列表。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("content") or ""))
            else:
                parts.append(str(getattr(block, "text", "") or getattr(block, "content", "") or ""))
        return "".join(parts)
    return str(content)


def content_image_count(content: Any) -> int:
    """统计多模态 content 中的图片块数量。"""
    if not isinstance(content, list):
        return 0
    n = 0
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = str(block.get("type") or "").lower()
        if btype in ("image_url", "input_image", "image"):
            n += 1
            continue
        if block.get("image_url") or block.get("image"):
            n += 1
    return n


def last_user_image_count(state: Mapping[str, Any]) -> int:
    """当前轮用户消息附带的图片数。"""
    for msg in reversed(state.get("messages") or []):
        if isinstance(msg, HumanMessage) or getattr(msg, "type", "") == "human":
            return content_image_count(getattr(msg, "content", None))
        if isinstance(msg, dict) and msg.get("role") == "user":
            return content_image_count(msg.get("content"))
    return 0


def content_image_urls(content: Any) -> list[str]:
    """从多模态 content 中提取图片 URL（保序去重）。"""
    if not isinstance(content, list):
        return []
    urls: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        u = ""
        image_url = block.get("image_url")
        if isinstance(image_url, dict):
            u = str(image_url.get("url") or "").strip()
        elif isinstance(image_url, str):
            u = image_url.strip()
        if not u:
            image = block.get("image")
            if isinstance(image, dict):
                u = str(image.get("url") or "").strip()
            elif isinstance(image, str):
                u = image.strip()
        if u and u not in urls:
            urls.append(u)
    return urls


def last_user_image_urls(state: Mapping[str, Any]) -> list[str]:
    """当前轮用户消息附带的图片 URL 列表。"""
    for msg in reversed(state.get("messages") or []):
        if isinstance(msg, HumanMessage) or getattr(msg, "type", "") == "human":
            return content_image_urls(getattr(msg, "content", None))
        if isinstance(msg, dict) and msg.get("role") == "user":
            return content_image_urls(msg.get("content"))
    return []


def normalize_attachments(raw: Any) -> list[dict]:
    """归一化附件列表：仅保留带 url/object_key 的条目（用户上传/LLM 多模态用）。"""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        object_key = str(item.get("object_key") or "").strip()
        if not url and not object_key:
            continue
        out.append(
            {
                "url": url,
                "object_key": object_key,
                "mime_type": str(item.get("mime_type") or "").strip(),
                "name": str(item.get("name") or "").strip(),
            }
        )
    return out


def _is_audio_attachment(att: dict) -> bool:
    mime = str(att.get("mime_type") or "").strip().lower()
    if mime.startswith("audio/") or mime in ("audio/mpeg", "audio/mp3"):
        return True
    name = str(att.get("name") or att.get("url") or "").lower()
    return bool(name.endswith(".mp3") or ".mp3?" in name)


def content_audio_urls(content: Any) -> list[str]:
    """从多模态 content 中提取音频 URL（保序去重）。"""
    if not isinstance(content, list):
        return []
    urls: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        u = ""
        audio_url = block.get("audio_url")
        if isinstance(audio_url, dict):
            u = str(audio_url.get("url") or "").strip()
        elif isinstance(audio_url, str):
            u = audio_url.strip()
        if not u and str(block.get("type") or "").lower() == "audio_url":
            nested = block.get("url")
            if isinstance(nested, str):
                u = nested.strip()
        if u and u not in urls:
            urls.append(u)
    return urls


def last_user_audio_urls(state: Mapping[str, Any]) -> list[str]:
    """当前轮用户消息附带的音频 URL：优先 state.pending_audio_urls，否则从 content 提取。"""
    pending = state.get("pending_audio_urls")
    if isinstance(pending, list):
        urls = [str(u).strip() for u in pending if str(u).strip()]
        if urls:
            return urls
    for msg in reversed(state.get("messages") or []):
        if isinstance(msg, HumanMessage) or getattr(msg, "type", "") == "human":
            return content_audio_urls(getattr(msg, "content", None))
        if isinstance(msg, dict) and msg.get("role") == "user":
            return content_audio_urls(msg.get("content"))
    return []


def build_user_content(
    text: str,
    attachments: list[dict] | None = None,
    *,
    for_llm: bool = False,
) -> str | list[dict]:
    """
    构建用户消息 content。
    - 无附件：返回纯文本 str（兼容旧路径）
    - 有附件：返回 OpenAI/LangChain 多模态 content blocks
    - 图片 → image_url；音频 → audio_url（主 LLM 侧由 agent 剥离，仅供 tool 取 URL）
    - for_llm=True 时对 object_key 签发短期可读 URL，便于模型拉取
    """
    text = (text or "").strip()
    atts = normalize_attachments(attachments)
    if not atts:
        return text

    has_image = any(not _is_audio_attachment(a) for a in atts)
    has_audio = any(_is_audio_attachment(a) for a in atts)
    if not text:
        if has_audio and not has_image:
            text = "请识别这段录音的内容。"
        else:
            text = "请描述这张图片的内容。"

    blocks: list[dict] = [{"type": "text", "text": text}]
    for att in atts:
        url = resolve_attachment_url(att) if for_llm else (att.get("url") or "").strip()
        if for_llm and not url:
            url = (att.get("url") or "").strip()
        if not url:
            continue
        if _is_audio_attachment(att):
            # 写入 content 供 last_user_audio_urls 提取；agent 发给主 LLM 前会剥离
            blocks.append(
                {
                    "type": "audio_url",
                    "audio_url": {"url": url},
                }
            )
        else:
            blocks.append(
                {
                    "type": "image_url",
                    "image_url": {"url": url},
                }
            )
    return blocks if len(blocks) > 1 else text


def strip_audio_blocks_from_messages(messages: list) -> list:
    """主 LLM 不支持 audio_url 时，去掉音频块，保留文本/图片。"""
    return sanitize_messages_for_llm(
        messages, drop_all_media=False, keep_last_user_images=True, drop_audio=True
    )


def sanitize_messages_for_llm(
    messages: list,
    *,
    drop_all_media: bool = False,
    keep_last_user_images: bool = True,
    drop_audio: bool = True,
) -> list:
    """发给主 LLM 前清洗多模态块。

    - 音频块默认全部去掉（主模型通常不支持）
    - 历史轮次的图片签名 URL 易过期导致 403，默认只保留「最后一条用户消息」里的图
    - drop_all_media=True：去掉全部音视频/图（录音识别等场景）
    """
    last_human_idx: int | None = None
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if isinstance(msg, HumanMessage) or getattr(msg, "type", "") == "human":
            last_human_idx = i
            break

    out: list = []
    for i, msg in enumerate(messages):
        content = getattr(msg, "content", None)
        if not isinstance(content, list):
            out.append(msg)
            continue

        keep_images = (
            (not drop_all_media)
            and keep_last_user_images
            and i == last_human_idx
        )
        cleaned: list = []
        changed = False
        for block in content:
            if not isinstance(block, dict):
                cleaned.append(block)
                continue
            btype = str(block.get("type") or "").lower()
            is_audio = btype == "audio_url" or bool(block.get("audio_url"))
            is_image = btype in ("image_url", "input_image", "image") or bool(
                block.get("image_url")
            )
            if is_audio and (drop_audio or drop_all_media):
                changed = True
                continue
            if is_image and (drop_all_media or not keep_images):
                changed = True
                continue
            cleaned.append(block)

        if not changed:
            out.append(msg)
            continue
        if not cleaned:
            text = extract_text(content).strip()
            new_content: str | list = text or ""
        else:
            new_content = cleaned
        try:
            out.append(msg.model_copy(update={"content": new_content}))
        except Exception:
            try:
                out.append(type(msg)(content=new_content))
            except Exception:
                out.append(msg)
    return out


def _is_xhs_card(item: dict) -> bool:
    kind = str(item.get("kind") or "").strip().lower()
    mime = str(item.get("mime_type") or "").strip().lower()
    return kind == "xhs_card" or mime == "application/x-xhs-card"


def normalize_stored_attachments(raw: Any) -> list[dict]:
    """消息回读用：保留小红书卡片全文案字段，并允许无配图的纯文案卡。"""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if _is_xhs_card(item):
            image_url = str(item.get("image_url") or item.get("url") or "").strip()
            object_key = str(item.get("object_key") or "").strip()
            tags = item.get("tags") or []
            if not isinstance(tags, list):
                tags = [str(tags)]
            try:
                index = int(item.get("index") or 0)
            except (TypeError, ValueError):
                index = 0
            if index <= 0:
                name = str(item.get("name") or "")
                # 兼容 name=xhs_3 之类的旧/残缺数据
                if name.startswith("xhs_"):
                    try:
                        index = int(name.split("_", 1)[1])
                    except (TypeError, ValueError, IndexError):
                        index = 0
            out.append(
                {
                    "kind": "xhs_card",
                    "index": index,
                    "title": str(item.get("title") or ""),
                    "body": str(item.get("body") or ""),
                    "tags": [str(t) for t in tags if str(t).strip()],
                    "image_url": image_url,
                    "error": str(item.get("error") or "").strip(),
                    "mime_type": "application/x-xhs-card",
                    "name": str(item.get("name") or (f"xhs_{index}" if index else "")).strip(),
                    "url": image_url,
                    "object_key": object_key,
                }
            )
            continue
        url = str(item.get("url") or "").strip()
        object_key = str(item.get("object_key") or "").strip()
        if not url and not object_key:
            continue
        out.append(
            {
                "url": url,
                "object_key": object_key,
                "mime_type": str(item.get("mime_type") or "").strip(),
                "name": str(item.get("name") or "").strip(),
            }
        )
    return out


def message_role_and_text(msg: Any) -> tuple[str, str] | None:
    """统一取出 human/ai 文本；其它角色忽略。"""
    if isinstance(msg, HumanMessage) or getattr(msg, "type", "") == "human":
        return "user", extract_text(getattr(msg, "content", "")).strip()
    if isinstance(msg, AIMessage) or getattr(msg, "type", "") == "ai":
        return "assistant", extract_text(getattr(msg, "content", "")).strip()
    if isinstance(msg, dict):
        role = str(msg.get("role") or "").lower()
        content = msg.get("content")
        text = extract_text(content).strip()
        if role in ("user", "human"):
            return "user", text
        if role in ("assistant", "ai"):
            return "assistant", text
    return None


def last_user_text(state: Mapping[str, Any]) -> str:
    for msg in reversed(state.get("messages") or []):
        if isinstance(msg, HumanMessage) or getattr(msg, "type", "") == "human":
            return extract_text(getattr(msg, "content", "")).strip()
        if isinstance(msg, dict) and msg.get("role") == "user":
            return extract_text(msg.get("content")).strip()
    return ""


def recent_dialog_text(state: Mapping[str, Any], *, max_turns: int = 4) -> str:
    """
    取当前用户句之前的近期对话（user/assistant）。
    max_turns：最多保留的 user↔assistant 轮数（按 user 计）。
    """
    msgs = list(state.get("messages") or [])
    if msgs:
        last = message_role_and_text(msgs[-1])
        if last and last[0] == "user":
            msgs = msgs[:-1]

    pairs: list[str] = []
    user_count = 0
    for msg in reversed(msgs):
        parsed = message_role_and_text(msg)
        if not parsed:
            continue
        role, text = parsed
        if not text:
            continue
        if role == "assistant" and len(text) > 200:
            text = text[:200] + "…"
        label = "用户" if role == "user" else "助手"
        pairs.append(f"{label}：{text}")
        if role == "user":
            user_count += 1
            if user_count >= max_turns:
                break
    pairs.reverse()
    return "\n".join(pairs)
