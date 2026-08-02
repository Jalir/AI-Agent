"""FastAPI 请求 / 响应 Pydantic 模型。"""

from pydantic import BaseModel, Field


class ApproveRequest(BaseModel):
    thread_id: str
    approved: bool
    reason: str = ""
    # 审批时用户编辑后的 tool 参数（如 send_email 的 to/subject/body）
    edited_args: dict | None = None
    workspace_id: int | None = Field(
        default=None,
        description="文档工作区审批续跑时传入，避免掉回共享知识库",
    )
    sales_workspace_id: int | None = Field(
        default=None,
        description="销售分析区审批续跑时传入",
    )


class ConversationItem(BaseModel):
    id: int
    thread_id: str
    title: str
    created_at: str
    updated_at: str


class MessageItem(BaseModel):
    role: str
    content: str
    created_at: str
    attachments: list = Field(default_factory=list)
    token_total: int = 0


class UploadSignatureRequest(BaseModel):
    file_name: str
    file_type: str


class SaveFileRequest(BaseModel):
    file_name: str
    file_url: str
    file_size: int
    file_type: str
    object_key: str


class ChatAttachment(BaseModel):
    url: str
    object_key: str = ""
    mime_type: str = ""
    name: str = ""


class ChatRequest(BaseModel):
    thread_id: str
    message: str = ""
    intent: str | None = Field(
        default=None,
        description=(
            "可选意图软提示：chat|rag|media_gen|xhs_pack|image_edit|speech_recognize"
        ),
    )
    attachments: list[ChatAttachment] = Field(default_factory=list)
    workspace_id: int | None = Field(
        default=None,
        description="文档工作区 ID：启用时检索走临时 RAG，与共享知识库隔离",
    )
    sales_workspace_id: int | None = Field(
        default=None,
        description="销售分析区 ID：启用时走结构化表查询/出图，与文档 RAG 隔离",
    )
