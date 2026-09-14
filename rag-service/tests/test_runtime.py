"""The UI can identify the configured model without receiving credentials."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app


def test_runtime_only_returns_public_model_fields(monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'openai')
    monkeypatch.setenv('LLM_MODEL', 'preview-model')
    monkeypatch.setenv('LLM_API_KEY', 'private-test-key-never-return-this')
    monkeypatch.setenv('LLM_BASE_URL', 'https://private-endpoint.example/v1')
    monkeypatch.setattr(app.state, 'settings', Settings(_env_file=None), raising=False)

    response = TestClient(app).get('/runtime')

    assert response.status_code == 200
    assert response.json()['llm'] == {
        'configured': True, 'provider': 'openai', 'model': 'preview-model',
    }
    assert 'private-test-key' not in response.text
    assert 'private-endpoint' not in response.text
    assert response.json()['embedding']['model'] == 'bge-m3'


def test_runtime_reports_missing_configuration_without_failing(monkeypatch):
    monkeypatch.setenv('LLM_MODEL', '')
    monkeypatch.setenv('LLM_API_KEY', '')
    monkeypatch.setattr(app.state, 'settings', Settings(_env_file=None), raising=False)

    response = TestClient(app).get('/runtime')

    assert response.status_code == 200
    assert response.json()['llm'] == {'configured': False, 'provider': None, 'model': None}
