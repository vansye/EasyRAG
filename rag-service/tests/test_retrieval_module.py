"""Public retrieval contracts; all dependencies use temporary, offline fixtures."""

from dataclasses import FrozenInstanceError, asdict
import json
import os
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import WhitespaceSplit
from tokenizers.processors import TemplateProcessing

from app.modules.retrieval import public as retrieval


@pytest.fixture(autouse=True)
def clean_retrieval_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("EMBEDDING_", "EMBED_", "CHUNK_", "CHROMA_")):
            monkeypatch.delenv(name, raising=False)


def test_search_times_index_probe_and_query_separately_from_embedding(module, monkeypatch):
    module.replace(11, (retrieval.IndexChunk(101, "first"),))
    now, calls, timings = [10.0], [], {}
    monkeypatch.setattr(retrieval, "perf_counter", lambda: now[0], raising=False)
    require_index, embed, query = module._require_index, module._embed, module._index.query

    def measured(name, duration, operation):
        def run(*args, **kwargs):
            calls.append(name)
            now[0] += duration
            return operation(*args, **kwargs)
        return run

    monkeypatch.setattr(module, "_require_index", measured("probe", 0.125, require_index))
    monkeypatch.setattr(module, "_embed", measured("embedding", 0.25, embed))
    monkeypatch.setattr(module._index, "query", measured("query", 0.5, query))

    def record(stage, elapsed):
        timings[stage] = timings.get(stage, 0) + elapsed

    hits = module.search("query unchanged?", record_timing=record)
    assert [hit.chunk_id for hit in hits] == [101]
    assert calls == ["probe", "embedding", "query"]
    assert timings == {"vector": 625.0, "embedding": 250.0}


def test_failed_embedding_is_measured_without_querying_the_index(module, monkeypatch):
    now, timings = [10.0], {}
    monkeypatch.setattr(retrieval, "perf_counter", lambda: now[0], raising=False)

    def fail(texts):
        now[0] += 0.25
        raise retrieval.RetrievalUnavailable("embedding", "TimeoutError")

    monkeypatch.setattr(module, "_embed", fail)
    with pytest.raises(retrieval.RetrievalUnavailable) as failure:
        module.search("query", record_timing=timings.__setitem__)
    assert failure.value.component == "embedding"
    assert timings == {"vector": 0.0, "embedding": 250.0}


def test_public_split_preserves_heading_paths_body_and_utf8_offsets():
    assert hasattr(retrieval, "split_markdown"), "retrieval must own its pure splitter"
    text = "序言🙂\r\n# 主标题\r\n简介。\r\n## 子章节\r\n内容。\r\n### 深层\r\n细节。\r\n## 另一节\r\n结束。"
    chunks = retrieval.split_markdown(text, count_tokens=lambda value: len(value) + 2, min_tokens=0)

    assert [chunk.heading_path for chunk in chunks] == [
        "", "主标题", "主标题 > 子章节", "主标题 > 子章节 > 深层", "主标题 > 另一节",
    ]
    assert "".join(chunk.text for chunk in chunks) == text
    assert chunks[0].byte_start == 0
    assert chunks[-1].byte_end == len(text.encode("utf-8"))
    for chunk in chunks:
        assert isinstance(chunk, retrieval.Chunk)
        assert isinstance(chunk, retrieval.ChunkDraft)
        assert text.encode("utf-8")[chunk.byte_start:chunk.byte_end].decode("utf-8") == chunk.text
        expected = chunk.text + ("\n" + chunk.heading_path if chunk.heading_path else "")
        assert chunk.embedding_text == expected
        assert chunk.token_count == len(expected) + 2
    assert all(left.byte_end == right.byte_start for left, right in zip(chunks, chunks[1:]))


@pytest.mark.parametrize("opening, short_closer, closing", [
    ("```python", "``", "```"),
    ("~~~~python", "~~~", "~~~~~"),
    ("````python", "```", "`````"),
])
def test_public_split_keeps_fenced_code_out_of_heading_paths(opening, short_closer, closing):
    text = f"# Root\n{opening}\n# code\n\n{short_closer}\n## still code\n{closing}\n## Next\nBody."
    chunks = retrieval.split_markdown(text, count_tokens=len, min_tokens=0)
    assert [chunk.heading_path for chunk in chunks] == ["Root", "Root > Next"]
    assert "## still code" in chunks[0].text
    assert "".join(chunk.text for chunk in chunks) == text


