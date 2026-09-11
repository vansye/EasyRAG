"""正式切片接口与 Java 共用 UTF-8 偏移样例，不依赖在线模型或业务数据库。"""

import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import WhitespaceSplit
from tokenizers.processors import TemplateProcessing

from app import config, main
from app.config import Settings


SERVICE_DIR = Path(__file__).resolve().parents[1]
CONTRACT_PATH = SERVICE_DIR.parent / "server/src/test/resources/contracts/utf8-offsets.json"
CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def clean_chunk_env(monkeypatch):
    for variable in list(os.environ):
        if variable.startswith(("EMBEDDING_", "EMBED_", "CHUNK_")):
            monkeypatch.delenv(variable, raising=False)


@pytest.fixture
def tokenizer_file(tmp_path):
    tokenizer = Tokenizer(WordLevel({"[UNK]": 0, "[CLS]": 1, "[SEP]": 2}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = WhitespaceSplit()
    tokenizer.post_processor = TemplateProcessing(
        single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 1), ("[SEP]", 2)],
    )
    tokenizer.enable_truncation(max_length=2)
    tokenizer.enable_padding(length=64, pad_id=0, pad_token="[UNK]")
    path = tmp_path / "test-tokenizer.json"
    tokenizer.save(str(path))
    return path


def settings_for_test(tokenizer_path, **overrides):
    values = {"embedding_model": "test-embedding", "embedding_dim": 3,
              "chunk_tokenizer_path": tokenizer_path, "chunk_min_tokens": 0}
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def client(tmp_path, monkeypatch, tokenizer_file):
    settings = settings_for_test(tokenizer_file)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "index")
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    with TestClient(main.app, raise_server_exceptions=False) as api:
        yield api


def chunk_payload(text="正文🙂"):
    return {"document_id": 11, "text": text, "title": "不应插入正文或标题路径的文档标题"}


def expected_tokenizer(tokenizer_file):
    tokenizer = Tokenizer.from_file(str(tokenizer_file))
    tokenizer.no_truncation()
    tokenizer.no_padding()
    return tokenizer


@pytest.mark.parametrize("case", CONTRACT["cases"], ids=lambda case: case["name"])
def test_chunk_matches_shared_utf8_contract(client, tokenizer_file, case):
    assert CONTRACT["offset_unit"] == "utf8_bytes"
    assert CONTRACT["interval"] == "[start,end)"

    response = client.post("/chunk", json=chunk_payload(case["text"]))

    assert response.status_code == 200, response.text
    chunks = response.json()["chunks"]
    assert [{key: value for key, value in chunk.items() if key != "token_count"}
            for chunk in chunks] == case["chunks"]
    assert "".join(chunk["text"] for chunk in chunks) == case["text"]
    tokenizer = expected_tokenizer(tokenizer_file)
    for chunk in chunks:
        source = case["text"].encode("utf-8")
        assert source[chunk["byte_start"]:chunk["byte_end"]].decode("utf-8") == chunk["text"]
        embedding_text = chunk["text"] + ("\n" + chunk["heading_path"] if chunk["heading_path"] else "")
        assert chunk["token_count"] == len(tokenizer.encode(embedding_text, add_special_tokens=True).ids)


def test_chunk_enforces_real_token_budget_without_truncation_or_padding(client, tokenizer_file):
    main.app.state.settings.chunk_max_tokens = 9
    text = "# 标题\r\n" + "word 🙂 " * 80

    response = client.post("/chunk", json=chunk_payload(text))

    assert response.status_code == 200, response.text
    chunks = response.json()["chunks"]
    assert len(chunks) > 1
    assert "".join(chunk["text"] for chunk in chunks) == text
    tokenizer = expected_tokenizer(tokenizer_file)
    for sequence, chunk in enumerate(chunks):
        assert chunk["seq"] == sequence
        assert 3 <= chunk["token_count"] <= 9
        assert chunk["heading_path"] == "标题"
        assert chunk["token_count"] == len(tokenizer.encode(chunk["text"] + "\n标题").ids)
        assert text.encode("utf-8")[chunk["byte_start"]:chunk["byte_end"]].decode("utf-8") == chunk["text"]


@pytest.mark.parametrize("changes", [
    {"document_id": 0}, {"document_id": -1}, {"document_id": 2**63},
    {"document_id": True}, {"document_id": "11"},
    {"text": ""}, {"text": " \t\r\n"}, {"text": None}, {"text": 17},
    {"title": None}, {"title": []},
])
def test_chunk_rejects_invalid_fields_before_loading_tokenizer(client, tmp_path, changes):
    main.app.state.settings = settings_for_test(tmp_path / "missing-private-tokenizer.json")
    payload = chunk_payload()
    payload.update(changes)

    response = client.post("/chunk", json=payload)

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


@pytest.mark.parametrize("field", ["document_id", "text", "title"])
def test_chunk_requires_declared_request_fields(client, field):
    payload = chunk_payload()
    del payload[field]
    assert client.post("/chunk", json=payload).status_code == 422


def test_chunk_accepts_full_positive_bigint_range(client):
    payload = chunk_payload()
    payload["document_id"] = 2**63 - 1
    assert client.post("/chunk", json=payload).status_code == 200


