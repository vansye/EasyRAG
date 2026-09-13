import asyncio
import json
import os
from pathlib import Path

import httpx2
import pytest
from openai import InternalServerError
from pydantic import ValidationError

from app import config


@pytest.fixture(autouse=True)
def clean_llm_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for variable_name in list(os.environ):
        if variable_name.startswith("LLM_"):
            monkeypatch.delenv(variable_name)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")


def test_llm_settings_read_environment(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-api-key")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "12.5")

    settings = config.LlmSettings(_env_file=None)

    assert settings.provider == "deepseek"
    assert settings.model == "deepseek-chat"
    assert str(settings.base_url) == "https://llm.example.test/v1"
    assert settings.api_key.get_secret_value() == "test-api-key"
    assert settings.timeout_seconds == 12.5
    assert "test-api-key" not in repr(settings)


def test_llm_settings_read_dotenv_without_embedding_fields(tmp_path):
    env_file = tmp_path / "rag.env"
    env_file.write_text(
        "LLM_PROVIDER=openai\n"
        "LLM_MODEL=local-model\n"
        "LLM_BASE_URL=http://127.0.0.1:11434/v1\n"
        "LLM_API_KEY=ollama\n"
        "EMBEDDING_MODEL=bge-m3\n",
        encoding="utf-8",
    )

    settings = config.LlmSettings(_env_file=env_file)

    assert settings.provider == "openai"
    assert settings.model == "local-model"
    assert str(settings.base_url) == "http://127.0.0.1:11434/v1"
    assert settings.api_key.get_secret_value() == "ollama"


@pytest.mark.parametrize("missing_field", ["model", "api_key"])
def test_llm_settings_require_model_and_key(missing_field):
    values = {"model": "chat-model", "api_key": "test-api-key"}
    del values[missing_field]

    with pytest.raises(ValidationError, match=missing_field):
        config.LlmSettings(_env_file=None, **values)


@pytest.mark.parametrize("timeout_seconds", [0, -1])
def test_llm_settings_reject_non_positive_timeout(timeout_seconds):
    with pytest.raises(ValidationError, match="timeout_seconds"):
        config.LlmSettings(
            _env_file=None,
            model="chat-model",
            api_key="test-api-key",
            timeout_seconds=timeout_seconds,
        )


def test_llm_settings_reject_unsupported_provider():
    with pytest.raises(ValidationError, match="provider"):
        config.LlmSettings(
            _env_file=None,
            provider="unsupported",
            model="chat-model",
            api_key="test-api-key",
        )


def test_invalid_llm_configuration_does_not_expose_api_key():
    with pytest.raises(ValidationError) as error:
        config.LlmSettings(_env_file=None, api_key="test-sensitive-api-key")

    assert "test-sensitive-api-key" not in str(error.value)


def test_index_settings_do_not_require_llm_configuration():
    settings = config.Settings(_env_file=None)

    assert settings.embedding_dim > 0


@pytest.mark.parametrize(
    "provider,model_name,expected_class",
    [
        ("openai", "chat-model", "ChatOpenAI"),
        ("deepseek", "deepseek-chat", "ChatDeepSeek"),
    ],
)
def test_factory_creates_configured_provider(provider, model_name, expected_class):
    from app.llm import create_chat_model

    settings = config.LlmSettings(
        _env_file=None,
        provider=provider,
        model=model_name,
        base_url="https://llm.example.test/v1",
        api_key="test-api-key",
        timeout_seconds=12.5,
    )

    chat_model = create_chat_model(settings)

    assert type(chat_model).__name__ == expected_class
    assert chat_model.model_name == model_name
    assert str(chat_model.root_client.base_url) == "https://llm.example.test/v1/"
    assert chat_model.root_client.api_key == "test-api-key"
    assert chat_model.request_timeout == 12.5
    assert chat_model.max_retries == 0


def test_factory_uses_environment_when_settings_are_omitted(monkeypatch):
    from app.llm import create_chat_model

    monkeypatch.setenv("LLM_MODEL", "configured-model")
    monkeypatch.setenv("LLM_API_KEY", "test-api-key")

    chat_model = create_chat_model()

    assert chat_model.model_name == "configured-model"
    assert str(chat_model.root_client.base_url) == "https://api.openai.com/v1/"


def test_factory_defers_required_configuration_until_called():
    from app.llm import create_chat_model

    with pytest.raises(ValidationError, match="model"):
        create_chat_model()


@pytest.mark.parametrize("provider", ["openai", "deepseek"])
def test_model_completes_async_tool_round_trip(provider, monkeypatch):
    from langchain_core.messages import HumanMessage
    from langchain_core.tools import tool

    from app.llm import create_chat_model

    requests = []
    searched_queries = []
    model_name = "deepseek-chat" if provider == "deepseek" else "chat-model"
    answer = "RAG combines retrieval and generation. [7]"

    @tool
    def search_knowledge(query: str) -> str:
        """Search the test knowledge base for relevant chunks."""
        searched_queries.append(query)
        return json.dumps({"chunk_id": 7, "text": "RAG combines retrieval and generation."})

    async def send_response(_client, request, **_options):
        assert str(request.url) == "https://llm.example.test/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-api-key"
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_search",
                        "type": "function",
                        "function": {
                            "name": "search_knowledge",
                            "arguments": json.dumps({"query": "RAG"}),
                        },
                    }
                ],
            }
            finish_reason = "tool_calls"
        else:
            assert len(requests) == 2
            message = {"role": "assistant", "content": answer}
            finish_reason = "stop"
        return httpx2.Response(
            200,
            request=request,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": model_name,
                "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    monkeypatch.setattr(httpx2.AsyncClient, "send", send_response)
    settings = config.LlmSettings(
        _env_file=None,
        provider=provider,
        model=model_name,
        base_url="https://llm.example.test/v1",
        api_key="test-api-key",
    )
    chat_model = create_chat_model(settings).bind_tools([search_knowledge])

    async def converse():
        messages = [HumanMessage(content="What is RAG?")]
        decision = await chat_model.ainvoke(messages)
        assert len(decision.tool_calls) == 1
        tool_call = decision.tool_calls[0]
        assert tool_call["name"] == "search_knowledge"
        assert tool_call["args"] == {"query": "RAG"}
        tool_result = search_knowledge.invoke(tool_call)
        return await chat_model.ainvoke([*messages, decision, tool_result])

    response = asyncio.run(converse())

    assert response.content == answer
    assert searched_queries == ["RAG"]
    assert len(requests) == 2
    assert requests[0]["model"] == model_name
    function_schema = requests[0]["tools"][0]["function"]
    assert function_schema["name"] == "search_knowledge"
    assert function_schema["parameters"]["properties"]["query"]["type"] == "string"
    tool_message = requests[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_search"
    assert json.loads(tool_message["content"])["chunk_id"] == 7


def test_model_propagates_upstream_failure_without_retry(monkeypatch):
    from app.llm import create_chat_model

    requests = []

    def send_failure(_client, request, **_options):
        requests.append(request)
        return httpx2.Response(
            503,
            request=request,
            json={"error": {"message": "temporarily unavailable", "type": "server_error"}},
        )

    monkeypatch.setattr(httpx2.Client, "send", send_failure)
    chat_model = create_chat_model(
        config.LlmSettings(
            _env_file=None,
            model="chat-model",
            base_url="https://llm.example.test/v1",
            api_key="test-api-key",
        )
    )

    with pytest.raises(InternalServerError):
        chat_model.invoke("What is RAG?")

    assert len(requests) == 1


def test_env_example_documents_all_required_llm_keys():
    """.env.example 是模板，不是可直接运行的配置。

    C-5 定了"API 为主"之后，模板不该内置任何可用端点——用户必须自己填
    密钥与地址。所以这里验的是"模板完整"（必填键齐全、形式正确），
    而不是"模板开箱即用"（旧口径，会迫使模板长期内置本地 Ollama 默认值）。
    """
    env_file = Path(__file__).resolve().parents[1] / ".env.example"
    text = env_file.read_text(encoding="utf-8")

    # LlmSettings 的必填项必须在模板里出现，否则新人不知道要配什么
    for required_key in ("LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "LLM_API_KEY"):
        assert f"{required_key}=" in text, f"{required_key} 缺失，新人照抄会漏配"

    # 占位符形式：不能留真实密钥或可用端点（否则等于内置了一套默认配置）
    assert "LLM_MODEL=<" in text
    assert "LLM_API_KEY=<" in text
    # provider 仍给确定值——它是枚举，不是用户自由填的
    assert "LLM_PROVIDER=openai" in text
    # 超时按最慢部署形态（本地模型）定，不能退回 60 秒
    assert "LLM_TIMEOUT_SECONDS=180" in text
    # 三种常见部署形态都要有示例可抄
    assert "deepseek" in text.lower() and "ollama" in text.lower()