def test_public_split_keeps_paragraph_sentence_and_unicode_hard_split_rules():
    paragraphs = ["# Topic\n" + "甲" * 20 + "\n\n", "乙" * 20 + "\n\n", "丙" * 20]
    chunks = retrieval.split_markdown("".join(paragraphs), count_tokens=len, max_tokens=45, min_tokens=0)
    assert [chunk.text for chunk in chunks] == paragraphs
    assert all(chunk.heading_path == "Topic" for chunk in chunks)

    sentences = ["甲" * 20 + "。", "乙" * 20 + "！", "丙" * 20 + "？"]
    chunks = retrieval.split_markdown("".join(sentences), count_tokens=len, max_tokens=30, min_tokens=0)
    assert [chunk.text for chunk in chunks] == sentences

    text = "🙂汉" * 90
    chunks = retrieval.split_markdown(text, count_tokens=len, max_tokens=31, min_tokens=0)
    assert len(chunks) > 1
    assert "".join(chunk.text for chunk in chunks) == text
    for chunk in chunks:
        assert chunk.token_count <= 31
        assert text.encode("utf-8")[chunk.byte_start:chunk.byte_end].decode("utf-8") == chunk.text


def test_public_split_merges_small_sections_only_within_embedding_budget():
    text = "# Root\nIntro.\n## A\nA.\n## B\nB.\n"
    chunks = retrieval.split_markdown(text, count_tokens=len, min_tokens=64)
    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].heading_path == "Root"
    assert retrieval.split_markdown(" \t\r\n", count_tokens=len) == []
    with pytest.raises(ValueError, match="heading|max_tokens"):
        retrieval.split_markdown("# " + "长标题" * 20 + "\n正文", count_tokens=len, max_tokens=20, min_tokens=0)


def test_settings_keep_service_root_defaults_after_rehoming():
    assert hasattr(retrieval, "RetrievalSettings"), "retrieval must own its settings"
    settings = retrieval.RetrievalSettings(_env_file=None, chunk_tokenizer_path="data/toy.json")
    service_dir = Path(__file__).resolve().parents[1]
    assert settings.chroma_dir == service_dir / "data/chroma"
    assert settings.chunk_tokenizer_path == service_dir / "data/toy.json"
    assert Path(settings.model_config["env_file"]) == service_dir / ".env"
    assert settings.collection_name == "easyrag_bge-m3_1024"


def test_settings_isolate_storage_and_keep_provider_and_secret_validation(tmp_path):
    settings = retrieval.RetrievalSettings(
        _env_file=None, chroma_dir=tmp_path / "chroma", embedding_model="Qwen/Qwen3:latest",
        embedding_api_key="private-credential", embedding_dim=3,
    )
    assert settings.chroma_dir == tmp_path / "chroma"
    assert settings.collection_name == "easyrag_Qwen-Qwen3-latest_3"
    assert "private-credential" not in repr(settings)
    with pytest.raises(ValidationError) as error:
        retrieval.RetrievalSettings(_env_file=None, embedding_api_key=["private-credential"])
    assert "private-credential" not in str(error.value)


@pytest.mark.parametrize("overrides", [
    {"embedding_dim": 0}, {"chunk_min_tokens": 65, "chunk_max_tokens": 64},
    {"embed_batch_size": 0}, {"embedding_timeout_seconds": float("inf")},
])
def test_settings_reject_invalid_embedding_and_chunk_limits(overrides):
    with pytest.raises(ValidationError):
        retrieval.RetrievalSettings(_env_file=None, **overrides)


@pytest.fixture
def tokenizer_file(tmp_path):
    tokenizer = Tokenizer(WordLevel({"[UNK]": 0, "[CLS]": 1, "[SEP]": 2}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = WhitespaceSplit()
    tokenizer.post_processor = TemplateProcessing(
        single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 1), ("[SEP]", 2)],
    )
    tokenizer.enable_truncation(max_length=2)
    tokenizer.enable_padding(length=64, pad_id=0, pad_token="[UNK]")
    path = tmp_path / "toy-tokenizer.json"
    tokenizer.save(str(path))
    return path


@pytest.fixture
def settings(tmp_path, tokenizer_file):
    return retrieval.RetrievalSettings(
        _env_file=None, chroma_dir=tmp_path / "chroma", chunk_tokenizer_path=tokenizer_file,
        chunk_min_tokens=0, embedding_model="test-embedding", embedding_dim=3,
        embedding_base_url="https://embedding.test", embedding_api_key="private-credential",
        embed_batch_size=2, embedding_timeout_seconds=0.25,
    )


