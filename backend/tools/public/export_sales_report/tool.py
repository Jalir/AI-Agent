"""导出销售分析报告（Word）供下载保存。"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

TOOL_NAME = "export_sales_report"


@tool(TOOL_NAME)
async def export_sales_report(
    content: str,
    filename: str = "销售分析报告.docx",
) -> str:
    """导出销售分析 Word。仅当用户明确要求导出/下载/生成报告，或短确认「好/要/导出」时调用；纯分析出图不要调用。
    导出引擎会把标题标记转为 Word 多级标题，正文自动首行缩进，呈现可商用分析报告版式。

    Args:
        content: 完整报告正文。要求：
            - 写成可商用中文分析报告：章节命名与层级自由发挥，不要固定套「一、二、三」；
              必须有总标题 + 多级标题（一级/二级/三级按内容需要取用），正文为连贯长段落；
            - 标题独占一行，只用结构标记（导出会变成 Word 标题并首行缩进正文，文档不留标记）：
              `# 总标题` 或 `[标题] …`；
              `## 一级标题` 或 `[一级] …`；
              `### 二级标题` 或 `[二级] …`；
              `#### 三级标题` 或 `[三级] …`；
            - 禁止 **加粗**、-/* 列表、1. 列表、```代码块、[]()链接等其余 Markdown；
            - 段落空行分隔；未限篇幅时写详尽；数字仅引用已查证据；勿贴图片 URL。
        filename: 文件名，建议 .docx（如 可行性分析报告.docx）
    """
    _ = (content, filename)
    return "export_sales_report"


def approval_question(tool_args: Any) -> str:
    name = ""
    if isinstance(tool_args, dict):
        name = str(tool_args.get("filename") or "").strip()
    if name:
        return f"即将生成销售分析报告「{name}」并提供下载，是否继续？"
    return "即将生成销售分析报告并提供下载，是否继续？"