@pytest.mark.parametrize("text", ["\ud800", "正文\udfff尾部"])
@pytest.mark.parametrize("field", ["text", "title"])
def test_chunk_rejects_unpaired_surrogates_without_echoing_them(client, tmp_path, text, field):
    main.app.state.settings = settings_for_test(tmp_path / "missing-private-tokenizer.json")
    payload = chunk_payload()
    payload[field] = text
    response = client.post(
        "/chunk", content=json.dumps(payload), headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": {"error": "INVALID_UTF8_TEXT"}}


@pytest.mark.parametrize("failure", ["missing", "invalid"])
def test_chunk_reports_unavailable_tokenizer_without_exposing_paths(client, tmp_path, failure):
    path = tmp_path / "private-tokenizer.json"
    if failure == "invalid":
        path.write_text("private invalid tokenizer content", encoding="utf-8")
    main.app.state.settings = settings_for_test(path)

    response = client.post("/chunk", json=chunk_payload())

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "TOKENIZER_UNAVAILABLE"
    assert response.json()["detail"]["cause"]
    assert "private" not in response.text


def test_chunk_reports_tokenizer_encoding_failure_without_character_count_fallback(client, tmp_path):
    path = tmp_path / "private-broken-vocabulary.json"
    Tokenizer(WordLevel({"known": 0}, unk_token="[MISSING]")).save(str(path))
    main.app.state.settings = settings_for_test(path)

    response = client.post("/chunk", json=chunk_payload())

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "TOKENIZER_UNAVAILABLE"
    assert "private" not in response.text
    assert "MISSING" not in response.text


def test_chunk_reports_heading_that_cannot_fit_budget_without_truncation(client):
    main.app.state.settings.chunk_max_tokens = 3

    response = client.post("/chunk", json=chunk_payload("# 标题\n正文"))

    assert response.status_code == 422
    assert response.json() == {"detail": {"error": "CHUNK_TOKEN_LIMIT_EXCEEDED"}}


def test_chunk_does_not_require_embedding_or_index_availability(client, monkeypatch):
    monkeypatch.setattr(main.app.state.index, "_collection", None)

    def reject_embedding(*_args, **_kwargs):
        pytest.fail("/chunk must not call embedding")

    monkeypatch.setattr(main, "embed_texts", reject_embedding)
    response = client.post("/chunk", json=chunk_payload())

    assert response.status_code == 200
    assert response.json()["chunks"][0]["text"] == "正文🙂"
    assert main.app.state.index._collection is None
    assert main.app.state.index.bm25.size == 0


def test_missing_tokenizer_does_not_prevent_startup_or_health(tmp_path, monkeypatch):
    settings = settings_for_test(tmp_path / "missing-private-tokenizer.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "index")
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    async def offline_embedding(_settings):
        return {"status": "DOWN", "reachable": False, "model_available": False}

    monkeypatch.setattr(main, "_probe_embedding", offline_embedding)
    with TestClient(main.app, raise_server_exceptions=False) as api:
        response = api.post("/chunk", json=chunk_payload())
        assert response.status_code == 503
        health = api.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "UP"


def test_equal_normalized_hash_does_not_allow_reusing_offsets_after_content_change(client):
    versions = {case["name"]: case["text"] for case in CONTRACT["cases"]}
    previous = versions["mixed_crlf"]
    updated = versions["mixed_lf"]

    def normalized_hash(text):
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    assert normalized_hash(previous) == normalized_hash(updated)
    previous_response = client.post("/chunk", json=chunk_payload(previous))
    updated_response = client.post("/chunk", json=chunk_payload(updated))
    assert previous_response.status_code == updated_response.status_code == 200
    previous_chunks = previous_response.json()["chunks"]
    updated_chunks = updated_response.json()["chunks"]
    assert previous_chunks[-1]["byte_end"] > len(updated.encode("utf-8"))
    assert updated_chunks[-1]["byte_end"] == len(updated.encode("utf-8"))
    assert "".join(chunk["text"] for chunk in previous_chunks) == previous
    assert "".join(chunk["text"] for chunk in updated_chunks) == updated


def test_default_tokenizer_uses_pinned_local_bge_artifact():
    settings = Settings(_env_file=None)
    assert settings.chunk_tokenizer_path == (
        SERVICE_DIR / "data/tokenizers/bge-m3-5617a9f61b028005a4858fdac845db406aefb181.json"
    )
    assert (settings.chunk_max_tokens, settings.chunk_min_tokens) == (512, 64)


def test_relative_tokenizer_path_is_based_on_service_not_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=None, chunk_tokenizer_path="data/tokenizers/custom.json")
    assert settings.chunk_tokenizer_path == SERVICE_DIR / "data/tokenizers/custom.json"


@pytest.mark.parametrize("limits", [
    {"chunk_max_tokens": 0}, {"chunk_max_tokens": -1}, {"chunk_min_tokens": -1},
    {"chunk_max_tokens": 4, "chunk_min_tokens": 5},
])
def test_invalid_chunk_configuration_fails_before_serving_requests(limits):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **limits)
