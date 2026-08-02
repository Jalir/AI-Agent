"""
Rerank 服务：远程 OpenAI 兼容 /v1/rerank，或本地 CrossEncoder。

混合检索（Dense + BM25 + RRF）多召回后精排。懒加载，风格对齐 embedding_service。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _resolve_local_device() -> str:
    from backend.config import settings

    try:
        import torch
    except ImportError as e:
        raise RuntimeError(
            "本地 Rerank 依赖未安装。请执行: "
            "pip install -r backend/requirements-local-models.txt"
        ) from e

    env = (settings.rerank_device or "").strip()
    if env:
        return env
    return "cuda" if torch.cuda.is_available() else "cpu"


def _resolve_local_model_name() -> str:
    from backend.config import settings

    name = (settings.rerank_model or "").strip()
    # api 默认模型名若误用于 local，回退 BGE
    if not name or name.startswith("Qwen/"):
        return "BAAI/bge-reranker-v2-m3"
    return name


def _rerank_via_api(
    query: str,
    documents: list[str],
    *,
    top_n: int | None = None,
) -> list[float]:
    """调用远程 rerank，返回与 documents 等长的 relevance_score 列表。

    必须显式传 top_n（默认=候选全文数），否则部分厂商可能只返回极少条，
    未出现在 results 里的文档会被当成 0 分。
    """
    from backend.config import settings

    base = settings.rerank_api_base
    api_key = (settings.rerank_api_key or settings.embedding_api_key or "").strip()
    model = (settings.rerank_model or "").strip()
    if not base or not api_key or not model:
        raise RuntimeError(
            "Rerank API 未配置完整：需要 RERANK_BASE_URL（或 EMBEDDING_BASE_URL）/ "
            "API Key / RERANK_MODEL"
        )

    url = f"{base}/rerank"
    timeout = float(settings.rerank_timeout_sec or 120.0)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    n = len(documents)
    # 对全部候选打分；最终截断由外层 top_k 负责
    want = max(1, min(n, int(top_n) if top_n is not None else n))
    payload: dict[str, Any] = {
        "model": model,
        "query": query,
        "documents": documents,
        "top_n": want,
    }
    instruction = (settings.rerank_instruction or "").strip()
    if instruction:
        payload["instruction"] = instruction

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=headers, json=payload)
        try:
            body = resp.json()
        except Exception:
            body = {"raw": (resp.text or "")[:500]}
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Rerank API HTTP {resp.status_code}: {str(body)[:400]}"
            )

    results = body.get("results") if isinstance(body, dict) else None
    if not isinstance(results, list):
        raise RuntimeError(f"Rerank API 无有效 results: {str(body)[:400]}")

    scores = [0.0] * len(documents)
    for item in results:
        if not isinstance(item, dict):
            continue
        idx = int(item.get("index", -1))
        if 0 <= idx < len(scores):
            scores[idx] = float(item.get("relevance_score") or 0.0)
    return scores


class RerankService:
    """精排服务（懒加载，可 warmup）。"""

    def __init__(self) -> None:
        self._model: Any = None
        self._api_ready: bool = False

    @property
    def ready(self) -> bool:
        from backend.config import settings

        if settings.rerank_provider_resolved == "api":
            return self._api_ready
        return self._model is not None

    def warmup(self) -> None:
        from backend.config import settings

        if settings.rerank_provider_resolved == "api":
            if self._api_ready:
                return
            logger.info(
                "Warming up rerank API: base=%s model=%s",
                settings.rerank_api_base,
                settings.rerank_model,
            )
            _ = _rerank_via_api("warmup", ["warmup passage"], top_n=1)
            self._api_ready = True
            logger.info("Rerank API is ready")
            return

        if self._model is not None:
            return
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as e:
            raise RuntimeError(
                "本地 Rerank 依赖未安装。请执行: "
                "pip install -r backend/requirements-local-models.txt"
            ) from e

        model_name = _resolve_local_model_name()
        device = _resolve_local_device()
        logger.info("Warming up local reranker: model=%s device=%s", model_name, device)
        self._model = CrossEncoder(model_name, device=device)
        _ = self._model.predict([["warmup", "warmup passage"]])
        logger.info("Local reranker model is ready")

    def rerank(
        self,
        query: str,
        docs: list[dict],
        top_k: int,
        *,
        score_threshold: float | None = None,
    ) -> list[dict]:
        """
        对混合检索候选精排，返回最多 top_k 条。

        - 保留原 RRF 分为 rrf_score
        - score 覆盖为 rerank 分（越高越相关）
        - score_threshold 非空时过滤低于阈值的结果
        """
        if not docs:
            return []
        if not (query or "").strip():
            return docs[:top_k]

        from backend.config import settings

        passages = [str(d.get("text") or "") for d in docs]

        if settings.rerank_provider_resolved == "api":
            try:
                scores = _rerank_via_api(query, passages, top_n=len(passages))
                self._api_ready = True
            except Exception as e:
                raise RuntimeError(f"远程重排接口调用失败: {e}") from e
        else:
            self.warmup()
            assert self._model is not None
            pairs = [[query, p] for p in passages]
            raw_scores = self._model.predict(pairs, show_progress_bar=False)
            if hasattr(raw_scores, "tolist"):
                scores = [float(x) for x in raw_scores.tolist()]
            else:
                scores = [float(x) for x in raw_scores]

        ranked: list[tuple[dict, float]] = []
        for doc, score in zip(docs, scores):
            item = dict(doc)
            item["rrf_score"] = float(item.get("score") or 0.0)
            item["score"] = score
            ranked.append((item, score))

        ranked.sort(key=lambda x: x[1], reverse=True)

        out: list[dict] = []
        for item, score in ranked:
            if score_threshold is not None and score < score_threshold:
                continue
            out.append(item)
            if len(out) >= top_k:
                break
        return out


rerank_service = RerankService()