@pytest.fixture
def offline_embedding(monkeypatch):
    state = {"posts": [], "gets": [], "fail_at": None, "invalid_at": None,
             "invalid_payload": None, "models_payload": None, "models_status": 200}
    original_client = httpx.Client

    def respond(request):
        if request.method == "GET":
            state["gets"].append(request)
            payload = state["models_payload"]
            if payload is None:
                payload = ({"data": [{"id": "test-embedding"}]} if request.url.path.endswith("/models")
                           else {"models": [{"name": "test-embedding:latest"}]})
            return httpx.Response(state["models_status"], json=payload)
        state["posts"].append(request)
        if len(state["posts"]) == state["fail_at"]:
            return httpx.Response(503, text="private upstream body and credentials")
        if len(state["posts"]) == state["invalid_at"]:
            return httpx.Response(200, content=json.dumps(state["invalid_payload"]))
        payload = json.loads(request.content)
        known = {"first": [1.0, 0.0, 0.0], "second": [0.9, 0.1, 0.0], "other": [0.0, 0.0, 1.0]}
        vectors = [known.get(text.split("\n")[0], [1.0, 0.0, 0.0]) for text in payload["input"]]
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": list(reversed([
                {"index": index, "embedding": vector} for index, vector in enumerate(vectors)
            ]))})
        return httpx.Response(200, json={"embeddings": vectors})

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    return state


@pytest.fixture
def module(settings, offline_embedding):
    assert hasattr(retrieval, "Retrieval"), "retrieval must expose its module facade"
    instance = retrieval.Retrieval(settings)
    yield instance
    instance.close()


def test_split_uses_local_tokenizer_without_padding_truncation_or_document_title(module, settings):
    settings.chunk_max_tokens = 9
    content = "# 标题\r\n" + "word 🙂 " * 80
    drafts = module.split(content, "must not enter body or heading path")
    tokenizer = Tokenizer.from_file(str(settings.chunk_tokenizer_path))
    tokenizer.no_truncation()
    tokenizer.no_padding()
    expected = retrieval.split_markdown(
        content, count_tokens=lambda text: len(tokenizer.encode(text, add_special_tokens=True).ids),
        max_tokens=9, min_tokens=0,
    )
    assert isinstance(drafts, tuple)
    assert [asdict(chunk) for chunk in drafts] == [asdict(chunk) for chunk in expected]
    assert len(drafts) > 1
    assert "".join(chunk.text for chunk in drafts) == content
    assert all(3 <= chunk.token_count <= 9 for chunk in drafts)
    assert all(chunk.heading_path == "标题" for chunk in drafts)


def test_replace_search_and_inspect_return_stable_ids_and_exact_plain_snapshots(module, offline_embedding):
    chunks = (
        retrieval.IndexChunk(101, "first", "Guide > A", ("RAG", "中文")),
        retrieval.IndexChunk(102, "second", "Guide > B", ()),
    )
    assert module.replace(11, chunks) == 2
    assert module.replace(22, (retrieval.IndexChunk(201, "other", "", ("tag",)),)) == 1
    expected = (
        retrieval.IndexEntry(101, 11, 0, "first", "Guide > A", ("RAG", "中文")),
        retrieval.IndexEntry(102, 11, 1, "second", "Guide > B", ()),
        retrieval.IndexEntry(201, 22, 0, "other", "", ("tag",)),
    )
    assert module.inspect() == expected
    assert module.replace(11, chunks) == 2
    assert module.inspect() == expected
    hits = module.search("query unchanged?", top_k=3)
    assert isinstance(hits, tuple)
    assert [hit.chunk_id for hit in hits] == [101, 102, 201]
    assert hits[0] == retrieval.SearchHit(101, 11, "first", "Guide > A", pytest.approx(1.0))
    assert hits[0].score >= hits[1].score >= hits[2].score
    assert len(module.search("query unchanged?", top_k=1)) == 1
    assert json.loads(offline_embedding["posts"][0].content)["input"] == [
        "first\nGuide > A", "second\nGuide > B",
    ]
    assert json.loads(offline_embedding["posts"][-1].content)["input"] == ["query unchanged?"]
    with pytest.raises(FrozenInstanceError):
        hits[0].text = "mutated"
    assert set(asdict(expected[0])) == {"chunk_id", "document_id", "seq", "text", "heading_path", "tags"}


