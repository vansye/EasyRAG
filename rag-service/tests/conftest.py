"""Tests must never read or modify a user's saved answer-model credentials."""

import pytest


@pytest.fixture(autouse=True)
def isolated_model_config(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_CONFIG_FILE", str(tmp_path / "llm.json"))
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    for name in ("OPENAI_API_BASE", "OPENAI_BASE_URL", "DEEPSEEK_API_BASE", "LANGSMITH_GATEWAY"):
        monkeypatch.delenv(name, raising=False)
