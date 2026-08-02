"""小红书批量图文产品模式。"""

from __future__ import annotations

import re

from backend.common.tool_outcome import INTERNAL_HINT_PREFIX
from backend.tools.hints import HintBuildContext, ProductHint

_UTTERANCE = re.compile(
    r"(?:小红书)?.{0,12}(?:图文|配图)|"
    r"(?:生成|做|出).{0,8}(?:图文|配图)|"
    r"(?:每(?:道|条|个)|分别|逐[条个]).{0,16}(?:图|文案)|"
    r"(?:\d+|多|几)(?:道|条|个).{0,40}(?:图文|配图|生图|图片)|"
    r"(?:道|条|个|这些|上面|上文|文案).{0,24}(?:生成|做|出).{0,8}(?:图片|配图|图文)|"
    r"(?:生成|做|出).{0,8}(?:图片|配图|图文).{0,24}(?:道|条|个|这些|上面|文案)|"
    r"(?:图文包|小红书图文|批量图文)",
    re.IGNORECASE,
)


def _build_agent_hint(ctx: HintBuildContext) -> str | None:
    n_hint = ""
    if ctx.suggested_kb_top_k:
        n_hint = f"约 {int(ctx.suggested_kb_top_k)} 条；"
    kb_bit = ""
    if ctx.suggested_kb_query:
        q = re.sub(r"(小红书)?配图|海报素材", "做法", ctx.suggested_kb_query)
        kb_bit = f"主题可参考 {q!r}（优先用上文已有菜谱/文案）；"
    forced_bit = (
        "用户已点击「小红书图文」模式。"
        if ctx.client_hint == "xhs_pack"
        else "用户要批量小红书图文。"
    )
    return (
        f"{INTERNAL_HINT_PREFIX}{forced_bit}"
        f"{n_hint}{kb_bit}"
        "建议调用 make_xhs_pack：items 为列表，每项一道菜/一条知识的文字素材"
        "（优先用上文已有文案或菜谱摘要）。"
        "禁止只用一次 generate_image 画多菜合集图；禁止对每条再单独连打 generate_image；禁止搜知识库里的「配图」。"
    )


PRODUCT_HINT = ProductHint(
    id="xhs_pack",
    route_intent="media_gen",
    priority=30,
    utterance_re=_UTTERANCE,
    skip_on_export_confirm=True,
    router_extra=(
        "【提示】用户可能要批量小红书图文：intent=media_gen，主题可继承上文。"
    ),
    status="正在准备图文…",
    build_agent_hint=_build_agent_hint,
)