def test_replacement_removes_obsolete_ids_but_preserves_other_documents(module):
    module.replace(11, (retrieval.IndexChunk(101, "first"), retrieval.IndexChunk(102, "second")))
    module.replace(22, (retrieval.IndexChunk(201, "other"),))
    assert module.replace(11, (retrieval.IndexChunk(101, "updated", "New"),)) == 1
    assert module.inspect() == (
        retrieval.IndexEntry(101, 11, 0, "updated", "New", ()),
        retrieval.IndexEntry(201, 22, 0, "other", "", ()),
    )


def test_delete_is_idempotent_and_reset_preserves_other_model_collections(module, settings):
    module.replace(11, (retrieval.IndexChunk(101, "first"), retrieval.IndexChunk(102, "second")))
    module.replace(22, (retrieval.IndexChunk(201, "other"),))
    comparison = retrieval.Retrieval(settings.model_copy(update={"embedding_model": "other-model"}))
    try:
        comparison.replace(11, (retrieval.IndexChunk(101, "comparison"),))
        assert module.delete_document(11) == 2
        assert module.delete_document(11) == 0
        assert module.delete_document(999) == 0
        assert [entry.chunk_id for entry in module.inspect()] == [201]
        module.reset()
        module.reset()
        assert module.inspect() == ()
        assert module.search("anything") == ()
        assert [entry.chunk_id for entry in comparison.inspect()] == [101]
    finally:
        comparison.close()


def test_rebuild_with_empty_tags_clears_old_arrays_and_preserves_other_collections(module, settings):
    comparison = retrieval.Retrieval(settings.model_copy(update={"embedding_model": "other-model"}))
    try:
        comparison.replace(22, (retrieval.IndexChunk(201, "other", tags=("keep", "中文")),))
        comparison_before = comparison.inspect()
        module.replace(11, (retrieval.IndexChunk(101, "first", tags=("old", "中文")),))
        module.reset()
        assert module.inspect() == ()
        assert module.replace(11, (retrieval.IndexChunk(101, "first"),)) == 1
        assert comparison.inspect() == comparison_before
        assert module.inspect() == (retrieval.IndexEntry(101, 11, 0, "first", "", ()),)
    finally:
        comparison.close()


def test_duplicate_and_cross_document_ids_cannot_mutate_an_existing_index(module, offline_embedding):
    module.replace(11, (retrieval.IndexChunk(101, "first"),))
    before = module.inspect()
    offline_embedding["posts"].clear()
    with pytest.raises(ValueError, match="unique"):
        module.replace(22, (retrieval.IndexChunk(201, "new"), retrieval.IndexChunk(201, "duplicate")))
    assert offline_embedding["posts"] == []
    with pytest.raises(retrieval.ChunkIdConflict):
        module.replace(22, (retrieval.IndexChunk(101, "conflict"),))
    assert module.inspect() == before


@pytest.mark.parametrize("document_id", [0, -1, True, "11", 2**63])
def test_invalid_document_ids_are_rejected_before_embedding_or_mutation(module, offline_embedding, document_id):
    with pytest.raises(ValueError, match="document_id"):
        module.replace(document_id, (retrieval.IndexChunk(101, "first"),))
    with pytest.raises(ValueError, match="document_id"):
        module.delete_document(document_id)
    assert module.inspect() == ()
    assert offline_embedding["posts"] == []


@pytest.mark.parametrize("chunks", [(), []])
def test_empty_replacement_is_rejected_before_mutation(module, offline_embedding, chunks):
    with pytest.raises(ValueError, match="chunks"):
        module.replace(11, chunks)
    assert offline_embedding["posts"] == []


def test_invalid_search_is_rejected_before_embedding(module, offline_embedding):
    for query, top_k in [(" \t", 5), ("query", 0), ("query", -1)]:
        with pytest.raises(ValueError):
            module.search(query, top_k=top_k)
    assert offline_embedding["posts"] == []


