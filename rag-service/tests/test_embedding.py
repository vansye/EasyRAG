"""原生 embedding 客户端的协议、批次及配置测试，不依赖在线模型。"""

from __future__ import annotations

import json
import os

import httpx
import pytest
from pydantic import ValidationError

from app.modules.retrieval.public import RetrievalSettings


@pytest.fixture(autouse=True)
def clean_embedding_env(monkeypatch):
    for variable in list(os.environ):
        if variable.startswith(("EMBEDDING_", "EMBED_", "CHUNK_")):
            monkeypatch.delenv(variable, raising=False)


def settings_for_test(**overrides):
    values = {"embedding_provider": "ollama", "embedding_model": "test-model",
              "embedding_dim": 3, "embedding_base_url": "https://embedding.test",
              "embed_batch_size": 2, "embedding_api_key": "sensitive-key",
              "embedding_timeout_seconds": 7.5}
    values.update(overrides)
    return RetrievalSettings(_env_file=None, **values)


def call_embedding(client, texts, settings):
    from app.modules.retrieval._embedding import embed_texts

    return embed_texts(client, texts, settings=settings)


def test_embedding_settings_keep_credentials_secret_and_timeout_configurable():
    settings = settings_for_test()

    assert settings.embedding_timeout_seconds == 7.5
    assert settings.embedding_api_key.get_secret_value() == "sensitive-key"
    assert "sensitive-key" not in repr(settings)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_embedding_settings_reject_nonpositive_or_nonfinite_timeout(timeout):
    with pytest.raises(ValidationError):
        settings_for_test(embedding_timeout_seconds=timeout)


def test_embedding_settings_hide_invalid_credential_input():
    with pytest.raises(ValidationError) as error:
        settings_for_test(embedding_api_key=["sensitive-key"])
    assert "sensitive-key" not in str(error.value)


def test_ollama_batches_inputs_without_truncation_or_retry():
    requests = []

    def respond(request):
        requests.append(request)
        payload = json.loads(request.content)
        return httpx.Response(200, json={"embeddings": [[len(text), 1, 0] for text in payload["input"]]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        vectors = call_embedding(client, ["a", "bb", "ccc"], settings_for_test())

    assert vectors == [[1, 1, 0], [2, 1, 0], [3, 1, 0]]
    assert [json.loads(request.content)["input"] for request in requests] == [["a", "bb"], ["ccc"]]
    for request in requests:
        assert str(request.url) == "https://embedding.test/api/embed"
        assert request.headers["authorization"] == "Bearer sensitive-key"
        assert json.loads(request.content)["model"] == "test-model"
        assert json.loads(request.content)["truncate"] is False


@pytest.mark.parametrize("api_key", [None, ""])
def test_local_embedding_does_not_send_an_empty_authorization_header(api_key):
    def respond(request):
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"embeddings": [[1, 1, 0]]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert call_embedding(client, ["text"], settings_for_test(embedding_api_key=api_key)) == [[1, 1, 0]]


@pytest.mark.parametrize("provider", ["openai", "deepseek"])
@pytest.mark.parametrize("base_url", ["https://embedding.test", "https://embedding.test/v1/"])
def test_compatible_embedding_restores_input_order_from_response_indices(provider, base_url):
    requests = []
    expected = {"first": [1, 0, 0], "second": [0, 1, 0], "third": [0, 0, 1]}

    def respond(request):
        requests.append(request)
        payload = json.loads(request.content)
        assert str(request.url) == "https://embedding.test/v1/embeddings"
        assert payload["encoding_format"] == "float"
        assert "truncate" not in payload
        entries = [{"index": index, "embedding": expected[text]}
                   for index, text in enumerate(payload["input"])]
        return httpx.Response(200, json={"data": list(reversed(entries))})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        vectors = call_embedding(client, list(expected), settings_for_test(
            embedding_provider=provider, embedding_base_url=base_url,
        ))

    assert vectors == list(expected.values())
    assert len(requests) == 2
    assert all(request.headers["authorization"] == "Bearer sensitive-key" for request in requests)


@pytest.mark.parametrize("payload", [
    None, [], {}, {"embeddings": []}, {"embeddings": "private"},
    {"embeddings": [[1, 2]]}, {"embeddings": ["bad"]},
    {"embeddings": [[True, 0, 1]]}, {"embeddings": [["private", 0, 1]]},
    {"embeddings": [[float("nan"), 0, 1]]}, {"embeddings": [[float("inf"), 0, 1]]},
    {"embeddings": [[1e40, 0, 1]]}, {"embeddings": [[-1e40, 0, 1]]},
])
def test_malformed_vectors_are_rejected_without_echoing_response_content(payload):
    def respond(_request):
        return httpx.Response(200, content=json.dumps(payload), headers={"content-type": "application/json"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError) as error:
            call_embedding(client, ["text"], settings_for_test())
    assert "private" not in str(error.value)


@pytest.mark.parametrize("coordinate", [float.fromhex("0x1.fffffep+127"), -float.fromhex("0x1.fffffep+127")])
def test_finite_float32_boundaries_are_accepted(coordinate):
    with httpx.Client(transport=httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"embeddings": [[coordinate, 0, 1]]})
    )) as client:
        assert call_embedding(client, ["text"], settings_for_test()) == [[coordinate, 0, 1]]


@pytest.mark.parametrize("indices", [[0, 0], [0, 2], [False, 1], [0, None]])
def test_compatible_embedding_rejects_invalid_response_indices(indices):
    def respond(_request):
        return httpx.Response(200, json={"data": [
            {"index": index, "embedding": [1, 0, 0]} for index in indices
        ]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError):
            call_embedding(client, ["first", "second"], settings_for_test(embedding_provider="openai"))


def test_non_json_embedding_response_is_rejected():
    with httpx.Client(transport=httpx.MockTransport(
        lambda _request: httpx.Response(200, text="private gateway response")
    )) as client:
        with pytest.raises(ValueError) as error:
            call_embedding(client, ["text"], settings_for_test())
    assert "private gateway response" not in str(error.value)


def test_model_error_stops_later_batches_without_retry():
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"embeddings": [[1, 0, 0], [0, 1, 0]]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            call_embedding(client, ["text"] * 5, settings_for_test())
    assert len(requests) == 2


def test_health_uses_the_same_compatible_url_and_credentials(monkeypatch):
    from app.modules.retrieval._embedding import probe_embedding

    requests = []
    original_client = httpx.Client

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "test-model"}]})

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(
        transport=httpx.MockTransport(respond), **kwargs,
    ))

    result = probe_embedding(settings_for_test(
        embedding_provider="openai", embedding_base_url="https://embedding.test/v1/",
    ))

    assert result["status"] == "UP"
    assert len(requests) == 1
    assert str(requests[0].url) == "https://embedding.test/v1/models"
    assert requests[0].headers["authorization"] == "Bearer sensitive-key"
