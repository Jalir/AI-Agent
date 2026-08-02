"""resolve_recipient 执行器。"""

from __future__ import annotations

from backend.common.permissions import EMAIL_RESOLVE_FUZZY
from backend.db.rbac_store import role_has_permission
from backend.tools.context import ToolExecContext
from backend.tools.gated.resolve_recipient.tool import run_resolve_recipient


async def execute(ctx: ToolExecContext) -> str:
    query = str(ctx.arg("query") or "").strip()
    allow_fuzzy = await role_has_permission(ctx.role, EMAIL_RESOLVE_FUZZY)
    return await run_resolve_recipient(query, allow_fuzzy=allow_fuzzy)
