"""
文档索引构建管线

将文件夹中的文档经过 DocumentLoader 分块后：
  L1、L2 → Postgres document_chunks 表（结构化查询、上下文补全）
  L3    → Milvus（向量检索，含 dense_vector）

检索时：Milvus 召回 L3 → Rerank → 按 parent_chunk_id 批量回查 L2 给 LLM。

保证：
  - chunk_id 全局唯一，重复入库时 PG upsert / Milvus 覆盖
  - L1/L2 与 L3 通过 root_chunk_id / parent_chunk_id 关联

source 支持：
  - 本地文件夹路径
  - 本地文件路径
  - 网络 URL（如阿里云 OSS 公网/签名地址）
  - 以上任意组合的列表
"""
import asyncio
import os

from backend.indexing.document_loader import DocumentLoader
from backend.indexing.milvus_client import MilvusService, get_milvus_service
from backend.indexing.postgres_store import insert_chunks, init_document_chunks_table


async def build_index(
    source: str | list[str],
    milvus_service: MilvusService | None = None,
    chunk_size: int = 300,
    chunk_overlap: int = 50,
    filenames: dict[str, str] | None = None,
    path_meta: dict[str, str] | None = None,
) -> dict:
    """
    索引文档并写入 Postgres + Milvus。

    Args:
        source: 本地文件夹 / 本地文件 / 网络 URL，或上述组成的列表
        milvus_service: 可复用的 MilvusService 实例，不传则从 settings 自动创建
        chunk_size: L3 分块大小基准（默认 300，夹在 200～400；L2/L1 自动推算）
        chunk_overlap: L3 分块重叠量（默认 50）
        filenames: 可选，source → 原始文件名映射（OSS URL 建议传入原始名）
        path_meta: 可选，source → 入库 file_path（用签名 URL 下载时可映射到永久 OSS 地址）

    Returns:
        {"files": int, "l1_l2_count": int, "l3_count": int}
    """
    # 延迟导入，避免无关路径启动时加载 torch / BGE-M3
    from backend.indexing.embedding import embedding_service

    if milvus_service is None:
        milvus_service = get_milvus_service()

    # 确保 PG 表和 Milvus collection 均已就绪
    await init_document_chunks_table()
    await asyncio.to_thread(milvus_service.init_collection)

    loader = DocumentLoader(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    # 文档加载/切块为 CPU 同步工作，放线程池避免堵事件循环
    all_chunks = await asyncio.to_thread(
        _load_chunks, loader, source, filenames, path_meta
    )
    if not all_chunks:
        print(f"未找到可处理的文档: {source}")
        return {"files": 0, "l1_l2_count": 0, "l3_count": 0}

    # 2. 分流：L1/L2 → PG, L3 → Milvus
    l1_l2_chunks = [c for c in all_chunks if c["chunk_level"] in (1, 2)]
    l3_chunks = [c for c in all_chunks if c["chunk_level"] == 3]

    files = len({c["filename"] for c in all_chunks})

    # 3. L1 + L2 写入 Postgres
    pg_count = 0
    if l1_l2_chunks:
        pg_count = await insert_chunks(l1_l2_chunks)
        print(f"L1/L2 块已写入 Postgres: {pg_count} 条")

    # 4. L3 向量化并写入 Milvus（embedding / Milvus 均为同步阻塞）
    mv_count = 0
    if l3_chunks:
        l3_texts = [c["text"] for c in l3_chunks]
        print(f"正在为 {len(l3_texts)} 个 L3 块计算向量...")

        vectors = await asyncio.to_thread(embedding_service.get_embeddings, l3_texts)

        vectors = vectors["dense"] if isinstance(vectors, dict) else vectors
        for chunk, vector in zip(l3_chunks, vectors):
            chunk["dense_vector"] = vector

        await asyncio.to_thread(milvus_service.insert, l3_chunks)
        mv_count = len(l3_chunks)
        print(f"L3 块已写入 Milvus: {mv_count} 条")

    print(f"索引完成: {files} 个文件, L1+L2={pg_count}, L3={mv_count}")
    return {"files": files, "l1_l2_count": pg_count, "l3_count": mv_count}


def _load_chunks(
    loader: DocumentLoader,
    source: str | list[str],
    filenames: dict[str, str] | None,
    path_meta: dict[str, str] | None,
) -> list[dict]:
    """按 source 类型选择加载方式：文件夹 / 单文件或 URL / 列表。"""
    if isinstance(source, list):
        return loader.load_documents_from_sources(source, filenames, path_meta)

    # 单个字符串：本地目录 → 扫目录；否则当作文件路径或网络 URL
    if not DocumentLoader.is_url(source) and os.path.isdir(source):
        return loader.load_documents_from_folder(source)

    return loader.load_documents_from_sources([source], filenames, path_meta)


if __name__ == "__main__":
    asyncio.run(build_index(r"F:\langgraph-demo\backend\test"))
    print("索引完成")

    # from langchain_huggingface import HuggingFaceEmbeddings
    # embedding = HuggingFaceEmbeddings(model_name="BAAI/bge-m3")
    # # 测试
    # vec = embedding.embed_query("测试医疗RAG")
    # print("向量维度：",len(vec))  # 输出1024就是成功！
