"""索引维护接口测试，直接写临时 Chroma 准备数据，不依赖 /embed 或 Ollama。"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, main
from app.config import Settings
from app.index_store import IndexStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    for variable in list(os.environ):
        if variable.startswith(("EMBEDDING_", "CHUNK_", "EMBED_")):
            monkeypatch.delenv(variable, raising=False)
    settings = Settings(_env_file=None, embedding_model="test-embedding", embedding_dim=3)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    with TestClient(main.app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def populated_index(client):
    index = main.app.state.index
    index._collection.add(
        ids=["101", "102", "201"],
        embeddings=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        documents=["第一篇的第一块", "第一篇的第二块", "另一篇的正文"],
        metadatas=[{"document_id": 11}, {"document_id": 11}, {"document_id": 22}],
    )
    return index


@pytest.fixture
def comparison_collection(populated_index):
    collection = populated_index._client.create_collection(
        name="easyrag_other-model_3",
        metadata={"hnsw:space": "cosine", "embedding_model": "other-model", "embedding_dim": 3},
    )
    collection.add(
        ids=["101"],
        embeddings=[[1.0, 0.0, 0.0]],
        documents=["另一模型的对照索引"],
        metadatas=[{"document_id": 11}],
    )
    return collection


def test_delete_removes_only_requested_document_and_reports_count(
    client, populated_index, comparison_collection
):
    response = client.delete("/index/11")

    assert response.status_code == 200
    assert response.json() == {"removed": 2}
    remaining = populated_index._collection.get(include=["documents", "metadatas"])
    assert remaining["ids"] == ["201"]
    assert remaining["documents"] == ["另一篇的正文"]
    assert remaining["metadatas"] == [{"document_id": 22}]
    assert comparison_collection.get(include=[])["ids"] == ["101"]
    assert populated_index.bm25.size == 0


def test_delete_is_idempotent_and_unknown_document_removes_nothing(client, populated_index):
    for document_id, expected_count in [(11, 2), (11, 0), (999, 0)]:
        response = client.delete(f"/index/{document_id}")
        assert response.status_code == 200
        assert response.json() == {"removed": expected_count}
    assert populated_index.vector_count() == 1


@pytest.mark.parametrize("document_id", ["0", "-1", "not-a-number", str(2**63)])
def test_delete_rejects_invalid_document_ids(client, populated_index, document_id):
    response = client.delete(f"/index/{document_id}")

    assert response.status_code == 422
    assert populated_index.vector_count() == 3


def test_delete_accepts_the_full_positive_bigint_range(client, populated_index):
    populated_index._collection.add(
        ids=["301", "302"],
        embeddings=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        documents=["相邻大 ID 的文档", "目标大 ID 的文档"],
        metadatas=[{"document_id": 2**63 - 2}, {"document_id": 2**63 - 1}],
    )

    response = client.delete(f"/index/{2**63 - 1}")

    assert response.status_code == 200
    assert response.json() == {"removed": 1}
    assert set(populated_index._collection.get(include=[])["ids"]) == {"101", "102", "201", "301"}


def test_reset_only_clears_current_collection_and_preserves_metadata(
    client, populated_index, comparison_collection
):
    collection = populated_index._collection
    original_id = collection.id
    original_metadata = dict(collection.metadata)

    response = client.post("/reset")

    assert response.status_code == 200
    assert response.json() == {"reset": True}
    assert collection.count() == 0
    assert comparison_collection.get(include=[])["ids"] == ["101"]
    reopened = IndexStore(main.app.state.settings)
    assert reopened.vector_count() == 0
    assert reopened._collection.id == original_id
    assert reopened._collection.metadata == original_metadata
    assert populated_index.bm25.size == 0


def test_reset_is_idempotent_for_an_empty_collection(client):
    for _attempt in range(2):
        response = client.post("/reset")
        assert response.status_code == 200
        assert response.json() == {"reset": True}
    assert main.app.state.index.vector_count() == 0


@pytest.mark.parametrize("method,path", [("DELETE", "/index/11"), ("POST", "/reset")])
def test_unavailable_index_returns_503_but_health_stays_up(client, monkeypatch, method, path):
    index = main.app.state.index
    monkeypatch.setattr(index, "_collection", None)
    monkeypatch.setattr(index, "chroma_error", "PermissionError")

    async def unavailable_embedding(_settings):
        return {"status": "DOWN", "reachable": False}

    monkeypatch.setattr(main, "_probe_embedding", unavailable_embedding)

    response = client.request(method, path)

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "INDEX_UNAVAILABLE"
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "UP"
    assert health.json()["chroma"]["status"] == "DOWN"
    assert health.json()["chroma"]["error"] == "PermissionError"
    assert health.json()["embedding"]["status"] == "DOWN"


@pytest.mark.parametrize(
    "method,path,operation",
    [("DELETE", "/index/11", "delete"), ("POST", "/reset", "get"), ("POST", "/reset", "delete")],
)
def test_storage_failure_does_not_claim_success_or_expose_details(
    client, populated_index, monkeypatch, method, path, operation
):
    def fail_storage(*_args, **_kwargs):
        raise RuntimeError("sensitive-path: storage unavailable")

    monkeypatch.setattr(populated_index._collection, operation, fail_storage)

    response = client.request(method, path)

    assert response.status_code == 503
    assert response.json() == {
        "detail": {"error": "INDEX_UNAVAILABLE", "cause": "RuntimeError"}
    }
    assert "sensitive-path" not in response.text
    assert populated_index.vector_count() == 3


@pytest.fixture
def embedding_server(client, monkeypatch):
    state = {"requests": [], "timeouts": [], "dimensions": 3,
             "fail_at": None, "timeout_at": None, "overflow_at": None}
    original_client = httpx.Client

    def respond(request):
        payload = json.loads(request.content)
        state["requests"].append(payload)
        state["timeouts"].append(request.extensions["timeout"]["read"])
        if len(state["requests"]) == state["timeout_at"]:
            raise httpx.ReadTimeout("private timeout details", request=request)
        if len(state["requests"]) == state["fail_at"]:
            return httpx.Response(503, text="private upstream details")
        return httpx.Response(200, json={
            "embeddings": [
                [1e40 if len(state["requests"]) == state["overflow_at"] else float(len(text))]
                + [1.0] * (state["dimensions"] - 1)
                for text in payload["input"]
            ]
        })

    monkeypatch.setattr(
        httpx, "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    return state


def embed_payload(count=3):
    return {"document_id": 11, "chunks": [
        {"chunk_id": 301 + offset, "text": f"new chunk {offset}\r\n正文",
         "heading_path": "Guide" if offset else "", "tags": ["RAG"] if offset else []}
        for offset in range(count)
    ]}


def test_embed_replaces_the_complete_document_in_internal_batches(
    client, populated_index, comparison_collection, embedding_server
):
    main.app.state.settings.embed_batch_size = 2
    payload = embed_payload()

    response = client.post("/embed", json=payload)

    assert response.status_code == 200
    assert response.json() == {"indexed": 3}
    assert [len(request["input"]) for request in embedding_server["requests"]] == [2, 1]
    assert all(request["truncate"] is False for request in embedding_server["requests"])
    # embedding 输入口径：正文 + 换行 + 非空标题路径（不变）
    expected_embedding_inputs = [
        chunk["text"] + ("\n" + chunk["heading_path"] if chunk["heading_path"] else "")
        for chunk in payload["chunks"]
    ]
    assert [text for request in embedding_server["requests"] for text in request["input"]] == expected_embedding_inputs
    records = populated_index._collection.get(include=["documents", "metadatas"])
    by_id = {identifier: (document, metadata) for identifier, document, metadata in zip(
        records["ids"], records["documents"], records["metadatas"], strict=True
    )}
    assert set(by_id) == {"201", "301", "302", "303"}
    for sequence, chunk in enumerate(payload["chunks"]):
        document, metadata = by_id[str(chunk["chunk_id"])]
        # documents 载荷口径（子 Issue C §五）：纯正文——检索返回的内容要直接
        # 交给 LLM 与溯源展示，不能带拼接的标题路径尾巴；标题路径在 metadata
        assert document == chunk["text"]
        assert metadata == {"document_id": 11, "seq": sequence,
                            "heading_path": chunk["heading_path"],
                            **({"tags": chunk["tags"]} if chunk["tags"] else {})}
    assert comparison_collection.get(include=[])["ids"] == ["101"]
    assert populated_index.bm25.size == 0


def test_embed_is_idempotent_and_removes_old_trailing_chunks(client, populated_index, embedding_server):
    payload = embed_payload()
    for _attempt in range(2):
        response = client.post("/embed", json=payload)
        assert response.status_code == 200
        assert response.json() == {"indexed": 3}
        assert populated_index.vector_count() == 4

    payload["chunks"] = payload["chunks"][:1]
    response = client.post("/embed", json=payload)

    assert response.status_code == 200
    assert response.json() == {"indexed": 1}
    assert set(populated_index._collection.get(include=[])["ids"]) == {"201", "301"}


def test_embed_accepts_more_than_64_chunks_in_one_request(client, populated_index, embedding_server):
    main.app.state.settings.embed_batch_size = 16

    response = client.post("/embed", json=embed_payload(65))

    assert response.status_code == 200
    assert response.json() == {"indexed": 65}
    assert [len(request["input"]) for request in embedding_server["requests"]] == [16, 16, 16, 16, 1]
    assert populated_index.vector_count() == 66


@pytest.mark.parametrize("invalid_part", ["empty", "duplicate", "document_id", "chunk_id", "blank", "heading"])
def test_embed_rejects_invalid_payload_without_model_or_index_changes(
    client, populated_index, embedding_server, invalid_part
):
    payload = embed_payload()
    if invalid_part == "empty":
        payload["chunks"] = []
    elif invalid_part == "duplicate":
        payload["chunks"][1]["chunk_id"] = payload["chunks"][0]["chunk_id"]
    elif invalid_part == "document_id":
        payload["document_id"] = 0
    elif invalid_part == "chunk_id":
        payload["chunks"][0]["chunk_id"] = -1
    elif invalid_part == "blank":
        payload["chunks"][0]["text"] = " \r\n\t"
    else:
        del payload["chunks"][0]["heading_path"]

    response = client.post("/embed", json=payload)

    assert response.status_code == 422
    assert embedding_server["requests"] == []
    assert populated_index.vector_count() == 3


@pytest.mark.parametrize("failure", ["dimensions", "second_batch", "float32_overflow"])
def test_embed_model_failure_preserves_the_previous_index(
    client, populated_index, embedding_server, failure
):
    main.app.state.settings.embed_batch_size = 2
    if failure == "dimensions":
        embedding_server["dimensions"] = 2
    elif failure == "float32_overflow":
        embedding_server["overflow_at"] = 2
    else:
        embedding_server["fail_at"] = 2

    response = client.post("/embed", json=embed_payload())

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "EMBEDDING_UNAVAILABLE"
    assert len(embedding_server["requests"]) == (1 if failure == "dimensions" else 2)
    assert set(populated_index._collection.get(include=[])["ids"]) == {"101", "102", "201"}
    assert "private upstream details" not in response.text


def test_embed_rejects_chunk_ids_owned_by_another_document(client, populated_index, embedding_server):
    payload = embed_payload()
    payload["chunks"][0]["chunk_id"] = 201

    response = client.post("/embed", json=payload)

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "CHUNK_ID_CONFLICT"
    assert set(populated_index._collection.get(include=[])["ids"]) == {"101", "102", "201"}


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_embed_reports_write_failure_and_cleanup_outcome(
    client, populated_index, embedding_server, monkeypatch, cleanup_fails
):
    main.app.state.settings.embed_batch_size = 2
    collection = populated_index._collection
    original_upsert = collection.upsert
    original_delete = collection.delete
    calls = {"upsert": 0, "delete": 0}

    def upsert(**kwargs):
        calls["upsert"] += 1
        if calls["upsert"] == 2:
            raise RuntimeError("private write details")
        return original_upsert(**kwargs)

    def delete(**kwargs):
        calls["delete"] += 1
        if cleanup_fails and calls["delete"] == 2:
            raise RuntimeError("private cleanup details")
        return original_delete(**kwargs)

    monkeypatch.setattr(collection, "upsert", upsert)
    monkeypatch.setattr(collection, "delete", delete)

    response = client.post("/embed", json=embed_payload())

    assert response.status_code == 503
    assert response.json() == {"detail": {
        "error": "INDEX_WRITE_FAILED", "cause": "RuntimeError",
        "cleanup_error": "RuntimeError" if cleanup_fails else None,
    }}
    assert "private" not in response.text
    assert calls == {"upsert": 2, "delete": 2}
    assert populated_index._collection.get(where={"document_id": 22}, include=[])["ids"] == ["201"]
    remaining = populated_index._collection.get(where={"document_id": 11}, include=[])["ids"]
    assert set(remaining) == ({"301", "302"} if cleanup_fails else set())


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_embed_reports_initial_delete_failure_and_cleanup_outcome(
    client, populated_index, embedding_server, monkeypatch, cleanup_fails
):
    collection = populated_index._collection
    original_delete = collection.delete
    delete_calls = []

    def delete(**kwargs):
        delete_calls.append(kwargs)
        if len(delete_calls) == 1:
            original_delete(ids=["101"])
            raise RuntimeError("private initial delete details")
        if cleanup_fails:
            raise OSError("private cleanup details")
        return original_delete(**kwargs)

    monkeypatch.setattr(collection, "delete", delete)

    response = client.post("/embed", json=embed_payload())

    assert response.status_code == 503
    assert response.json() == {"detail": {
        "error": "INDEX_WRITE_FAILED", "cause": "RuntimeError",
        "cleanup_error": "OSError" if cleanup_fails else None,
    }}
    assert "private" not in response.text
    assert delete_calls == [{"where": {"document_id": 11}}] * 2
    assert set(collection.get(include=[])["ids"]) == ({"102", "201"} if cleanup_fails else {"201"})


def test_embed_does_not_call_model_when_index_is_unavailable(client, embedding_server, monkeypatch):
    monkeypatch.setattr(main.app.state.index, "_collection", None)

    response = client.post("/embed", json=embed_payload())

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "INDEX_UNAVAILABLE"
    assert embedding_server["requests"] == []


def test_embed_uses_configured_timeout_and_does_not_retry(client, populated_index, embedding_server):
    main.app.state.settings.embed_batch_size = 2
    main.app.state.settings.embedding_timeout_seconds = 0.25
    embedding_server["timeout_at"] = 2

    response = client.post("/embed", json=embed_payload())

    assert response.status_code == 503
    assert response.json() == {"detail": {"error": "EMBEDDING_UNAVAILABLE", "cause": "ReadTimeout"}}
    assert embedding_server["timeouts"] == [0.25, 0.25]
    assert set(populated_index._collection.get(include=[])["ids"]) == {"101", "102", "201"}
    assert "private timeout details" not in response.text


@pytest.mark.parametrize("operation", ["delete", "reset", "replace"])
def test_index_mutations_do_not_interleave_with_document_replacement(
    client, populated_index, monkeypatch, operation
):
    from app.embedding import EmbeddingChunk

    write_started = Event()
    release_write = Event()
    second_attempted = Event()
    original_lock = populated_index._write_lock
    original_upsert = populated_index._collection.upsert

    class ObservedLock:
        attempts = 0

        def __enter__(self):
            self.attempts += 1
            if self.attempts == 2:
                second_attempted.set()
            original_lock.acquire()

        def __exit__(self, *_exception):
            original_lock.release()

    def paused_upsert(**kwargs):
        write_started.set()
        if not release_write.wait(10):
            raise TimeoutError("test did not release the write")
        return original_upsert(**kwargs)

    monkeypatch.setattr(populated_index, "_write_lock", ObservedLock())
    monkeypatch.setattr(populated_index._collection, "upsert", paused_upsert)
    chunks = [EmbeddingChunk(**chunk) for chunk in embed_payload()["chunks"]]

    def second_mutation():
        if operation == "delete":
            return populated_index.delete_document(11)
        if operation == "reset":
            return populated_index.reset()
        replacement = EmbeddingChunk(chunk_id=401, text="replacement", heading_path="")
        return populated_index.replace_document(11, [replacement], [[1.0, 0.0, 0.0]])

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(populated_index.replace_document, 11, chunks, [[1.0, 0.0, 0.0]] * 3)
        try:
            assert write_started.wait(10)
            second = executor.submit(second_mutation)
            assert second_attempted.wait(10)
            assert not second.done()
        finally:
            release_write.set()
        assert first.result(timeout=10) == 3
        second.result(timeout=10)

    expected = {"delete": {"201"}, "reset": set(), "replace": {"201", "401"}}
    assert set(populated_index._collection.get(include=[])["ids"]) == expected[operation]
