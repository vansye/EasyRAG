"""Answer-model configuration and sessions stay behind one credential-safe boundary."""

import json
import os
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import httpx2
import pytest

from app.modules.answer_models import public as answer_models


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in tuple(os.environ):
        if name.startswith("LLM_") or name in {
            "OPENAI_API_BASE", "OPENAI_BASE_URL", "OPENAI_API_KEY", "DEEPSEEK_API_BASE",
            "DEEPSEEK_API_KEY", "LANGSMITH_GATEWAY", "LANGSMITH_API_KEY", "LANGCHAIN_API_KEY",
        }:
            monkeypatch.delenv(name)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "environment-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://models.example.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "private-environment-key")
    monkeypatch.setenv("LLM_CONFIG_FILE", str(tmp_path / "environment-config.json"))
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")

    def unexpected_http(*_args, **_options):
        raise AssertionError("tests must replace the model HTTP transport")

    monkeypatch.setattr(httpx2.Client, "send", unexpected_http)
    monkeypatch.setattr(httpx2.AsyncClient, "send", unexpected_http)


def update(**changes):
    return {
        "provider": "openai", "model": "saved-model",
        "base_url": "https://models.example.test/v1", "api_key": "",
        **changes,
    }


def test_get_preserves_vue_fields_without_returning_credentials(tmp_path):
    models = answer_models.Models(config_path=tmp_path / "llm.json")

    snapshot = models.get()

    assert snapshot == {
        "configured": True, "provider": "openai", "model": "environment-model",
        "base_url": "https://models.example.test/v1", "api_key_configured": True,
        "source": "environment",
    }
    assert "private-" not in repr(snapshot)
    snapshot["model"] = "changed-by-consumer"
    assert models.get()["model"] == "environment-model"


def test_saved_file_takes_priority_over_environment_and_explicit_path_wins(tmp_path, monkeypatch):
    path = tmp_path / "config" / "llm.json"
    models = answer_models.Models(config_path=path)

    saved = models.save(update(api_key="private-saved-key"))
    monkeypatch.setenv("LLM_MODEL", "changed-environment-model")
    monkeypatch.setenv("LLM_API_KEY", "private-changed-environment-key")

    assert saved["source"] == "local"
    assert saved["model"] == "saved-model"
    assert "private-" not in repr(saved)
    assert answer_models.Models(config_path=path).get() == saved
    assert json.loads(path.read_text(encoding="utf-8"))["api_key"] == "private-saved-key"
    assert not Path(os.environ["LLM_CONFIG_FILE"]).exists()


@pytest.mark.parametrize("blank_key", [None, "", "   "])
def test_blank_key_keeps_current_key_for_same_normalized_destination(tmp_path, blank_key):
    path = tmp_path / "llm.json"
    models = answer_models.Models(config_path=path)

    models.save(update(base_url="https://models.example.test/v1/", api_key=blank_key))
    models.save(update(model="another-model", api_key=blank_key))

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["api_key"] == "private-environment-key"
    assert persisted["model"] == "another-model"


def test_omitted_key_keeps_the_current_secret(tmp_path):
    path = tmp_path / "llm.json"
    payload = update()
    del payload["api_key"]

    answer_models.Models(config_path=path).save(payload)

    assert json.loads(path.read_text(encoding="utf-8"))["api_key"] == "private-environment-key"


@pytest.mark.parametrize("changes", [
    {"provider": "deepseek"}, {"base_url": "https://other.example.test/v1"},
    {"base_url": "http://models.example.test/v1"}, {"base_url": "https://models.example.test/v2"},
])
def test_destination_change_requires_new_key_and_preserves_saved_file(tmp_path, changes):
    path = tmp_path / "llm.json"
    models = answer_models.Models(config_path=path)
    before = models.save(update(api_key="private-saved-key"))
    original = path.read_bytes()

    with pytest.raises(answer_models.ConfigRejected) as rejected:
        models.save(update(**changes))

    assert rejected.value.code == "API_KEY_REQUIRED"
    assert "private-" not in str(rejected.value)
    assert path.read_bytes() == original
    assert models.get() == before