@pytest.mark.parametrize("provider", ["ollama", "openai", "deepseek"])
def test_embedding_protocol_keeps_batching_input_order_and_configured_timeout(module, settings, offline_embedding, provider):
    settings.embedding_provider = provider
    module.replace(11, tuple(retrieval.IndexChunk(101 + index, text) for index, text in enumerate([
        "first", "second", "other",
    ])))
    assert [hit.chunk_id for hit in module.search("query", top_k=3)] == [101, 102, 103]
    posts = offline_embedding["posts"]
    assert [len(json.loads(request.content)["input"]) for request in posts] == [2, 1, 1]
    for request in posts:
        payload = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer private-credential"
        assert request.extensions["timeout"]["read"] == 0.25
        if provider == "ollama":
            assert request.url.path == "/api/embed"
            assert payload["truncate"] is False
        else:
            assert request.url.path == "/v1/embeddings"
            assert payload["encoding_format"] == "float"
            assert "truncate" not in payload


def test_later_embedding_failure_preserves_previous_index_without_retry(module, offline_embedding):
    module.replace(11, (retrieval.IndexChunk(101, "first"),))
    before = module.inspect()
    offline_embedding["posts"].clear()
    offline_embedding["fail_at"] = 2
    with pytest.raises(retrieval.RetrievalUnavailable) as error:
        module.replace(11, tuple(retrieval.IndexChunk(301 + index, "replacement") for index in range(5)))
    assert error.value.component == "embedding"
    assert error.value.cause == "HTTPStatusError"
    assert "private" not in str(error.value)
    assert "private" not in repr(vars(error.value))
    assert len(offline_embedding["posts"]) == 2
    assert module.inspect() == before


@pytest.mark.parametrize("payload", [
    None, [], {}, {"embeddings": []}, {"embeddings": "private"},
    {"embeddings": [[1, 2]]}, {"embeddings": [[True, 0, 1]]},
    {"embeddings": [["private", 0, 1]]}, {"embeddings": [[float("nan"), 0, 1]]},
    {"embeddings": [[float("inf"), 0, 1]]}, {"embeddings": [[1e40, 0, 1]]},
])
def test_invalid_embedding_vectors_never_replace_previous_index(module, offline_embedding, payload):
    module.replace(11, (retrieval.IndexChunk(101, "first"),))
    before = module.inspect()
    offline_embedding["posts"].clear()
    offline_embedding["invalid_at"] = 1
    offline_embedding["invalid_payload"] = payload
    with pytest.raises(retrieval.RetrievalUnavailable) as error:
        module.replace(11, (retrieval.IndexChunk(301, "replacement"),))
    assert error.value.component == "embedding"
    assert error.value.cause == "EmbeddingResponseError"
    assert "private" not in str(error.value)
    assert module.inspect() == before


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_partial_write_reports_confirmed_cleanup_or_remaining_entries(module, monkeypatch, cleanup_fails):
    module.replace(11, (retrieval.IndexChunk(101, "first"),))
    module.replace(22, (retrieval.IndexChunk(201, "other"),))
    collection = module._index._collection
    original_upsert, original_delete = collection.upsert, collection.delete
    calls = {"upsert": 0, "delete": 0}

    def upsert(**kwargs):
        calls["upsert"] += 1
        if calls["upsert"] == 2:
            raise RuntimeError("private write details")
        return original_upsert(**kwargs)

    def delete(**kwargs):
        calls["delete"] += 1
        if cleanup_fails and calls["delete"] == 2:
            raise OSError("private cleanup details")
        return original_delete(**kwargs)

    monkeypatch.setattr(collection, "upsert", upsert)
    monkeypatch.setattr(collection, "delete", delete)
    with pytest.raises(retrieval.IndexWriteError) as error:
        module.replace(11, tuple(retrieval.IndexChunk(301 + index, "replacement") for index in range(3)))
    assert error.value.cause == "RuntimeError"
    assert error.value.cleanup_error == ("OSError" if cleanup_fails else None)
    assert error.value.cleanup_confirmed is (not cleanup_fails)
    assert "private" not in str(error.value)
    assert "private" not in repr(vars(error.value))
    assert calls == {"upsert": 2, "delete": 2}
    assert {entry.chunk_id for entry in module.inspect()} == ({201, 301, 302} if cleanup_fails else {201})


@pytest.mark.parametrize("changed_metadata", [
    {"embedding_model": "different", "embedding_dim": 3},
    {"embedding_model": "test-embedding", "embedding_dim": 17},
])
def test_metadata_mismatch_leaves_module_unavailable_and_index_untouched(module, settings, changed_metadata):
    module.replace(11, (retrieval.IndexChunk(101, "first"),))
    before = module.inspect()
    module._index._collection.modify(metadata=changed_metadata)
    reopened = retrieval.Retrieval(settings)
    try:
        status = reopened.runtime_info()
        assert status["status"] == "DOWN"
        assert status["chroma"]["error"] == "IndexMetadataMismatch"
        with pytest.raises(retrieval.RetrievalUnavailable):
            reopened.inspect()
        assert module.inspect() == before
    finally:
        reopened.close()


