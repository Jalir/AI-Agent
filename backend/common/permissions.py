"""权限码常量（RBAC）。

约定：
- 码值稳定、小写、点分层：resource.action[.scope]
- 业务门禁只认权限码，不认「是不是 admin」字符串（admin 仅作角色容器）
- 新增能力：先在此声明，再写入 rbac 种子 / role_permissions
"""

from __future__ import annotations

# ---- 邮件 ----
EMAIL_SEND = "email.send"
EMAIL_RESOLVE = "email.resolve"
EMAIL_RESOLVE_FUZZY = "email.resolve_fuzzy"

# 权限目录：code -> 说明（种子与文档同源）
PERMISSION_CATALOG: dict[str, str] = {
    EMAIL_SEND: "发送邮件（经 HITL 审批后由服务端投递）",
    EMAIL_RESOLVE: "按用户名/邮箱精确解析收件人",
    EMAIL_RESOLVE_FUZZY: "模糊搜索收件人（前缀匹配）",
}

# 默认角色 → 权限（仅用于首次种子；运行时以 DB role_permissions 为准）
DEFAULT_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset(PERMISSION_CATALOG.keys()),
    # 公司场景：普通用户默认不能发信 / 查通讯录；需要时由管理员改 role_permissions
    "user": frozenset(),
}
