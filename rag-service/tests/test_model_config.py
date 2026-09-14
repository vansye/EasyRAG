"""The browser can configure the answer model without reading its credentials."""

import json
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app


@pytest.fixture(autouse=True)
def configuration_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in tuple(os.environ):
        if name.startswith("LLM_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "environment-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://models.example.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "private-configuration-key")
    monkeypatch.setenv("LLM_CONFIG_FILE", str(tmp_path / "config" / "llm.json"))
    monkeypatch.setattr(app.state, "settings", Settings(_env_file=None), raising=False)


def update(**changes):
    return {
        "provider": "openai", "model": "saved-model",
        "base_url": "https://models.example.test/v1", "api_key": "",
        **changes,
    }


def test_read_configuration_exposes_settings_but_never_the_key():
    response = TestClient(app).get("/model-config")

    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "provider": "openai",
        "model": "environment-model",
        "base_url": "https://models.example.test/v1",
        "api_key_configured": True,
        "source": "environment",
    }
    assert "private-configuration-key" not in response.text


def test_save_persists_and_new_model_instances_use_the_saved_configuration(monkeypatch, tmp_path):
    client = TestClient(app)
    response = client.put("/model-config", json=update(api_key="new-private-key"))
    assert response.status_code == 200
    assert response.json()["source"] == "local"
    assert response.json()["model"] == "saved-model"
    assert "new-private-key" not in response.text
    assert response.headers["cache-control"] == "no-store"
    persisted = json.loads((tmp_path / "config" / "llm.json").read_text(encoding="utf-8"))
    assert persisted["api_key"] == "new-private-key"

    # A fresh client and model factory must read the file, not browser state.
    assert TestClient(app).get("/model-config").json() == response.json()
    captured = []
    monkeypatch.setattr("app.llm.init_chat_model", lambda model, **options: captured.append((model, options)))
    from app.llm import create_chat_model
    create_chat_model()
    assert captured[0][0] == "saved-model"
    assert captured[0][1]["api_key"] == "new-private-key"
    assert captured[0][1]["base_url"] == "https://models.example.test/v1"
    assert client.get("/runtime").json()["llm"]["model"] == "saved-model"


def test_blank_key_keeps_existing_key_only_for_the_same_destination(tmp_path):
    response = TestClient(app).put("/model-config", json=update(base_url="https://models.example.test/v1/"))
    assert response.status_code == 200
    assert response.json()["api_key_configured"] is True
    persisted = json.loads((tmp_path / "config" / "llm.json").read_text(encoding="utf-8"))
    assert persisted["api_key"] == "private-configuration-key"


@pytest.mark.parametrize("changes", [
    {"base_url": "https://other.example.test/v1"},
    {"provider": "deepseek"},
])
def test_changed_destination_requires_a_new_key_and_preserves_current_settings(changes, tmp_path):
    client = TestClient(app)
    response = client.put("/model-config", json=update(**changes))
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "API_KEY_REQUIRED"
    assert not (tmp_path / "config" / "llm.json").exists()
    assert client.get("/model-config").json()["model"] == "environment-model"


def test_new_key_allows_switching_to_another_provider():
    response = TestClient(app).put("/model-config", json=update(
        provider="deepseek", base_url="https://api.deepseek.com", api_key="new-provider-key",
    ))
    assert response.status_code == 200
    assert response.json()["provider"] == "deepseek"
    assert "new-provider-key" not in response.text


@pytest.mark.parametrize("changes", [
    {"provider": "unknown"}, {"model": "   "}, {"model": 123},
    {"base_url": "file:///private"},
    {"base_url": "https://user:private-url-key@example.test/v1"},
    {"base_url": "https://example.test/v1?key=private-url-key"},
    {"base_url": "https://example.test/v1#private-url-key"},
    {"api_key": {"secret": "private-input-key"}}, {"embedding_model": "other-model"},
])
def test_invalid_settings_are_rejected_without_echoing_input(changes, tmp_path):
    response = TestClient(app).put("/model-config", json=update(**changes))
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "INVALID_CONFIG"
    assert "private-" not in response.text
    assert not (tmp_path / "config" / "llm.json").exists()


