"""检索门面：问题向量化 + IndexStore 查询（模块 C 的第一步）。

组合而非新逻辑：embedding 走 app/embedding.py 的现有 HTTP 客户端，
查询走 IndexStore.query。本模块不做任何判定或生成——那是 qa.py 的事。
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel, Field

from app.config import Settings
from app.embedding import embed_texts
from app.index_store import IndexStore


class RetrievedChunk(BaseModel):
    chunk_id: int = Field(strict=True, gt=0)
    document_id: int
    text: str
    heading_path: str = ""
    score: float


def retrieve(
    question: str,
    settings: Settings,
    index: IndexStore,
    *,
    top_k: int = 5,
    client: httpx.Client | None = None,
) -> list[RetrievedChunk]:
    """把问题变成 top-k 片段。question 必须非空白；embedding 失败原样上抛。

    client 可注入：问答链路里与 LLM 调用共用一个连接生命周期；测试里注入
    mock 客户端使本函数完全离线。
    """
    if not question.strip():
        raise ValueError("question must not be blank")
    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=settings.embedding_timeout_seconds)
    try:
        vectors = embed_texts(client, [question], settings=settings)
    finally:
        if owns_client:
            client.close()
    hits = index.query(vectors[0], top_k=top_k)
    return [RetrievedChunk(**hit) for hit in hits]