def test_new_key_can_change_provider_and_reset_restores_environment(tmp_path):
    path = tmp_path / "llm.json"
    models = answer_models.Models(config_path=path)

    saved = models.save(update(
        provider="deepseek", base_url="https://api.deepseek.com", api_key=" private-new-key ",
    ))

    assert saved["provider"] == "deepseek"
    assert saved["base_url"] == "https://api.deepseek.com"
    assert json.loads(path.read_text(encoding="utf-8"))["api_key"] == "private-new-key"
    reset = models.reset()
    assert reset["source"] == "environment"
    assert reset["model"] == "environment-model"
    assert not path.exists()
    assert models.reset() == reset
    assert os.environ["LLM_API_KEY"] == "private-environment-key"


def test_missing_environment_is_unconfigured_and_can_be_saved(tmp_path, monkeypatch):
    path = tmp_path / "llm.json"
    models = answer_models.Models(config_path=path)
    monkeypatch.delenv("LLM_MODEL")
    monkeypatch.delenv("LLM_API_KEY")

    empty = models.get()

    assert empty == {
        "configured": False, "provider": "openai", "model": "",
        "base_url": "https://api.openai.com/v1", "api_key_configured": False,
        "source": "environment",
    }
    with pytest.raises(answer_models.ConfigRejected) as rejected:
        models.save(update())
    assert rejected.value.code == "API_KEY_REQUIRED"
    assert not path.exists()
    assert models.save(update(api_key="private-first-key"))["configured"] is True
    assert models.reset() == empty


@pytest.mark.parametrize("changes", [
    {"provider": "unknown"}, {"model": "   "}, {"model": 123}, {"model": "m" * 201},
    {"base_url": "file:///private-file"},
    {"base_url": "https://user:private-url-key@example.test/v1"},
    {"base_url": "https://example.test/v1?key=private-url-key"},
    {"base_url": "https://example.test/v1#private-url-key"},
    {"api_key": {"secret": "private-input-key"}}, {"api_key": "k" * 4097},
    {"embedding_model": "unrelated"},
])
def test_invalid_update_is_rejected_without_echoing_input(tmp_path, changes):
    path = tmp_path / "llm.json"
    models = answer_models.Models(config_path=path)

    with pytest.raises(answer_models.ConfigRejected) as rejected:
        models.save(update(**changes))

    assert rejected.value.code == "INVALID_CONFIG"
    assert "private-" not in str(rejected.value)
    assert "private-" not in repr(rejected.value)
    assert not path.exists()


@pytest.mark.parametrize("payload", [None, [], "private-input-key"])
def test_non_object_updates_are_rejected(tmp_path, payload):
    models = answer_models.Models(config_path=tmp_path / "llm.json")

    with pytest.raises(answer_models.ConfigRejected) as rejected:
        models.save(payload)

    assert rejected.value.code == "INVALID_CONFIG"
    assert "private-" not in str(rejected.value)


@pytest.mark.parametrize("address", [
    "http://localhost:11434/v1", "http://127.0.0.1:11434/v1",
    "http://models.example.test/v1", "https://models.example.test/v1",
])
def test_http_and_https_destinations_preserve_legacy_compatibility(tmp_path, address):
    models = answer_models.Models(config_path=tmp_path / "llm.json")

    assert models.save(update(base_url=address, api_key="private-new-key"))["base_url"] == address


def test_failed_atomic_replace_preserves_saved_configuration_and_cleans_temp_file(tmp_path, monkeypatch):
    path = tmp_path / "llm.json"
    models = answer_models.Models(config_path=path)
    previous = models.save(update())
    original = path.read_bytes()

    def fail_replace(*_args):
        raise OSError("private-filesystem-details")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(answer_models.ModelUnavailable) as failed:
        models.save(update(model="failed-model", api_key="private-failed-key"))

    assert failed.value.code == "MODEL_CONFIG_UNAVAILABLE"
    assert "private-" not in str(failed.value)
    assert path.read_bytes() == original
    assert models.get() == previous
    assert list(tmp_path.glob(".llm-*.tmp")) == []