@pytest.mark.parametrize("body", ['{"api_key":"private-json-key"', '"private-json-key"', '[]'])
def test_invalid_json_has_a_sanitized_error(body):
    response = TestClient(app).put("/model-config", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert "private-json-key" not in response.text


def test_reset_restores_environment_without_changing_it(tmp_path):
    client = TestClient(app)
    assert client.put("/model-config", json=update()).status_code == 200
    response = client.delete("/model-config")
    assert response.status_code == 200
    assert response.json()["source"] == "environment"
    assert response.json()["model"] == "environment-model"
    assert not (tmp_path / "config" / "llm.json").exists()
    assert client.delete("/model-config").json() == response.json()


def test_missing_environment_can_be_configured_from_the_browser(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    client = TestClient(app)
    assert client.get("/model-config").json()["configured"] is False
    assert client.put("/model-config", json=update()).status_code == 400
    response = client.put("/model-config", json=update(api_key="first-key"))
    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert client.delete("/model-config").json()["configured"] is False


def test_failed_atomic_replacement_preserves_last_saved_settings(monkeypatch, tmp_path):
    client = TestClient(app)
    assert client.put("/model-config", json=update()).status_code == 200
    previous = (tmp_path / "config" / "llm.json").read_bytes()

    def fail_replace(*_args):
        raise OSError("private-filesystem-details")

    monkeypatch.setattr("app.model_config.os.replace", fail_replace)
    response = client.put("/model-config", json=update(model="failed-model", api_key="failed-private-key"))
    assert response.status_code == 503
    assert "private-" not in response.text
    assert (tmp_path / "config" / "llm.json").read_bytes() == previous
    assert client.get("/model-config").json()["model"] == "saved-model"
    assert list((tmp_path / "config").iterdir()) == [tmp_path / "config" / "llm.json"]


def test_corrupt_saved_file_is_reported_and_can_be_reset(tmp_path):
    path = tmp_path / "config" / "llm.json"
    path.parent.mkdir()
    path.write_text('{"api_key":"private-corrupt-key"', encoding="utf-8")
    client = TestClient(app)
    response = client.get("/model-config")
    assert response.status_code == 503
    assert "private-corrupt-key" not in response.text
    assert client.get("/runtime").json()["llm"]["configured"] is False
    assert client.delete("/model-config").json()["model"] == "environment-model"


def test_query_reports_an_unreadable_model_configuration_without_internal_details(monkeypatch):
    from app.model_config import ModelConfigUnavailable

    def fail_configuration(**_options):
        raise ModelConfigUnavailable("private-config-details")

    monkeypatch.setattr(app.state, "index", SimpleNamespace(vector_count=lambda: 0), raising=False)
    monkeypatch.setattr("app.main.QaPipeline", fail_configuration)
    response = TestClient(app, raise_server_exceptions=False).post("/query", json={"question": "test"})
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "LLM_CONFIG_UNAVAILABLE"
    assert "private-config-details" not in response.text


@pytest.mark.parametrize("provider,environment,expected", [
    ("openai", {}, "https://api.openai.com/v1"),
    ("deepseek", {}, "https://api.deepseek.com/v1"),
    ("openai", {"OPENAI_BASE_URL": "https://sdk.example.test/v1"}, "https://sdk.example.test/v1"),
    ("openai", {
        "OPENAI_API_BASE": "https://langchain.example.test/v1",
        "OPENAI_BASE_URL": "https://sdk.example.test/v1",
        "LANGSMITH_GATEWAY": "true",
    }, "https://langchain.example.test/v1"),
    ("openai", {
        "LLM_BASE_URL": "https://explicit.example.test/v1",
        "OPENAI_API_BASE": "https://langchain.example.test/v1",
        "LANGSMITH_GATEWAY": "true",
    }, "https://explicit.example.test/v1"),
    ("openai", {
        "OPENAI_BASE_URL": "https://sdk.example.test/v1", "LANGSMITH_GATEWAY": "true",
    }, "https://gateway.smith.langchain.com/openai/v1"),
    ("openai", {"LANGSMITH_GATEWAY": "https://gateway.example.test/"}, "https://gateway.example.test/openai/v1"),
    ("deepseek", {
        "DEEPSEEK_API_BASE": "https://deepseek.example.test/v1",
        "OPENAI_API_BASE": "https://langchain.example.test/v1",
        "LANGSMITH_GATEWAY": "true",
    }, "https://deepseek.example.test/v1"),
    ("deepseek", {
        "LLM_BASE_URL": "https://explicit.example.test/v1",
        "DEEPSEEK_API_BASE": "https://deepseek.example.test/v1",
    }, "https://explicit.example.test/v1"),
    *[("openai", {"LANGSMITH_GATEWAY": value}, "https://api.openai.com/v1") for value in ("", "false", "0", "NO")],
    *[("openai", {"LANGSMITH_GATEWAY": value}, "https://gateway.smith.langchain.com/openai/v1") for value in ("TRUE", "1", "yes")],
])
def test_read_save_and_actual_model_use_the_same_effective_endpoint(monkeypatch, provider, environment, expected):
    from app.llm import create_chat_model

    monkeypatch.delenv("LLM_BASE_URL")
    monkeypatch.setenv("LLM_PROVIDER", provider)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    client = TestClient(app)

    before = client.get("/model-config")
    assert before.status_code == 200
    assert before.json()["base_url"] == expected
    assert str(create_chat_model().root_client.base_url).rstrip("/") == expected

    # Changing only the model must keep both the endpoint and its existing key.
    saved = client.put("/model-config", json=update(provider=provider, base_url=before.json()["base_url"]))
    assert saved.status_code == 200
    assert saved.json()["base_url"] == expected
    model = create_chat_model()
    assert model.model_name == "saved-model"
    assert str(model.root_client.base_url).rstrip("/") == expected
    assert model.root_client.api_key == "private-configuration-key"


@pytest.mark.parametrize("variable", ["OPENAI_API_BASE", "OPENAI_BASE_URL", "LANGSMITH_GATEWAY"])
def test_sdk_gateway_key_cannot_be_reused_at_official_endpoint(monkeypatch, variable, tmp_path):
    monkeypatch.delenv("LLM_BASE_URL")
    monkeypatch.setenv(variable, "https://private-gateway.example.test/v1")

    response = TestClient(app).put("/model-config", json=update(base_url="https://api.openai.com/v1"))

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "API_KEY_REQUIRED"
    assert not (tmp_path / "config" / "llm.json").exists()


@pytest.mark.parametrize("variable,provider", [
    ("LLM_BASE_URL", "openai"), ("OPENAI_API_BASE", "openai"),
    ("OPENAI_BASE_URL", "openai"), ("LANGSMITH_GATEWAY", "openai"),
    ("DEEPSEEK_API_BASE", "deepseek"),
])
@pytest.mark.parametrize("url", [
    "https://user:private-url-key@example.test/v1",
    "https://example.test/v1?api_key=private-url-key",
    "https://example.test/v1#private-url-key",
])
def test_legacy_endpoint_credentials_are_neither_exposed_nor_used(monkeypatch, variable, provider, url):
    from app.llm import create_chat_model
    from app.model_config import ModelConfigUnavailable

    monkeypatch.delenv("LLM_BASE_URL")
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv(variable, url)

    response = TestClient(app).get("/model-config")

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "MODEL_CONFIG_UNAVAILABLE"
    assert "private-" not in response.text
    with pytest.raises(ModelConfigUnavailable) as failure:
        create_chat_model()
    assert "private-" not in str(failure.value)
