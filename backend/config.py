"""应用配置：环境变量 / .env → pydantic-settings（单一读取入口）。

约定：
- 密钥与环境相关值只来自环境变量或 backend/.env，不硬编码
- 业务代码统一 ``from backend.config import settings``
- ``load_dotenv`` 只负责把 .env 注入 ``os.environ``（给 pymilvus 等直接读环境的库）
- ``Settings`` 负责类型化读取与默认值，不再手写 ``os.getenv`` 填字段
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent / ".env"

# 注入进程环境，供第三方库与 protect_pymilvus_env 使用
load_dotenv(_ENV_FILE)


def protect_pymilvus_env() -> None:
    """
    pymilvus 在 import 时会读取环境变量 MILVUS_URI，且只接受 http(s)/host:port。
    Milvus Lite 本地文件（如 ./milvus_demo.db）会触发 ConnectionConfigException。

    将非 http(s) 的值挪到 APP_MILVUS_URI，并把 MILVUS_URI 置空占位，
    避免本地路径被 pymilvus 误解析。
    """
    uri = (os.environ.get("MILVUS_URI") or "").strip()
    if uri and not uri.startswith(("http://", "https://")):
        os.environ.setdefault("APP_MILVUS_URI", uri)
        os.environ["MILVUS_URI"] = ""


protect_pymilvus_env()


class Settings(BaseSettings):
    """字段名自动映射环境变量（如 llm_api_key ← LLM_API_KEY）。"""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- LLM（主模型）----
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""

    # ---- LLM（轻量：意图 / 标题等；空则回退主模型）----
    llm_fast_base_url: str = ""
    llm_fast_api_key: str = ""
    llm_fast_model: str = ""

    # ---- LangSmith（可观测；需 API Key 且 tracing=true）----
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "langgraph-demo"
    # 空=官方默认；自建/区域部署时再填
    langsmith_endpoint: str = ""
    # 可选工作区 ID（LangSmith org workspace）
    langsmith_workspace_id: str = ""

    # ---- 文生图（/images/generations；SiliconFlow / 火山方舟 / OpenAI 兼容）----
    image_gen_base_url: str = ""
    image_gen_api_key: str = ""
    image_gen_model: str = ""
    # 硅基常用 1024x1024；火山 Seedream 常用 1K / 2K / 3K / 4K
    image_gen_size: str = "1024x1024"
    image_gen_timeout_sec: float = 120.0
    # auto：按 base_url 判断；也可强制 openai / siliconflow / ark
    image_gen_provider: str = "auto"
    image_gen_num_inference_steps: int = 20
    image_gen_guidance_scale: float = 7.5
    # 仅火山方舟等：是否水印、输出格式（png/jpeg）；硅基忽略
    image_gen_watermark: bool = False
    image_gen_output_format: str = "png"

    # ---- 图像编辑（独立凭证；SiliconFlow / 火山方舟）----
    image_edit_base_url: str = ""
    image_edit_api_key: str = ""
    image_edit_model: str = "Qwen/Qwen-Image-Edit-2509"
    # auto：按 base_url 判断；也可强制 openai / siliconflow / ark
    image_edit_provider: str = "auto"
    # 火山 Seedream 常用 1K；硅基忽略
    image_edit_size: str = "1K"
    image_edit_num_inference_steps: int = 20
    image_edit_cfg: float = 4.0
    image_edit_timeout_sec: float = 120.0
    # 仅火山方舟等；硅基忽略
    image_edit_watermark: bool = False
    image_edit_output_format: str = "png"

    # ---- 录音识别（SiliconFlow Qwen3-Omni chat/completions + audio_url）----
    asr_base_url: str = ""
    asr_api_key: str = ""
    asr_model: str = "Qwen/Qwen3-Omni-30B-A3B-Thinking"
    asr_timeout_sec: float = 600.0

    # ---- 声音克隆 TTS（SiliconFlow /audio/speech + references）----
    tts_base_url: str = ""
    tts_api_key: str = ""
    tts_default_model: str = "fnlp/MOSS-TTSD-v0.5"
    tts_timeout_sec: float = 180.0

    # ---- Agent 安全闸（防死循环 / 无效重试）----
    graph_recursion_limit: int = 28  # 整图节点步进上限
    tool_max_total_calls: int = 12  # 单轮用户消息内工具总次数
    tool_max_calls_per_name: int = 3  # 未单独配置的 tool 默认每名上限
    tool_max_consecutive_failures: int = 2  # 连续失败熔断

    # ---- LLM 上下文裁剪（仅投影到主模型；不改 checkpoint / 前端历史）----
    llm_context_trim_enabled: bool = True
    # 保留最近多少轮用户消息（含其后 assistant/tool）；<=0 表示不按轮裁
    llm_context_max_user_turns: int = 12
    # 窗口内非最近一轮的 Tool/长 assistant 正文上限（字符）
    llm_context_tool_max_chars: int = 6000
    # 最近一轮工具结果正文上限（字符）
    llm_context_recent_tool_max_chars: int = 12000

    # ---- 并发（单进程 asyncio；多用户靠限流 + 连接池）----
    # 同时进行的 graph/SSE 轮次上限；超出则排队，超时返回 503
    max_concurrent_runs: int = 32
    max_concurrent_runs_wait_sec: float = 15.0
    # uvicorn 同时接受的连接上限（含闲置 SSE）
    uvicorn_limit_concurrency: int = 200

    # ---- 文档工作区临时 RAG ----
    # 滑动过期：活跃上传/对话会续期；过期后 GC 清 PG + Milvus + OSS
    workspace_ttl_days: int = 10
    # GC 轮询间隔（秒）；0=关闭后台清理
    workspace_gc_interval_sec: int = 3600
    # 每轮最多处理的工作区数
    workspace_gc_batch_size: int = 20

    # ---- 销售分析工作区（Excel 结构化表，无向量）----
    # 空则回退 workspace_ttl_days / workspace_gc_*
    sales_workspace_ttl_days: int = 10
    sales_workspace_gc_interval_sec: int = 3600
    sales_workspace_gc_batch_size: int = 20
    sales_max_file_bytes: int = 20 * 1024 * 1024
    sales_max_sheets: int = 20
    sales_max_rows_per_sheet: int = 50_000
    sales_query_default_limit: int = 200
    sales_query_max_limit: int = 2000

    # ---- PostgreSQL（本地可有安全默认；生产务必用环境变量覆盖）----
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "langgraph_chat"
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_pool_min: int = 2
    # 建议 ≥ max_concurrent_runs，并留余量给标题/CRUD
    postgres_pool_max: int = 40
    # asyncpg 单条 SQL 超时（秒）
    postgres_command_timeout_sec: float = 30.0
    # 从池中获取连接的超时（秒）；耗尽时快速失败，避免请求挂死
    postgres_pool_acquire_timeout_sec: float = 10.0
    # 闲置连接最长存活（秒）；到期回收。0=不回收
    postgres_pool_max_inactive_sec: float = 300.0
    # LangGraph checkpointer 专用 psycopg 池；max=0 表示跟 max_concurrent_runs 对齐
    postgres_checkpoint_pool_min: int = 1
    postgres_checkpoint_pool_max: int = 0
    # checkpointer 池：取连接超时 / 连接最大寿命 / 最大闲置（秒）
    postgres_checkpoint_pool_timeout_sec: float = 10.0
    postgres_checkpoint_pool_max_lifetime_sec: float = 3600.0
    postgres_checkpoint_pool_max_idle_sec: float = 600.0

    # ---- 鉴权（JWT + Refresh Cookie）----
    # 生产务必设置强随机 JWT_SECRET；未设置时使用开发默认（勿用于公网）
    jwt_secret: str = "dev-only-change-me-langgraph-demo"
    jwt_access_expire_minutes: int = 30
    jwt_refresh_expire_days: int = 14
    # 逗号分隔；Cookie 方案需明确 origin，不能用 *
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # 首次启动若库中无管理员，则用以下账号创建（密码为空则跳过）
    auth_admin_username: str = "admin"
    auth_admin_password: str = ""
    auth_admin_email: str = "admin@localhost"
    # Refresh Cookie：跨站（无 Vite 代理）时需 none；同站代理用 lax
    auth_cookie_samesite: str = "lax"
    auth_cookie_secure: bool = False
    auth_cookie_name: str = "refresh_token"

    # ---- 阿里云 OSS ----
    oss_access_key_id: str = ""
    oss_access_key_secret: str = ""
    oss_endpoint: str = ""
    oss_bucket_name: str = ""

    # ---- 阿里云实时语音（NLS SpeechTranscriber）----
    nls_app_key: str = ""
    # 空则回退 OSS_ACCESS_KEY_*
    nls_access_key_id: str = ""
    nls_access_key_secret: str = ""
    nls_gateway_url: str = "wss://nls-gateway-cn-shanghai.aliyuncs.com/ws/v1"

    # ---- Embedding（api=远程 /v1/embeddings；local=本地 BGE-M3）----
    # api | local；未显式设置时：配齐 base+key+model 则默认 api
    embedding_provider: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""
    embedding_base_url: str = ""
    # 输出维度（Qwen3 / Qwen3-VL 支持）；须与 Milvus dense_dim 一致，换模型需重建索引
    embedding_dimensions: int = 1024
    embedding_timeout_sec: float = 120.0
    # 本地回退：LOCAL_EMBEDDING_MODEL / EMBEDDING_DEVICE 仍由 os.getenv 读取

    # ---- Rerank（api=远程 /v1/rerank；local=本地 CrossEncoder）----
    rerank_enabled: bool = True
    # api | local；空则跟 embedding_provider
    rerank_provider: str = ""
    rerank_base_url: str = ""  # 空则回退 embedding_base_url
    rerank_api_key: str = ""  # 空则回退 embedding_api_key
    rerank_model: str = "Qwen/Qwen3-Reranker-8B"
    rerank_device: str = ""  # 仅 local：空则 CUDA 自动检测
    rerank_timeout_sec: float = 120.0
    rerank_instruction: str = ""  # 可选；硅基部分模型支持
    rerank_recall_multiplier: int = 4  # recall_k = max(top_k * multiplier, 20)
    rerank_max_recall: int = 50
    # 可选绝对阈值（空=关闭）；一般不需要，优先用相对过滤
    rerank_score_threshold: str = ""
    # 相对过滤：保留 score >= top1_score - margin（自适应，少手调）
    rerank_relative_filter: bool = True
    rerank_score_margin: float = 0.4

    # ---- Parent expand：L3 命中后按 parent_chunk_id 回查 L2 给 LLM ----
    rag_expand_to_l2: bool = True

    # ---- Milvus ----
    milvus_token: str = ""
    # 本地 Lite 经 protect 后落在 APP_MILVUS_URI
    app_milvus_uri: str = ""

    @property
    def rerank_score_threshold_value(self) -> float | None:
        raw = (self.rerank_score_threshold or "").strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    @model_validator(mode="after")
    def _fallback_fast_llm(self) -> Settings:
        """未单独配置 fast / 生图 / 图编 / rerank 时，回退到主模型或 embedding 凭证。"""
        if not self.llm_fast_base_url:
            self.llm_fast_base_url = self.llm_base_url
        if not self.llm_fast_api_key:
            self.llm_fast_api_key = self.llm_api_key
        if not self.llm_fast_model:
            self.llm_fast_model = self.llm_model
        if not self.image_gen_base_url:
            self.image_gen_base_url = self.llm_base_url
        if not self.image_gen_api_key:
            base = (self.image_gen_base_url or "").lower()
            if "siliconflow" in base and (self.embedding_api_key or "").strip():
                self.image_gen_api_key = self.embedding_api_key
            else:
                self.image_gen_api_key = self.llm_api_key
        if not self.image_edit_base_url:
            self.image_edit_base_url = self.llm_base_url
        if not self.image_edit_api_key:
            base = (self.image_edit_base_url or "").lower()
            if "siliconflow" in base and (self.embedding_api_key or "").strip():
                self.image_edit_api_key = self.embedding_api_key
            else:
                self.image_edit_api_key = self.llm_api_key
        if not self.asr_base_url:
            # 优先跟图像编辑同厂；否则回退主 LLM
            self.asr_base_url = self.image_edit_base_url or self.llm_base_url
        if not self.asr_api_key:
            base = (self.asr_base_url or "").lower()
            if "siliconflow" in base and (self.embedding_api_key or "").strip():
                self.asr_api_key = self.embedding_api_key
            else:
                self.asr_api_key = self.image_edit_api_key or self.llm_api_key
        if not self.tts_base_url:
            self.tts_base_url = self.asr_base_url or self.llm_base_url
        if not self.tts_api_key:
            base = (self.tts_base_url or "").lower()
            if "siliconflow" in base and (self.embedding_api_key or "").strip():
                self.tts_api_key = self.embedding_api_key
            else:
                self.tts_api_key = self.asr_api_key or self.llm_api_key
        if not (self.rerank_base_url or "").strip():
            self.rerank_base_url = self.embedding_base_url
        if not (self.rerank_api_key or "").strip():
            self.rerank_api_key = self.embedding_api_key
        return self

    @staticmethod
    def normalize_openai_v1_base(url: str) -> str:
        """去掉误配的路径后缀，得到 .../v1 根。"""
        u = (url or "").strip().rstrip("/")
        for suffix in (
            "/chat/completions",
            "/embeddings",
            "/rerank",
            "/images/generations",
        ):
            if u.lower().endswith(suffix):
                u = u[: -len(suffix)]
                break
        return u.rstrip("/")

    @property
    def embedding_provider_resolved(self) -> str:
        raw = (self.embedding_provider or "").strip().lower()
        if raw in ("api", "local"):
            return raw
        has_remote = bool(
            (self.embedding_base_url or "").strip()
            and (self.embedding_api_key or "").strip()
            and (self.embedding_model or "").strip()
        )
        return "api" if has_remote else "local"

    @property
    def rerank_provider_resolved(self) -> str:
        raw = (self.rerank_provider or "").strip().lower()
        if raw in ("api", "local"):
            return raw
        return self.embedding_provider_resolved

    @property
    def embedding_api_base(self) -> str:
        return self.normalize_openai_v1_base(self.embedding_base_url)

    @property
    def rerank_api_base(self) -> str:
        return self.normalize_openai_v1_base(
            self.rerank_base_url or self.embedding_base_url
        )

    @property
    def milvus_uri(self) -> str:
        """本地 Lite 用 APP_MILVUS_URI；远程用未清空的 MILVUS_URI。"""
        return (
            self.app_milvus_uri
            or (os.environ.get("MILVUS_URI") or "").strip()
            or "./milvus_demo.db"
        )

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


def apply_langsmith_environ(s: Settings | None = None) -> bool:
    """把 Settings 同步到 os.environ，供 LangChain/LangGraph/langsmith SDK 读取。

    Returns:
        是否已开启 tracing（需 key + tracing=true）。
    """
    cfg = s or settings
    key = (cfg.langsmith_api_key or os.environ.get("LANGCHAIN_API_KEY") or "").strip()
    project = (cfg.langsmith_project or "langgraph-demo").strip() or "langgraph-demo"
    endpoint = (cfg.langsmith_endpoint or "").strip()
    workspace = (cfg.langsmith_workspace_id or "").strip()

    if key:
        os.environ["LANGSMITH_API_KEY"] = key
        os.environ["LANGCHAIN_API_KEY"] = key
    if project:
        os.environ["LANGSMITH_PROJECT"] = project
        os.environ["LANGCHAIN_PROJECT"] = project
    if endpoint:
        os.environ["LANGSMITH_ENDPOINT"] = endpoint
        os.environ["LANGCHAIN_ENDPOINT"] = endpoint
    if workspace:
        os.environ["LANGSMITH_WORKSPACE_ID"] = workspace

    enabled = bool(cfg.langsmith_tracing and key)
    flag = "true" if enabled else "false"
    os.environ["LANGSMITH_TRACING"] = flag
    os.environ["LANGCHAIN_TRACING_V2"] = flag
    return enabled


settings = Settings()
apply_langsmith_environ(settings)
