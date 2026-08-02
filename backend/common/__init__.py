"""共享基础能力：消息解析、LLM 工厂、SSE token 队列、checkpointer。"""

from backend.common.checkpoint import (
    checkpointer,
    close_checkpointer,
    get_checkpointer,
    init_checkpointer,
    reset_all_threads,
    reset_thread,
)
from backend.common.llm import build_llm, build_llm_fast, generate_title
from backend.common.messages import (
    build_user_content,
    extract_text,
    last_user_image_count,
    last_user_text,
    message_role_and_text,
    normalize_attachments,
    normalize_stored_attachments,
    recent_dialog_text,
)
from backend.common.stream import (
    STREAM_DONE,
    begin_run,
    end_run,
    get_token_queue,
    register_token_queue,
    unregister_token_queue,
)

__all__ = [
    "STREAM_DONE",
    "begin_run",
    "build_llm",
    "build_llm_fast",
    "checkpointer",
    "close_checkpointer",
    "build_user_content",
    "end_run",
    "extract_text",
    "generate_title",
    "get_checkpointer",
    "get_token_queue",
    "init_checkpointer",
    "last_user_image_count",
    "last_user_text",
    "message_role_and_text",
    "normalize_attachments",
    "normalize_stored_attachments",
    "recent_dialog_text",
    "register_token_queue",
    "reset_all_threads",
    "reset_thread",
    "unregister_token_queue",
]
