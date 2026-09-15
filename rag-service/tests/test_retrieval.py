"""检索能力测试：IndexStore.query 与 retrieval 门面，直接写临时 Chroma，不依赖 Ollama。"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest

from app.modules.retrieval._index_store import IndexStore
from app.modules.retrieval.public import IndexChunk, Retrieval, RetrievalSettings, RetrievalUnavailable


@pytest.fixture
def settings(tmp_path, monkeypatch):
    for variable in list(os.environ):
        if variable.startswith(("EMBEDDING_", "CHUNK_", "EMBED_", "CHROMA_")):
            monkeypatch.delenv(variable, raising=False)
    return RetrievalSettings(
        _env_file=None, chroma_dir=tmp_path / "chroma",
        embedding_model="test-embedding", embedding_dim=3,
    )


@pytest.fixture
def index(settings):
    instance = IndexStore(settings)
    yield instance
    instance.close()


@pytest.fixture
def retrieval(settings):
    instance = Retrieval(settings)
    yield instance
    instance.close()


class TestIndexStoreQuery:

    def test_returns_hits_descending_with_pure_text(self, index):
        index._collection.add(
            ids=["101", "102", "201"],
            embeddings=[[1.0, 0.0, 0.0], [0.9, 0.1, 0.0], [0.0, 0.0, 1.0]],
            documents=["ACID 的正文", "事务隔离的正文", "无关文档"],
            metadatas=[
                {"document_id": 11, "seq": 0, "heading_path": "详细 > 概念"},
                {"document_id": 11, "seq": 1, "heading_path": ""},
                {"document_id": 22, "seq": 0, "heading_path": ""},
            ],
        )

        hits = index.query([1.0, 0.0, 0.0], top_k=3)

        assert [hit.chunk_id for hit in hits] == [101, 102, 201]
        assert hits[0].document_id == 11
        assert hits[0].text == "ACID 的正文"
        assert hits[0].heading_path == "详细 > 概念"
        # score = 1 - cosine 距离：完全同向为 1.0，降序排列
        assert hits[0].score == pytest.approx(1.0)
        assert hits[0].score >= hits[1].score >= hits[2].score

    def test_top_k_limits_and_empty_index(self, index):
        assert index.query([1.0, 0.0, 0.0], top_k=5) == ()
        index._collection.add(
            ids=["1"], embeddings=[[1.0, 0.0, 0.0]],
            documents=["唯一"], metadatas=[{"document_id": 1}],
        )
        assert len(index.query([1.0, 0.0, 0.0], top_k=5)) == 1

    def test_rejects_non_positive_top_k(self, index):
        with pytest.raises(ValueError, match="top_k"):
            index.query([1.0, 0.0, 0.0], top_k=0)

    def test_replace_document_stores_pure_text_not_embedding_text(self, index):
        """载荷修正的回归锚：documents 必须是纯正文，检索结果才能直接用。

        embedding_text = 正文 + 换行 + 标题路径。如果载荷存了它，检索回来
        的 text 会带拼接的标题路径尾巴——为拿干净原文反向查 Java 就破坏
        边界（子 Issue C §五）。
        """
        chunk = IndexChunk(chunk_id=7, text="干净正文", heading_path="一 > 二", tags=[])
        vector = [1.0, 0.0, 0.0]

        index.replace_document(11, [chunk], [vector])

        stored = index._collection.get(ids=["7"], include=["documents"])
        assert stored["documents"] == ["干净正文"]
        hits = index.query([1.0, 0.0, 0.0], top_k=1)
        assert hits[0].text == "干净正文"
        # 标题路径在 metadata，信息不丢
        assert hits[0].heading_path == "一 > 二"


class _FakeEmbeddingTransport(httpx.BaseTransport):
    """把任何问题都编码为固定向量，让 retrieve 完全离线。"""

    def __init__(self, vector: list[float]):
        self.vector = vector
        self.received: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.received.append(json.loads(request.content)["input"][0])
        return httpx.Response(200, json={"embeddings": [self.vector]})


class TestRetrieveFacade:

    def test_retrieves_through_embedding_and_index(self, retrieval, monkeypatch):
        retrieval._index._collection.add(
            ids=["5"], embeddings=[[1.0, 0.0, 0.0]],
            documents=["命中正文"], metadatas=[{"document_id": 3, "heading_path": ""}],
        )
        transport = _FakeEmbeddingTransport([1.0, 0.0, 0.0])
        original_client = httpx.Client
        monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(
            transport=transport, **kwargs,
        ))

        hits = retrieval.search("什么是 ACID？", top_k=3)

        assert [hit.chunk_id for hit in hits] == [5]
        assert hits[0].chunk_id == 5
        assert hits[0].document_id == 3
        assert hits[0].text == "命中正文"
        assert hits[0].score == pytest.approx(1.0)
        # 问题原样送达 embedding 端点，未被改写
        assert transport.received == ["什么是 ACID？"]

    def test_blank_question_is_rejected_before_any_call(self, retrieval, monkeypatch):
        transport = _FakeEmbeddingTransport([1.0, 0.0, 0.0])
        original_client = httpx.Client
        monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(
            transport=transport, **kwargs,
        ))

        with pytest.raises(ValueError, match="blank"):
            retrieval.search("  ")
        assert transport.received == []

    def test_embedding_failure_propagates(self, retrieval, monkeypatch):
        class _FailingTransport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                return httpx.Response(503, json={"error": "down"})

        original_client = httpx.Client
        monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(
            transport=_FailingTransport(), **kwargs,
        ))

        with pytest.raises(RetrievalUnavailable) as error:
            retrieval.search("问题")
        assert error.value.component == "embedding"
        assert error.value.cause == "HTTPStatusError"
