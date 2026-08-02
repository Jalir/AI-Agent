"""
Milvus 客户端封装

- milvus_client_session：连接生命周期（创建 / 关闭）
- MilvusService：连接与集合配置，链式返回 self，不持有连接
- MilvusStore：集合读写，不持有连接，所有 IO 经 milvus_client_session

uri 兼容：
  - 本地 Milvus Lite：./milvus_demo.db / 绝对路径 .db 文件
  - 远程服务：http(s)://host:port
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv

load_dotenv()

# 必须在 import pymilvus 之前处理：本地 .db 不能留在环境变量 MILVUS_URI 里
try:
    from backend.config import protect_pymilvus_env
except ImportError:
    protect_pymilvus_env = None  # type: ignore[assignment]

if protect_pymilvus_env is not None:
    protect_pymilvus_env()
else:
    _raw = (os.environ.get("MILVUS_URI") or "").strip()
    if _raw and not _raw.startswith(("http://", "https://")):
        os.environ.setdefault("APP_MILVUS_URI", _raw)
        os.environ["MILVUS_URI"] = ""

from pymilvus import AnnSearchRequest, DataType, Function, FunctionType, MilvusClient, RRFRanker

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_milvus_uri(uri: str) -> str:
    """规范化 Milvus URI：http(s) 原样返回；本地相对路径锚定到项目根目录。"""
    uri = (uri or "").strip() or "./milvus_demo.db"
    if uri.startswith(("http://", "https://")):
        return uri
    path = Path(uri)
    if not path.is_absolute():
        path = (_PROJECT_ROOT / path).resolve()
    return str(path)


def is_milvus_lite_uri(uri: str) -> bool:
    return not uri.startswith(("http://", "https://"))

QUERY_MAX_LIMIT = 16384  # Milvus 单次 query 硬上限

DEFAULT_QUERY_FIELDS = ["chunk_id", "filename", "file_type", "chunk_level", "text"]
DEFAULT_HYBRID_OUTPUT_FIELDS = [
    "chunk_id",
    "parent_chunk_id",
    "root_chunk_id",
    "filename",
    "file_path",
    "file_type",
    "page_number",
    "chunk_level",
    "chunk_idx",
    "text",
]

_HYBRID_FIELD_DEFAULTS: dict[str, object] = {
    "text": "",
    "parent_chunk_id": "",
    "root_chunk_id": "",
    "filename": "",
    "file_path": "",
    "file_type": "",
    "page_number": 0,
    "chunk_level": 0,
    "chunk_idx": 0,
}


@contextmanager
def milvus_client_session(uri: str, token: str = "") -> Iterator[MilvusClient]:
    """创建 Milvus 连接，退出时自动关闭。"""
    client = MilvusClient(uri=uri, token=token)
    try:
        yield client
    finally:
        client.close()


def _normalize_filter(filter_expr: str) -> str:
    """空字符串在 Milvus 里不代表"查全部"，用 chunk_id 存在判断兜底。"""
    return filter_expr.strip() if filter_expr and filter_expr.strip() else 'chunk_id != ""'


def _normalize_optional_filter(filter_expr: str) -> str | None:
    expr = filter_expr.strip() if filter_expr else ""
    return expr or None


def _format_hybrid_hits(raw_results: list) -> list[dict]:
    """将 hybrid_search 原始 Hit 结构转为干净的 dict 列表。

    pymilvus 3.x：Hit.entity 返回 Hit 自身（兼容旧 ORM），真正字段在 hit["entity"]
    或经 Hit.get("text") 穿透读取。切勿 dict(hit.entity)，否则 text 等字段会全部丢失。
    """
    if not raw_results:
        return []

    clean_results: list[dict] = []
    for hit in raw_results[0]:
        nested = hit.get("entity") if hasattr(hit, "get") else None
        item: dict = dict(nested) if isinstance(nested, dict) else {}
        # Hit.get 会穿透到 entity，兜底补齐未拷到的字段
        for field, default in _HYBRID_FIELD_DEFAULTS.items():
            if field not in item or item[field] is None:
                val = hit.get(field, default) if hasattr(hit, "get") else default
                item[field] = default if val is None else val
        if "chunk_id" not in item and hasattr(hit, "get"):
            item["chunk_id"] = hit.get("chunk_id") or ""
        item["score"] = float(getattr(hit, "score", None) or hit.get("distance") or 0.0)
        item["id"] = getattr(hit, "id", None)
        clean_results.append(item)
    return clean_results


class MilvusService:
    """Milvus 连接与集合配置；本身不持有连接，链式方法返回 self。"""

    def __init__(
        self,
        uri: str,
        token: str = "",
        collection: str = "embeddings_collection",
        dense_dim: int = 1024,
    ) -> None:
        self.uri = uri
        self.token = token
        self.collection = collection
        self.dense_dim = dense_dim
        self._loaded = False
        self._store = MilvusStore(self)

    def with_uri(self, uri: str) -> MilvusService:
        self.uri = uri
        self._loaded = False
        return self

    def with_token(self, token: str) -> MilvusService:
        self.token = token
        self._loaded = False
        return self

    def with_collection(self, collection: str) -> MilvusService:
        if collection != self.collection:
            self._loaded = False
        self.collection = collection
        return self

    def with_dense_dim(self, dense_dim: int) -> MilvusService:
        self.dense_dim = dense_dim
        return self

    @property
    def store(self) -> MilvusStore:
        return self._store

    # --- 向后兼容：委托给 MilvusStore ---

    def init_collection(self) -> None:
        self._store.init_collection()

    def insert(self, insert_data: list[dict]) -> dict:
        return self._store.insert(insert_data)

    def query(
        self,
        filter_expr: str = "",
        output_fields: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        return self._store.query(filter_expr, output_fields, limit, offset)

    def hybrid_search(
        self,
        query_text: str,
        dense_vector: list[float],
        top_k: int = 5,
        filter_expr: str = "",
        output_fields: list[str] | None = None,
        rrf_k: int = 60,
    ) -> list[dict]:
        return self._store.hybrid_search(
            query_text, dense_vector, top_k, filter_expr, output_fields, rrf_k
        )

    def delete(self, filter_expr: str) -> dict:
        return self._store.delete(filter_expr)

    def drop_collection(self) -> bool:
        return self._store.drop_collection()

    def has_collection(self) -> bool:
        return self._store.has_collection()


class MilvusStore:
    """Milvus 集合读写；本身不持有连接，所有 IO 经 milvus_client_session。"""

    def __init__(self, service: MilvusService) -> None:
        self._cfg = service

    def _ensure_loaded(self, client: MilvusClient) -> None:
        if self._cfg._loaded:
            return
        client.load_collection(collection_name=self._cfg.collection)
        self._cfg._loaded = True
        logger.info("Collection '%s' loaded into memory.", self._cfg.collection)

    def _build_schema(self, client: MilvusClient):
        schema = client.create_schema(auto_id=False, enable_dynamic_field=True)

        schema.add_field(
            field_name="chunk_id", datatype=DataType.VARCHAR, max_length=128, is_primary=True
        )
        schema.add_field(
            field_name="parent_chunk_id", datatype=DataType.VARCHAR, max_length=128
        )
        schema.add_field(
            field_name="root_chunk_id", datatype=DataType.VARCHAR, max_length=128
        )
        schema.add_field(field_name="filename", datatype=DataType.VARCHAR, max_length=512)
        schema.add_field(field_name="file_path", datatype=DataType.VARCHAR, max_length=1024)
        schema.add_field(field_name="file_type", datatype=DataType.VARCHAR, max_length=32)
        schema.add_field(field_name="page_number", datatype=DataType.INT64)
        schema.add_field(field_name="chunk_level", datatype=DataType.INT16)
        schema.add_field(field_name="chunk_idx", datatype=DataType.INT64)
        schema.add_field(
            field_name="text",
            datatype=DataType.VARCHAR,
            max_length=65535,
            enable_analyzer=True,
            analyzer_params={"type": "chinese"},
            enable_match=True,
        )
        schema.add_field(
            field_name="dense_vector", datatype=DataType.FLOAT_VECTOR, dim=self._cfg.dense_dim
        )
        schema.add_field(field_name="sparse_vector", datatype=DataType.SPARSE_FLOAT_VECTOR)

        schema.add_function(
            Function(
                name="text_bm25_emb",
                input_field_names=["text"],
                output_field_names=["sparse_vector"],
                function_type=FunctionType.BM25,
            )
        )
        return schema

    def _build_index_params(self, client: MilvusClient):
        index_params = client.prepare_index_params()
        index_params.add_index(
            field_name="dense_vector",
            index_type="HNSW",
            metric_type="IP",
            params={"M": 16, "efConstruction": 256},
        )
        index_params.add_index(
            field_name="sparse_vector",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
            params={"drop_ratio_build": 0.2},
        )
        return index_params

    def init_collection(self) -> None:
        """初始化 collection：已存在则跳过创建，但仍确保已加载。"""
        with milvus_client_session(self._cfg.uri, self._cfg.token) as client:
            if client.has_collection(self._cfg.collection):
                logger.info(
                    "Collection '%s' already exists, skip creating.", self._cfg.collection
                )
                self._ensure_loaded(client)
                return

            client.create_collection(
                collection_name=self._cfg.collection,
                schema=self._build_schema(client),
                index_params=self._build_index_params(client),
            )
            logger.info("Collection '%s' created.", self._cfg.collection)
            self._ensure_loaded(client)

    def insert(self, insert_data: list[dict]) -> dict:
        """
        插入数据。每个 dict 至少包含：
        chunk_id, parent_chunk_id, root_chunk_id, filename, file_path,
        file_type, page_number, chunk_level, chunk_idx, text, dense_vector
        （sparse_vector 由 BM25 Function 自动生成）
        """
        if not insert_data:
            logger.warning("insert_data 为空，跳过插入")
            return {}

        with milvus_client_session(self._cfg.uri, self._cfg.token) as client:
            self._ensure_loaded(client)
            result = client.insert(collection_name=self._cfg.collection, data=insert_data)
            logger.info(
                "Inserted %s rows into '%s'.",
                result.get("insert_count", len(insert_data)),
                self._cfg.collection,
            )
            return result

    def query(
        self,
        filter_expr: str = "",
        output_fields: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """标量查询（非向量检索），类似 SQL 的 WHERE + SELECT。"""
        expr = _normalize_filter(filter_expr)
        fields = output_fields or DEFAULT_QUERY_FIELDS

        with milvus_client_session(self._cfg.uri, self._cfg.token) as client:
            self._ensure_loaded(client)
            return client.query(
                collection_name=self._cfg.collection,
                filter=expr,
                output_fields=fields,
                limit=min(limit, QUERY_MAX_LIMIT),
                offset=offset,
            )

    def hybrid_search(
        self,
        query_text: str,
        dense_vector: list[float],
        top_k: int = 5,
        filter_expr: str = "",
        output_fields: list[str] | None = None,
        rrf_k: int = 60,
    ) -> list[dict]:
        """Dense + BM25 混合检索，RRF 融合排序。"""
        fields = output_fields or DEFAULT_HYBRID_OUTPUT_FIELDS
        expr = _normalize_optional_filter(filter_expr)
        recall_limit = top_k * 2

        with milvus_client_session(self._cfg.uri, self._cfg.token) as client:
            self._ensure_loaded(client)

            req_dense = AnnSearchRequest(
                data=[dense_vector],
                anns_field="dense_vector",
                param={"metric_type": "IP"},
                limit=recall_limit,
                expr=expr,
            )
            req_sparse = AnnSearchRequest(
                data=[query_text],
                anns_field="sparse_vector",
                param={"metric_type": "BM25"},
                limit=recall_limit,
                expr=expr,
            )

            raw_results = client.hybrid_search(
                collection_name=self._cfg.collection,
                reqs=[req_dense, req_sparse],
                ranker=RRFRanker(k=rrf_k),
                limit=top_k,
                output_fields=fields,
            )

        return _format_hybrid_hits(raw_results)

    def delete(self, filter_expr: str) -> dict:
        """按标量表达式删除实体（如 filename == \"a.docx\"）。"""
        expr = (filter_expr or "").strip()
        if not expr:
            raise ValueError("delete 需要非空 filter_expr")
        with milvus_client_session(self._cfg.uri, self._cfg.token) as client:
            if not client.has_collection(self._cfg.collection):
                logger.info(
                    "delete skipped: collection '%s' missing", self._cfg.collection
                )
                return {}
            self._ensure_loaded(client)
            result = client.delete(
                collection_name=self._cfg.collection,
                filter=expr,
            )
            logger.info(
                "Deleted from '%s' filter=%s result=%s",
                self._cfg.collection,
                expr[:120],
                result,
            )
            return result if isinstance(result, dict) else {"result": result}

    def has_collection(self) -> bool:
        with milvus_client_session(self._cfg.uri, self._cfg.token) as client:
            return bool(client.has_collection(self._cfg.collection))

    def drop_collection(self) -> bool:
        """删除整个 collection（工作区销毁时用）。不存在视为已清理成功（返回 True）。"""
        with milvus_client_session(self._cfg.uri, self._cfg.token) as client:
            name = self._cfg.collection
            if not client.has_collection(name):
                logger.info("drop_collection skip: '%s' not found", name)
                self._cfg._loaded = False
                return True
            client.drop_collection(name)
            self._cfg._loaded = False
            logger.info("Dropped collection '%s'", name)
            return True