def test_runtime_info_is_local_and_health_checks_owned_dependencies(module, offline_embedding):
    runtime = module.runtime_info()
    assert runtime["embedding"] == {"provider": "ollama", "model": "test-embedding", "dim": 3}
    assert runtime["status"] == "UP"
    assert runtime["chroma"]["vectors"] == 0
    assert offline_embedding["gets"] == offline_embedding["posts"] == []
    health = module.health()
    assert health["status"] == "UP"
    assert health["chroma"]["status"] == "UP"
    assert health["embedding"]["model_available"] is True
    assert health["tokenizer"]["status"] == "UP"
    assert len(offline_embedding["gets"]) == 1
    assert "private" not in json.dumps(health)


@pytest.mark.parametrize("payload", [[], {"models": None}, {"models": ["private response"]}])
def test_health_reports_malformed_model_lists_without_exposing_upstream_body(module, offline_embedding, payload):
    offline_embedding["models_payload"] = payload
    health = module.health()
    assert health["status"] == "DOWN"
    assert health["embedding"]["status"] == "DOWN"
    assert health["embedding"]["error"] == "BAD_RESPONSE"
    assert "private" not in json.dumps(health)


def test_missing_tokenizer_is_safe_and_does_not_disable_readonly_index_operations(module, settings, tmp_path):
    settings.chunk_tokenizer_path = tmp_path / "private-missing-tokenizer.json"
    with pytest.raises(retrieval.RetrievalUnavailable) as error:
        module.split("content", "title")
    assert error.value.component == "tokenizer"
    assert "private" not in str(error.value)
    assert module.inspect() == ()
    assert module.health()["tokenizer"]["status"] == "DOWN"


def test_unavailable_index_fails_before_embedding_and_reports_a_safe_local_snapshot(module, offline_embedding, monkeypatch):
    monkeypatch.setattr(module._index, "_collection", None)
    monkeypatch.setattr(module._index, "chroma_error", "PermissionError")
    with pytest.raises(retrieval.RetrievalUnavailable) as error:
        module.replace(11, (retrieval.IndexChunk(101, "first"),))
    assert error.value.component == "index"
    assert offline_embedding["posts"] == []
    assert module.runtime_info()["chroma"]["error"] == "PermissionError"


def test_close_is_idempotent_without_deleting_persisted_entries(module, settings):
    module.replace(11, (retrieval.IndexChunk(101, "first"),))
    module.close()
    module.close()
    with pytest.raises(retrieval.RetrievalUnavailable):
        module.inspect()
    reopened = retrieval.Retrieval(settings)
    try:
        assert reopened.inspect() == (retrieval.IndexEntry(101, 11, 0, "first", "", ()),)
    finally:
        reopened.close()


def test_inspect_accepts_legacy_list_metadata_and_returns_detached_tuple_tags(module):
    module._index._collection.add(
        ids=["101", "102"], embeddings=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        documents=["legacy body", "untagged body"], metadatas=[
            {"document_id": 11, "seq": 0, "heading_path": "Root", "tags": ["RAG", "中文"]},
            {"document_id": 11, "seq": 1, "heading_path": ""},
        ],
    )
    assert module.inspect() == (
        retrieval.IndexEntry(101, 11, 0, "legacy body", "Root", ("RAG", "中文")),
        retrieval.IndexEntry(102, 11, 1, "untagged body", "", ()),
    )
    tags = ["first"]
    chunk = retrieval.IndexChunk(201, "text", tags=tags)
    tags.append("later")
    assert chunk.tags == ("first",)


def test_chroma_initialization_fault_is_local_and_sanitized(settings, tmp_path, offline_embedding):
    settings.chroma_dir = tmp_path / "not-a-directory"
    settings.chroma_dir.write_text("private content", encoding="utf-8")
    instance = retrieval.Retrieval(settings)
    try:
        assert instance.runtime_info()["chroma"]["status"] == "DOWN"
        assert instance.runtime_info()["chroma"]["error"] == "FileExistsError"
        with pytest.raises(retrieval.RetrievalUnavailable):
            instance.inspect()
        assert instance.split("readable text", "title")
        assert "private" not in json.dumps(instance.health())
    finally:
        instance.close()