@pytest.mark.parametrize("contents", [
    b'{"api_key":"private-corrupt-key"', b'[]', b'{"model":""}', b'\xff\xfe',
])
def test_corrupt_saved_file_is_sanitized_and_can_be_reset(tmp_path, contents):
    path = tmp_path / "llm.json"
    path.write_bytes(contents)
    models = answer_models.Models(config_path=path)

    with pytest.raises(answer_models.ModelUnavailable) as failed:
        models.get()

    assert failed.value.code == "MODEL_CONFIG_UNAVAILABLE"
    assert "private-" not in str(failed.value)
    assert models.reset()["model"] == "environment-model"


def test_saved_values_fall_back_to_environment_for_missing_fields(tmp_path):
    path = tmp_path / "llm.json"
    path.write_text('{"model":"saved-model"}', encoding="utf-8")
    models = answer_models.Models(config_path=path)

    assert models.get()["model"] == "saved-model"
    assert models.get()["source"] == "local"


def test_dotenv_keeps_llm_prefix_and_ignores_embedding_settings(tmp_path, monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("LLM_"):
            monkeypatch.delenv(name)
    (tmp_path / ".env").write_text(
        "LLM_PROVIDER=deepseek\nLLM_MODEL=dotenv-model\nLLM_API_KEY=private-dotenv-key\n"
        "LLM_BASE_URL=http://localhost:11434/v1\nEMBEDDING_MODEL=not-an-answer-model\n",
        encoding="utf-8",
    )

    snapshot = answer_models.Models(config_path=tmp_path / "llm.json").get()

    assert snapshot["model"] == "dotenv-model"
    assert snapshot["provider"] == "deepseek"
    assert snapshot["base_url"] == "http://localhost:11434/v1"
    assert "private-" not in repr(snapshot)


@pytest.mark.parametrize("operation", ["get", "save", "reset", "open_session"])
def test_invalid_dotenv_bytes_are_sanitized_for_each_public_operation(tmp_path, operation):
    (tmp_path / ".env").write_bytes(b"LLM_API_KEY=private-dotenv-key\xff")
    models = answer_models.Models(config_path=tmp_path / "llm.json")

    with pytest.raises(answer_models.ModelUnavailable) as failed:
        if operation == "save":
            models.save(update(api_key="private-new-key"))
        else:
            getattr(models, operation)()

    assert failed.value.code == "MODEL_CONFIG_UNAVAILABLE"
    assert "private-" not in str(failed.value)
    assert not (tmp_path / "llm.json").exists()


@pytest.mark.parametrize("provider,environment,expected", [
    ("openai", {}, "https://api.openai.com/v1"),
    ("deepseek", {}, "https://api.deepseek.com/v1"),
    ("openai", {"OPENAI_BASE_URL": "https://sdk.example.test/v1"}, "https://sdk.example.test/v1"),
    ("openai", {
        "OPENAI_API_BASE": "https://langchain.example.test/v1",
        "OPENAI_BASE_URL": "https://sdk.example.test/v1", "LANGSMITH_GATEWAY": "true",
    }, "https://langchain.example.test/v1"),
    ("openai", {
        "LLM_BASE_URL": "https://explicit.example.test/v1",
        "OPENAI_API_BASE": "https://langchain.example.test/v1", "LANGSMITH_GATEWAY": "true",
    }, "https://explicit.example.test/v1"),
    ("openai", {
        "OPENAI_BASE_URL": "https://sdk.example.test/v1", "LANGSMITH_GATEWAY": "true",
    }, "https://gateway.smith.langchain.com/openai/v1"),
    ("openai", {"LANGSMITH_GATEWAY": "https://gateway.example.test/"}, "https://gateway.example.test/openai/v1"),
    ("deepseek", {
        "DEEPSEEK_API_BASE": "https://deepseek.example.test/v1",
        "OPENAI_API_BASE": "https://langchain.example.test/v1", "LANGSMITH_GATEWAY": "true",
    }, "https://deepseek.example.test/v1"),
    ("deepseek", {
        "LLM_BASE_URL": "https://explicit.example.test/v1",
        "DEEPSEEK_API_BASE": "https://deepseek.example.test/v1",
    }, "https://explicit.example.test/v1"),
    *[("openai", {"LANGSMITH_GATEWAY": value}, "https://api.openai.com/v1") for value in ("", "false", "0", "NO")],
    *[("openai", {"LANGSMITH_GATEWAY": value}, "https://gateway.smith.langchain.com/openai/v1") for value in ("TRUE", "1", "yes")],
])
def test_get_and_save_share_effective_sdk_endpoint_precedence(tmp_path, monkeypatch, provider, environment, expected):
    monkeypatch.delenv("LLM_BASE_URL")
    monkeypatch.setenv("LLM_PROVIDER", provider)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    models = answer_models.Models(config_path=tmp_path / "llm.json")

    assert models.get()["base_url"] == expected
    assert models.save(update(provider=provider, base_url=expected))["base_url"] == expected


@pytest.mark.parametrize("variable", ["OPENAI_API_BASE", "OPENAI_BASE_URL", "LANGSMITH_GATEWAY"])
def test_gateway_key_is_not_reused_at_provider_default(tmp_path, monkeypatch, variable):
    monkeypatch.delenv("LLM_BASE_URL")
    monkeypatch.setenv(variable, "https://private-gateway.example.test/v1")
    path = tmp_path / "llm.json"
    models = answer_models.Models(config_path=path)

    with pytest.raises(answer_models.ConfigRejected) as rejected:
        models.save(update(base_url="https://api.openai.com/v1"))

    assert rejected.value.code == "API_KEY_REQUIRED"
    assert not path.exists()


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
def test_credentials_in_environment_endpoints_are_rejected_without_exposure(tmp_path, monkeypatch, variable, provider, url):
    monkeypatch.delenv("LLM_BASE_URL")
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv(variable, url)
    models = answer_models.Models(config_path=tmp_path / "llm.json")

    for operation in (models.get, models.open_session):
        with pytest.raises(answer_models.ModelUnavailable) as failed:
            operation()
        assert failed.value.code == "MODEL_CONFIG_UNAVAILABLE"
        assert "private-" not in str(failed.value)


def test_default_config_path_stays_at_service_root_without_reading_user_file(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_CONFIG_FILE")
    reads = []

    def no_saved_file(path, **_options):
        reads.append(path)
        raise FileNotFoundError

    monkeypatch.setattr(Path, "read_text", no_saved_file)

    models = answer_models.Models()
    assert reads == []
    assert models.get()["source"] == "environment"
    assert reads == [Path(__file__).resolve().parents[1] / "config" / "llm.json"]


def test_config_file_environment_override_is_honored(tmp_path):
    models = answer_models.Models()

    models.save(update(api_key="private-new-key"))

    assert (tmp_path / "environment-config.json").is_file()
    assert models.get()["source"] == "local"


def test_session_constructs_one_client_and_sends_one_human_message_per_completion(tmp_path, monkeypatch):
    from langchain_core.messages import HumanMessage

    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "12.5")
    created = []
    requests = []

    def create_client(model, **options):
        created.append((model, options))

        def invoke(messages):
            requests.append(messages)
            return SimpleNamespace(content=" answer with whitespace \n")

        return SimpleNamespace(invoke=invoke)

    monkeypatch.setattr("langchain.chat_models.init_chat_model", create_client)
    models = answer_models.Models(config_path=tmp_path / "llm.json")

    session = models.open_session()
    assert requests == []
    assert session.complete("first prompt") == " answer with whitespace \n"
    assert session.complete("second prompt") == " answer with whitespace \n"

    assert created == [("environment-model", {
        "model_provider": "openai", "api_key": "private-environment-key",
        "base_url": "https://models.example.test/v1", "timeout": 12.5, "max_retries": 0,
    })]
    assert len(requests) == 2
    assert all(len(messages) == 1 and isinstance(messages[0], HumanMessage) for messages in requests)
    assert [messages[0].content for messages in requests] == ["first prompt", "second prompt"]
    assert "private-" not in repr(session)
    assert not any(hasattr(session, name) for name in ("client", "settings", "api_key", "invoke", "bind_tools"))


def test_session_model_info_is_a_fresh_credential_free_snapshot(tmp_path, monkeypatch):
    def create_client(model, **_options):
        return SimpleNamespace(invoke=lambda _messages: SimpleNamespace(content=model))

    monkeypatch.setattr("langchain.chat_models.init_chat_model", create_client)
    session = answer_models.Models(config_path=tmp_path / "llm.json").open_session()

    snapshot = session.model_info

    assert isinstance(snapshot, dict)
    assert snapshot == {"provider": "openai", "model": "environment-model"}
    assert session.model_info == snapshot
    assert session.model_info is not snapshot
    snapshot.update(provider="deepseek", model="changed-by-consumer", api_key="private-consumer-key")
    assert session.model_info == {"provider": "openai", "model": "environment-model"}
    assert session.complete("question") == "environment-model"


def test_session_model_info_uses_the_environment_read_before_client_creation(tmp_path, monkeypatch):
    def create_client(model, **_options):
        monkeypatch.setenv("LLM_PROVIDER", "deepseek")
        monkeypatch.setenv("LLM_MODEL", "changed-environment-model")
        return SimpleNamespace(invoke=lambda _messages: SimpleNamespace(content=model))

    monkeypatch.setattr("langchain.chat_models.init_chat_model", create_client)
    models = answer_models.Models(config_path=tmp_path / "llm.json")
    session = models.open_session()

    assert session.model_info == {"provider": "openai", "model": "environment-model"}
    assert session.complete("existing question") == "environment-model"
    next_session = models.open_session()
    assert next_session.model_info == {"provider": "deepseek", "model": "changed-environment-model"}
    assert next_session.complete("next question") == "changed-environment-model"


@pytest.mark.parametrize("prepared", [False, True])
def test_concurrent_save_only_changes_the_next_session(tmp_path, monkeypatch, prepared):
    started = Event()
    release = Event()
    created = []

    def create_client(model, **options):
        created.append((model, options))

        def invoke(messages):
            if messages[0].content == "blocked question":
                started.set()
                if not release.wait(timeout=5):
                    raise TimeoutError("test release was not signaled")
            return SimpleNamespace(content=f"reply from {model}")

        return SimpleNamespace(invoke=invoke)

    monkeypatch.setattr("langchain.chat_models.init_chat_model", create_client)
    models = answer_models.Models(config_path=tmp_path / "llm.json")
    if prepared:
        models.prepare()
    session = models.open_session()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(session.complete, "blocked question")
        try:
            assert started.wait(timeout=5)
            models.save(update(
                provider="deepseek", base_url="https://new.example.test/v1", api_key="private-new-key",
            ))
            assert session.model_info == {"provider": "openai", "model": "environment-model"}
            next_session = models.open_session()
            assert next_session.model_info == {"provider": "deepseek", "model": "saved-model"}
            assert next_session.complete("new question") == "reply from saved-model"
        finally:
            release.set()
        assert pending.result(timeout=5) == "reply from environment-model"

    assert session.complete("follow-up judgement") == "reply from environment-model"
    assert len(created) == 2
    assert created[0][1]["api_key"] == "private-environment-key"
    assert created[1][1]["api_key"] == "private-new-key"
    assert created[1][1]["base_url"] == "https://new.example.test/v1"
    assert created[1][1]["model_provider"] == "deepseek"


def test_reset_does_not_change_an_open_session(tmp_path, monkeypatch):
    def create_client(model, **_options):
        return SimpleNamespace(invoke=lambda _messages: SimpleNamespace(content=model))

    monkeypatch.setattr("langchain.chat_models.init_chat_model", create_client)
    models = answer_models.Models(config_path=tmp_path / "llm.json")
    models.save(update())
    session = models.open_session()

    models.reset()

    assert session.model_info == {"provider": "openai", "model": "saved-model"}
    assert session.complete("existing question") == "saved-model"
    next_session = models.open_session()
    assert next_session.model_info == {"provider": "openai", "model": "environment-model"}
    assert next_session.complete("next question") == "environment-model"


def test_sdk_load_failure_is_sanitized_and_does_not_break_public_metadata(tmp_path, monkeypatch):
    def broken_sdk(*_args, **_options):
        raise RuntimeError("private-sdk-initialization-key")

    monkeypatch.setattr("langchain.chat_models.init_chat_model", broken_sdk)
    models = answer_models.Models(config_path=tmp_path / "llm.json")

    assert models.get()["configured"] is True
    assert models.save(update())["configured"] is True
    assert models.reset()["configured"] is True
    with pytest.raises(answer_models.ModelUnavailable) as failed:
        models.open_session()

    assert failed.value.code == "LLM_UNAVAILABLE"
    assert "private-" not in str(failed.value)
    assert failed.value.__cause__ is None


@pytest.mark.parametrize("contents", ["", " \n", [], [{"type": "text", "text": "non-string result"}], None])
def test_empty_and_nontext_replies_are_sanitized_model_failures(tmp_path, monkeypatch, contents):
    monkeypatch.setattr("langchain.chat_models.init_chat_model", lambda *_args, **_options: SimpleNamespace(
        invoke=lambda _messages: SimpleNamespace(content=contents),
    ))
    session = answer_models.Models(config_path=tmp_path / "llm.json").open_session()

    with pytest.raises(answer_models.ModelUnavailable) as failed:
        session.complete("question")

    assert failed.value.code == "LLM_UNAVAILABLE"
    assert str(failed.value) == "answer model is unavailable"


def test_session_rejects_missing_configuration_with_owned_error(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_MODEL")
    monkeypatch.delenv("LLM_API_KEY")
    models = answer_models.Models(config_path=tmp_path / "llm.json")

    with pytest.raises(answer_models.ModelUnavailable) as failed:
        models.open_session()

    assert failed.value.code == "LLM_NOT_CONFIGURED"
    assert "private-" not in str(failed.value)


def test_session_rejects_corrupt_saved_configuration(tmp_path):
    path = tmp_path / "llm.json"
    path.write_text('{"api_key":"private-corrupt-key"', encoding="utf-8")
    models = answer_models.Models(config_path=path)

    with pytest.raises(answer_models.ModelUnavailable) as failed:
        models.open_session()

    assert failed.value.code == "MODEL_CONFIG_UNAVAILABLE"
    assert "private-" not in str(failed.value)


@pytest.mark.parametrize("provider", ["openai", "deepseek"])
def test_real_sdk_session_uses_configured_provider_and_effective_endpoint(tmp_path, monkeypatch, provider):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.delenv("LLM_BASE_URL")
    variable = "OPENAI_API_BASE" if provider == "openai" else "DEEPSEEK_API_BASE"
    monkeypatch.setenv(variable, "https://gateway.example.test/v1")
    requests = []

    def send_response(_client, request, **_options):
        requests.append(request)
        return httpx2.Response(200, request=request, json={
            "id": "chatcmpl-test", "object": "chat.completion", "created": 0,
            "model": "environment-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "answer"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })

    monkeypatch.setattr(httpx2.Client, "send", send_response)
    models = answer_models.Models(config_path=tmp_path / "llm.json")
    models.prepare()
    saved = models.save(update(provider=provider, base_url=models.get()["base_url"]))
    monkeypatch.setenv(variable, "https://changed.example.test/v1")

    assert models.open_session().complete("What is RAG?") == "answer"

    assert len(requests) == 1
    assert str(requests[0].url) == saved["base_url"] + "/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer private-environment-key"
    body = json.loads(requests[0].content)
    assert body["model"] == "saved-model"
    assert body["messages"] == [{"role": "user", "content": "What is RAG?"}]


@pytest.mark.parametrize("provider", ["openai", "deepseek"])
def test_sdk_failure_does_not_retry_or_expose_upstream_details(tmp_path, monkeypatch, provider):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    requests = []

    def send_failure(_client, request, **_options):
        requests.append(request)
        return httpx2.Response(503, request=request, json={
            "error": {"message": "private-upstream-body", "type": "server_error"},
        })

    monkeypatch.setattr(httpx2.Client, "send", send_failure)
    session = answer_models.Models(config_path=tmp_path / "llm.json").open_session()

    with pytest.raises(answer_models.ModelUnavailable) as failed:
        session.complete("question")

    assert failed.value.code == "LLM_UNAVAILABLE"
    assert str(failed.value) == "answer model is unavailable"
    assert failed.value.__cause__ is None
    assert len(requests) == 1


@pytest.mark.parametrize("configuration", ["missing", "corrupt"])
def test_prepare_needs_no_settings_and_creates_no_client(tmp_path, monkeypatch, configuration):
    path = tmp_path / "llm.json"
    monkeypatch.delenv("LLM_MODEL")
    monkeypatch.delenv("LLM_API_KEY")
    if configuration == "corrupt":
        path.write_bytes(b'{"api_key":"private-corrupt-key"')
        (tmp_path / ".env").write_bytes(b"LLM_API_KEY=private-dotenv-key\xff")

    def unexpected_client(*_args, **_options):
        pytest.fail("SDK preparation must not construct a model or HTTP client")

    monkeypatch.setattr("langchain.chat_models.init_chat_model", unexpected_client)
    monkeypatch.setattr(httpx2.Client, "__init__", unexpected_client)
    monkeypatch.setattr(httpx2.AsyncClient, "__init__", unexpected_client)
    monkeypatch.setattr(socket.socket, "connect", unexpected_client)
    monkeypatch.setattr(socket, "getaddrinfo", unexpected_client)
    models = answer_models.Models(config_path=path)

    assert models.prepare() is None
    assert models.prepare() is None

    if configuration == "corrupt":
        assert path.read_bytes() == b'{"api_key":"private-corrupt-key"'
    else:
        assert not path.exists()


def test_prepare_does_not_freeze_configuration_before_the_first_question(tmp_path, monkeypatch):
    models = answer_models.Models(config_path=tmp_path / "llm.json")
    models.prepare()
    models.save(update(provider="deepseek", base_url="https://next.example.test/v1", api_key="private-next-key"))
    created = []

    def create_client(model, **options):
        created.append((model, options))
        return SimpleNamespace(invoke=lambda _messages: SimpleNamespace(content=model))

    monkeypatch.setattr("langchain.chat_models.init_chat_model", create_client)
    session = models.open_session()

    assert session.model_info == {"provider": "deepseek", "model": "saved-model"}
    assert created[0][1]["api_key"] == "private-next-key"
    assert created[0][1]["base_url"] == "https://next.example.test/v1"
    assert session.complete("question") == "saved-model"


@pytest.mark.parametrize("unavailable_module", [
    "langchain.chat_models", "langchain_openai", "langchain_deepseek", "openai.resources.chat",
])
def test_prepare_failure_is_sanitized_and_a_later_attempt_can_succeed(tmp_path, monkeypatch, unavailable_module):
    models = answer_models.Models(config_path=tmp_path / "llm.json")
    with monkeypatch.context() as missing_dependency:
        missing_dependency.setitem(sys.modules, unavailable_module, None)
        with pytest.raises(answer_models.ModelUnavailable) as failed:
            models.prepare()

        assert failed.value.code == "LLM_UNAVAILABLE"
        assert str(failed.value) == "answer model is unavailable"
        assert failed.value.__cause__ is None
        assert failed.value.__suppress_context__
        assert models.get()["configured"] is True
        assert models.save(update())["configured"] is True
        assert models.reset()["configured"] is True
        if unavailable_module == "langchain_deepseek":
            assert models.open_session().model_info["provider"] == "openai"

    assert models.prepare() is None
    assert models.open_session().model_info == {"provider": "openai", "model": "environment-model"}