def get_milvus_service(
    uri: str | None = None,
    token: str | None = None,
    collection: str | None = None,
    dense_dim: int | None = None,
) -> MilvusService:
    """
    从环境变量或显式参数构建 MilvusService。

    读取顺序：显式 uri → APP_MILVUS_URI（本地 Lite）→ MILVUS_URI（远程）→ 默认本地 db
    dense_dim 默认跟 settings.embedding_dimensions（须与建库向量一致）。
    """
    if dense_dim is None:
        try:
            from backend.config import settings

            dense_dim = int(settings.embedding_dimensions or 1024)
        except Exception:
            dense_dim = int(os.getenv("EMBEDDING_DIMENSIONS", "1024") or "1024")

    raw_uri = (
        uri
        or os.getenv("APP_MILVUS_URI")
        or os.getenv("MILVUS_URI")
        or "./milvus_demo.db"
    )
    return MilvusService(
        uri=resolve_milvus_uri(raw_uri),
        token=token or os.getenv("MILVUS_TOKEN", ""),
        collection=collection or os.getenv("MILVUS_COLLECTION", "embeddings_collection"),
        dense_dim=dense_dim,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    service = get_milvus_service(dense_dim=1024)
    # service.init_collection()

    result = service.query(
        filter_expr='filename == "doc1.pdf"',
        output_fields=["chunk_id", "text", "chunk_level", "filename"],
    )
    for row in result:
        print(row)
