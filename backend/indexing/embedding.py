"""文本向量化：远程 OpenAI 兼容 /v1/embeddings，或本地 BGE-M3。"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

# 必须在 import pymilvus 之前：本地 .db 路径不能留在环境变量 MILVUS_URI
try:
    from backend.config import protect_pymilvus_env
    protect_pymilvus_env()
except Exception:
    _uri = (os.environ.get("MILVUS_URI") or "").strip()
    if _uri and not _uri.startswith(("http://", "https://")):
        os.environ.setdefault("APP_MILVUS_URI", _uri)
        os.environ["MILVUS_URI"] = ""

logger = logging.getLogger(__name__)

# SiliconFlow 经典 Embedding 单次 input 上限 32；VL 模型实测常只稳定返回更小批量
_EMBED_BATCH_SIZE = 32
_EMBED_BATCH_SIZE_VL = 8


def _is_vl_embedding_model(model: str) -> bool:
    return "vl-embedding" in (model or "").lower()


def _create_local_dense_embedder() -> Any:
    try:
        import torch
        from pymilvus.model.hybrid import BGEM3EmbeddingFunction
    except ImportError as e:
        raise RuntimeError(
            "本地 Embedding 依赖未安装。请执行: "
            "pip install -r backend/requirements-local-models.txt"
        ) from e

    # 本地 BGE-M3 专用；勿与 API 的 EMBEDDING_MODEL（如 Qwen）混用
    model_name = os.getenv("LOCAL_EMBEDDING_MODEL", "BAAI/bge-m3")
    env_device = os.getenv("EMBEDDING_DEVICE")
    device = env_device if env_device else ("cuda" if torch.cuda.is_available() else "cpu")
    use_fp16 = bool(device.startswith("cuda"))

    logger.info(
        "Creating BGE-M3 embedder: model=%s device=%s fp16=%s",
        model_name,
        device,
        use_fp16,
    )
    return BGEM3EmbeddingFunction(
        model_name=model_name,
        device=device,
        use_fp16=use_fp16,
    )


def _embed_via_api(texts: list[str]) -> list[list[float]]:
    from backend.config import settings

    base = settings.embedding_api_base
    api_key = (settings.embedding_api_key or "").strip()
    model = (settings.embedding_model or "").strip()
    if not base or not api_key or not model:
        raise RuntimeError(
            "Embedding API 未配置完整：需要 EMBEDDING_BASE_URL / "
            "EMBEDDING_API_KEY / EMBEDDING_MODEL"
        )

    url = f"{base}/embeddings"
    timeout = float(settings.embedding_timeout_sec or 120.0)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    dimensions = int(settings.embedding_dimensions or 0)
    is_vl = _is_vl_embedding_model(model)
    batch_size = _EMBED_BATCH_SIZE_VL if is_vl else _EMBED_BATCH_SIZE

    # 空串会被部分厂商静默丢弃，导致 index 对不齐
    cleaned: list[str] = []
    for i, raw in enumerate(texts):
        text = (raw or "").strip()
        if not text:
            raise RuntimeError(f"Embedding 输入第 {i} 条为空，无法向量化")
        cleaned.append(text)

    out: list[list[float] | None] = [None] * len(cleaned)
    with httpx.Client(timeout=timeout) as client:
        for start in range(0, len(cleaned), batch_size):
            batch = cleaned[start : start + batch_size]
            # VL 模型：纯文本批量也可用 string list；过长文本建议 truncate
            payload: dict[str, Any] = {
                "model": model,
                "input": batch,
                "encoding_format": "float",
            }
            if dimensions > 0:
                payload["dimensions"] = dimensions
            if is_vl:
                payload["truncate"] = "right"

            resp = client.post(url, headers=headers, json=payload)
            try:
                body = resp.json()
            except Exception:
                body = {"raw": (resp.text or "")[:500]}
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Embedding API HTTP {resp.status_code}: {str(body)[:400]}"
                )

            data = body.get("data") if isinstance(body, dict) else None
            if not isinstance(data, list) or not data:
                raise RuntimeError(f"Embedding API 无有效 data: {str(body)[:400]}")

            # 按 index 回填，兼容乱序返回
            for item in data:
                if not isinstance(item, dict):
                    continue
                idx = int(item.get("index", 0))
                emb = item.get("embedding")
                if not isinstance(emb, list):
                    raise RuntimeError("Embedding API 返回的 embedding 不是列表")
                global_idx = start + idx
                if 0 <= global_idx < len(out):
                    out[global_idx] = [float(x) for x in emb]

            got = sum(1 for i in range(start, start + len(batch)) if out[i] is not None)
            if got != len(batch):
                hint = ""
                if is_vl:
                    hint = (
                        " 当前模型是 VL Embedding，文档 RAG 建议改用 "
                        "Qwen/Qwen3-Embedding-8B（文本版）。"
                    )
                raise RuntimeError(
                    f"Embedding API 批量不完整：请求 {len(batch)} 条，"
                    f"返回 {got} 条（batch_start={start}）。{hint}"
                )

    missing = [i for i, v in enumerate(out) if v is None]
    if missing:
        raise RuntimeError(f"Embedding API 缺少部分结果，index={missing[:8]}")
    return out  # type: ignore[return-value]


def _normalize_local_result(res: Any) -> dict:
    import numpy as np

    if isinstance(res, dict):
        if "dense" in res:
            dense = res["dense"]
            if hasattr(dense, "detach"):
                dense = dense.detach().cpu().numpy()
            res["dense"] = np.array(dense, dtype=np.float32).tolist()
        return res

    dense = res
    if hasattr(dense, "detach"):
        dense = dense.detach().cpu().numpy()
    return {"dense": np.array(dense, dtype=np.float32).tolist()}


class EmbeddingService:
    """文本向量化服务（懒加载，可 warmup）。"""

    def __init__(self) -> None:
        self._embedder: Any = None
        self._api_ready: bool = False

    @property
    def ready(self) -> bool:
        from backend.config import settings

        if settings.embedding_provider_resolved == "api":
            return self._api_ready
        return self._embedder is not None

    def warmup(self) -> None:
        """预热：API 模式探测一次；local 模式加载权重。"""
        from backend.config import settings

        provider = settings.embedding_provider_resolved
        if provider == "api":
            if self._api_ready:
                return
            logger.info(
                "Warming up embedding API: base=%s model=%s dim=%s",
                settings.embedding_api_base,
                settings.embedding_model,
                settings.embedding_dimensions,
            )
            _ = _embed_via_api(["warmup"])
            self._api_ready = True
            logger.info("Embedding API is ready")
            return

        if self._embedder is not None:
            return
        logger.info("Warming up local embedding model...")
        self._embedder = _create_local_dense_embedder()
        _ = self._embedder(["warmup"])
        logger.info("Local embedding model is ready")

    def get_embeddings(self, texts: list[str]) -> dict:
        if not texts:
            return {"dense": [], "sparse": []}

        from backend.config import settings

        if settings.embedding_provider_resolved == "api":
            try:
                dense = _embed_via_api(texts)
                self._api_ready = True
                return {"dense": dense}
            except Exception as e:
                raise Exception(f"远程嵌入接口调用失败: {e}") from e

        self.warmup()
        assert self._embedder is not None
        try:
            return _normalize_local_result(self._embedder(texts))
        except Exception as e:
            raise Exception(f"本地嵌入模型调用失败: {e}") from e


# 全进程唯一实例（真正加载发生在 warmup / 首次 get_embeddings）
embedding_service = EmbeddingService()
