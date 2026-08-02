"""工具执行上下文：供各技能 execute(ctx) 使用。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig


@dataclass
class ToolExecContext:
    """单次工具调用的运行时上下文。"""

    name: str
    args: dict[str, Any]
    thread_id: str | None = None
    config: RunnableConfig | None = None
    user_id: int | None = None
    role: str = "user"
    # 本轮最近一次 query_sales_data 结果（出图 / 列汇总复用）
    sales_last_query: dict[str, Any] | None = None

    def arg(self, key: str, default: Any = "") -> Any:
        return self.args.get(key, default)
