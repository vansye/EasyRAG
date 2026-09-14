"""Tests must never read or modify a user's saved answer-model credentials."""

import pytest

from app import model_config


@pytest.fixture(autouse=True)
def isolated_model_config(monkeypatch, tmp_path):
    monkeypatch.setattr(model_config, "CONFIG_PATH", tmp_path / "llm.json")
    monkeypatch.delenv("LLM_CONFIG_FILE", raising=False)
    for name in ("OPENAI_API_BASE", "OPENAI_BASE_URL", "DEEPSEEK_API_BASE", "LANGSMITH_GATEWAY"):
        monkeypatch.delenv(name, raising=False)