def test_invalid_configuration_constructs_an_unavailable_module_without_reading_user_config(monkeypatch):
    def invalid_settings():
        raise ValueError("private configuration input")

    monkeypatch.setattr(retrieval, "RetrievalSettings", invalid_settings)
    instance = retrieval.Retrieval()
    try:
        assert instance.runtime_info()["status"] == "DOWN"
        assert "private" not in json.dumps(instance.health())
        with pytest.raises(retrieval.RetrievalUnavailable) as error:
            instance.inspect()
        assert error.value.component == "configuration"
        assert "private" not in str(error.value)
    finally:
        instance.close()


@pytest.mark.parametrize("identifier, metadata", [
    ("0101", {"document_id": 11, "seq": 0}),
    ("101", {"document_id": True, "seq": 0}),
    ("101", {"document_id": 11, "seq": True}),
    ("101", {"document_id": 11}),
    ("101", {"document_id": 11, "seq": 0, "heading_path": 17}),
    ("101", {"document_id": 11, "seq": 0, "tags": "not-a-list"}),
])
def test_inspect_rejects_unfaithful_snapshots_without_modifying_the_index(module, identifier, metadata):
    collection = module._index._collection
    collection.add(ids=[identifier], embeddings=[[1, 0, 0]], documents=["body"], metadatas=[metadata])
    before = collection.get(include=["documents", "metadatas"])
    with pytest.raises(retrieval.RetrievalUnavailable) as error:
        module.inspect()
    assert error.value.component == "index"
    assert collection.get(include=["documents", "metadatas"]) == before


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_initial_delete_failure_also_reports_its_cleanup_outcome(module, monkeypatch, cleanup_fails):
    module.replace(11, (retrieval.IndexChunk(101, "first"), retrieval.IndexChunk(102, "second")))
    module.replace(22, (retrieval.IndexChunk(201, "other"),))
    collection = module._index._collection
    original_delete = collection.delete
    calls = []

    def delete(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            original_delete(ids=["101"])
            raise RuntimeError("private initial delete details")
        if cleanup_fails:
            raise OSError("private cleanup details")
        return original_delete(**kwargs)

    monkeypatch.setattr(collection, "delete", delete)
    with pytest.raises(retrieval.IndexWriteError) as error:
        module.replace(11, (retrieval.IndexChunk(301, "replacement"),))
    assert error.value.cause == "RuntimeError"
    assert error.value.cleanup_error == ("OSError" if cleanup_fails else None)
    assert error.value.cleanup_confirmed is (not cleanup_fails)
    assert calls == [{"where": {"document_id": 11}}] * 2
    assert {entry.chunk_id for entry in module.inspect()} == ({102, 201} if cleanup_fails else {201})


@pytest.mark.parametrize("operation, method", [
    ("inspect", "get"), ("delete_document", "delete"), ("reset", "delete_collection"),
])
def test_runtime_index_errors_are_sanitized(module, monkeypatch, operation, method):
    def unavailable(*_args, **_kwargs):
        raise OSError("private storage path and credentials")

    target = module._index._client if operation == "reset" else module._index._collection
    monkeypatch.setattr(target, method, unavailable)
    with pytest.raises(retrieval.RetrievalUnavailable) as error:
        getattr(module, operation)(11) if operation == "delete_document" else getattr(module, operation)()
    assert error.value.component == "index"
    assert error.value.cause == "OSError"
    assert "private" not in str(error.value)


@pytest.mark.parametrize("content, title", [(" ", "title"), ("\ud800", "title"), ("content", "\udfff")])
def test_invalid_split_input_is_rejected_without_tokenizer_fallback(module, settings, tmp_path, content, title):
    settings.chunk_tokenizer_path = tmp_path / "missing.json"
    with pytest.raises(ValueError):
        module.split(content, title)


@pytest.mark.parametrize("indices", [[0, 0], [0, 2], [False, 1], [0, None]])
def test_compatible_embedding_rejects_invalid_indices_before_mutation(module, settings, offline_embedding, indices):
    settings.embedding_provider = "openai"
    offline_embedding["invalid_at"] = 1
    offline_embedding["invalid_payload"] = {"data": [
        {"index": index, "embedding": [1, 0, 0]} for index in indices
    ]}
    with pytest.raises(retrieval.RetrievalUnavailable) as error:
        module.replace(11, (retrieval.IndexChunk(101, "first"), retrieval.IndexChunk(102, "second")))
    assert error.value.cause == "EmbeddingResponseError"
    assert module.inspect() == ()


@pytest.mark.parametrize("fault", ["model", "metadata_and_physical_dimension", "physical_dimension"])
def test_explicit_reset_recovers_a_mismatched_collection_with_a_fresh_handle(
    module, settings, offline_embedding, fault,
):
    client = module._index._client
    client.delete_collection(settings.collection_name)
    dimensions = 3 if fault == "model" else 17
    old_collection = client.create_collection(
        settings.collection_name,
        metadata={"hnsw:space": "cosine",
                  "embedding_model": "old-model" if fault == "model" else settings.embedding_model,
                  "embedding_dim": 17 if fault == "metadata_and_physical_dimension" else 3},
    )
    old_collection.add(
        ids=["101"], embeddings=[[1.0] + [0.0] * (dimensions - 1)], documents=["old body"],
        metadatas=[{"document_id": 11, "seq": 0, "heading_path": "Old", "tags": ["legacy"]}],
    )
    previous_id = old_collection.id
    module.close()
    reopened = retrieval.Retrieval(settings)
    comparison = retrieval.Retrieval(settings.model_copy(update={"embedding_model": "comparison-model"}))
    try:
        comparison.replace(22, (retrieval.IndexChunk(201, "other"),))
        comparison_before = comparison.inspect()
        if fault != "physical_dimension":
            assert reopened.runtime_info()["chroma"]["error"] == "IndexMetadataMismatch"
            with pytest.raises(retrieval.RetrievalUnavailable):
                reopened.inspect()
        existing = reopened._index._client.get_collection(settings.collection_name)
        assert existing.get(include=["documents"])["documents"] == ["old body"]

        offline_embedding["posts"].clear()
        offline_embedding["gets"].clear()
        reopened.reset()

        assert offline_embedding["posts"] == offline_embedding["gets"] == []
        assert reopened.runtime_info()["chroma"]["status"] == "UP"
        assert reopened._index._collection.id != previous_id
        assert reopened._index._collection.metadata == {
            "hnsw:space": "cosine", "embedding_model": settings.embedding_model, "embedding_dim": 3,
        }
        assert reopened.inspect() == ()
        assert comparison.inspect() == comparison_before
        assert reopened.replace(11, (retrieval.IndexChunk(101, "first", "Current", ("RAG",)),)) == 1
        expected = (retrieval.IndexEntry(101, 11, 0, "first", "Current", ("RAG",)),)
        assert reopened.inspect() == expected
        hits = reopened.search("query", top_k=1)
        assert hits == (retrieval.SearchHit(101, 11, "first", "Current", pytest.approx(1.0)),)
        reopened.close()
        reopened.close()
        reopened = retrieval.Retrieval(settings)
        assert reopened.inspect() == expected
    finally:
        reopened.close()
        comparison.close()


def test_reset_creation_failure_discards_the_stale_handle_and_allows_explicit_recovery(module, monkeypatch):
    module.replace(11, (retrieval.IndexChunk(101, "first"),))

    def fail_create(*_args, **_kwargs):
        raise OSError("private collection creation details")

    with monkeypatch.context() as failure:
        failure.setattr(module._index._client, "create_collection", fail_create)
        with pytest.raises(retrieval.RetrievalUnavailable) as error:
            module.reset()
        assert error.value.component == "index"
        assert error.value.cause == "OSError"
        assert "private" not in str(error.value)
        assert module.runtime_info()["chroma"]["status"] == "DOWN"
        with pytest.raises(retrieval.RetrievalUnavailable):
            module.inspect()
    module.reset()
    assert module.inspect() == ()
    assert module.replace(11, (retrieval.IndexChunk(101, "first"),)) == 1
    assert module.search("query")[0].chunk_id == 101


def test_explicit_reset_can_open_the_configured_store_after_initialization_recovers(settings, offline_embedding):
    settings.chroma_dir.write_text("temporary blocker", encoding="utf-8")
    instance = retrieval.Retrieval(settings)
    try:
        assert instance.runtime_info()["chroma"]["error"] == "FileExistsError"
        settings.chroma_dir.unlink()
        instance.reset()
        assert instance.runtime_info()["chroma"]["status"] == "UP"
        assert instance.replace(11, (retrieval.IndexChunk(101, "first"),)) == 1
        assert instance.inspect() == (retrieval.IndexEntry(101, 11, 0, "first", "", ()),)
    finally:
        instance.close()
