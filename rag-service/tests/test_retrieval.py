"""检索能力测试：IndexStore.query 与 retrieval 门面，直接写临时 Chroma，不依赖 Ollama。"""

from __future__ import annotations

import json
import os
import sys
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


class TestPersistencePathGuard:
    """chromadb 1.5.9 在 Windows 下对非 ASCII 目录静默丢失 HNSW 文件（#74），必须在打开前拒绝。"""

    @pytest.mark.parametrize("platform,path,safe", [
        ("win32", "D:/easyrag-data/chroma", True),
        ("win32", "D:/知识库/rag-service/data/chroma", False),
        ("win32", "C:/Users/café/chroma", False),
        ("linux", "/srv/个人项目/chroma", True),
        ("darwin", "/Users/个人/chroma", True),
    ])
    def test_only_windows_requires_ascii_paths(self, platform, path, safe):
        from pathlib import Path

        from app.modules.retrieval._index_store import persistence_path_is_safe

        assert persistence_path_is_safe(Path(path), platform=platform) is safe

    def test_unsafe_path_is_refused_before_anything_is_written(self, tmp_path, monkeypatch):
        from app.modules.retrieval import _index_store

        monkeypatch.setattr(_index_store, "_PLATFORM", "win32")
        chroma_dir = tmp_path / "个人知识库" / "chroma"
        settings = RetrievalSettings(_env_file=None, chroma_dir=chroma_dir, embedding_model="test-embedding", embedding_dim=3)

        store = IndexStore(settings)
        try:
            assert store.probe() == {"status": "DOWN", "collection": settings.collection_name, "error": "UnsafePersistencePath"}
            with pytest.raises(_index_store.UnsafePersistencePath):
                store.reset()
        finally:
            store.close()
        assert not chroma_dir.exists()

        module = Retrieval(settings)
        try:
            assert module.health()["chroma"]["error"] == "UnsafePersistencePath"
            with pytest.raises(RetrievalUnavailable) as error:
                module.search("问题")
            assert (error.value.component, error.value.cause) == ("index", "UnsafePersistencePath")
        finally:
            module.close()

    def test_ascii_path_on_windows_opens_normally(self, tmp_path, monkeypatch):
        from app.modules.retrieval import _index_store

        monkeypatch.setattr(_index_store, "_PLATFORM", "win32")
        settings = RetrievalSettings(_env_file=None, chroma_dir=tmp_path / "chroma", embedding_model="test-embedding", embedding_dim=3)
        store = IndexStore(settings)
        try:
            assert store.probe()["status"] == "UP"
        finally:
            store.close()

    @pytest.mark.skipif(sys.platform != "win32", reason="只有 Windows 的窄字符文件 API 会丢字")
    def test_upstream_canary_chromadb_still_loses_vectors_under_non_ascii_directory(self, tmp_path):
        """绕过守门直接写 chromadb，证明缺陷仍在；本用例一旦失败，说明上游已修复、守门可以放开。"""
        import ctypes

        import chromadb
        from chromadb.config import Settings as ChromaSettings

        if ctypes.windll.kernel32.GetACP() == 65001:
            pytest.skip("系统 ANSI 代码页已是 UTF-8，非 ASCII 路径不再丢字")
        directory = tmp_path / "个人知识库" / "chroma"
        directory.mkdir(parents=True)
        # 阈值降到 10，写 20 条即触发落盘与日志清理，不必写满默认的 1000 条。
        metadata = {"hnsw:space": "cosine", "hnsw:sync_threshold": 10, "hnsw:batch_size": 5}

        def open_collection():
            client = chromadb.PersistentClient(path=str(directory), settings=ChromaSettings(anonymized_telemetry=False))
            return client, client.get_or_create_collection("canary", metadata=metadata)

        client, collection = open_collection()
        collection.upsert(ids=[str(i) for i in range(20)], embeddings=[[float(i), 1.0, 0.5] for i in range(20)])
        assert collection.count() == 20
        client.close()
        written = {file.name for segment in directory.iterdir() if segment.is_dir() for file in segment.iterdir()}
        assert "index_metadata.pickle" in written
        assert "data_level0.bin" not in written
        try:
            client, collection = open_collection()
            survived = collection.count()
            client.close()
        except Exception:  # noqa: BLE001 - 上游抛的是 chromadb.errors.InternalError，类型本身不是契约
            survived = None
        assert survived != 20
