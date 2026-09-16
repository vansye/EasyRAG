"""SDK preparation belongs to startup without making model settings mandatory."""

import asyncio
import logging
import os
from threading import Event
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.application import runtime
from app.application.process_lock import ProcessLock, RuntimeLockUnavailable
from app.http import create_app
from app.modules.answer_models.public import ModelUnavailable, Models
from app.modules.knowledge.public import Knowledge
from app.modules.retrieval.public import Retrieval


def application(monkeypatch, tmp_path, models):
    knowledge, retrieval = Mock(spec=Knowledge), Mock(spec=Retrieval)
    knowledge.health.return_value = {"status": "UP"}
    retrieval.health.return_value = {"status": "UP"}
    retrieval.runtime_info.return_value = {"embedding": {"provider": "test", "model": "test", "dim": 3}}
    monkeypatch.setattr(runtime, "Knowledge", lambda: knowledge)
    monkeypatch.setattr(runtime, "Retrieval", lambda: retrieval)
    monkeypatch.setattr(runtime, "Models", lambda: models)
    settings = runtime.RuntimeSettings(_env_file=None, runtime_lock_file=tmp_path / "backend.lock")
    return create_app(settings=settings), knowledge, retrieval, settings.runtime_lock_file


@pytest.mark.parametrize("cancel_startup", [False, True])
def test_preparation_finishes_under_process_lock_before_serving_or_cancelling(tmp_path, monkeypatch, cancel_startup):
    started, release = Event(), Event()

    def prepare():
        started.set()
        assert release.wait(5), "test did not release SDK preparation"

    models = Mock(spec=Models)
    models.prepare = Mock(side_effect=prepare)
    app, knowledge, retrieval, lock_path = application(monkeypatch, tmp_path, models)

    async def exercise():
        context = app.router.lifespan_context(app)
        startup = asyncio.create_task(context.__aenter__())
        try:
            assert await asyncio.to_thread(started.wait, 2)
            assert not startup.done(), "HTTP startup finished before SDK preparation"
            with pytest.raises(RuntimeLockUnavailable):
                with ProcessLock(lock_path):
                    pass
            if cancel_startup:
                startup.cancel()
                await asyncio.sleep(.02)
                assert not startup.done(), "cancellation orphaned the preparing worker"
                knowledge.close.assert_not_called()
                retrieval.close.assert_not_called()
                with pytest.raises(RuntimeLockUnavailable):
                    with ProcessLock(lock_path):
                        pass
            release.set()
            if cancel_startup:
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(startup, 3)
            else:
                await asyncio.wait_for(startup, 3)
                assert app.state.services.models is models
                assert app.state.services.gate.state == "RECOVERY_REQUIRED"
        finally:
            release.set()
            if not startup.done():
                try:
                    await asyncio.wait_for(startup, 3)
                except asyncio.CancelledError:
                    pass
            if not startup.cancelled() and startup.exception() is None:
                await context.__aexit__(None, None, None)

    asyncio.run(exercise())
    models.prepare.assert_called_once_with()
    models.open_session.assert_not_called()
    knowledge.close.assert_called_once_with()
    retrieval.close.assert_called_once_with()
    with ProcessLock(lock_path):
        pass


def test_sdk_preparation_failure_preserves_startup_health_and_model_configuration(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    for key in tuple(os.environ):
        if key.startswith("LLM_"):
            monkeypatch.delenv(key)
    models = Models(config_path=tmp_path / "llm.json")
    failure = ModelUnavailable()
    failure.args = ("private-sdk-failure-details",)
    prepare = Mock(side_effect=failure)
    monkeypatch.setattr(models, "prepare", prepare, raising=False)
    app, knowledge, retrieval, lock_path = application(monkeypatch, tmp_path, models)

    with caplog.at_level(logging.WARNING, logger="app.application.runtime"), TestClient(app) as client:
        prepare.assert_called_once_with()
        assert client.get("/health").json()["status"] == "UP"
        assert client.get("/api/runtime").json()["state"] == "RECOVERY_REQUIRED"
        assert client.get("/api/model-config").json()["configured"] is False
        response = client.put("/api/model-config", json={
            "provider": "openai", "model": "new-model",
            "api_key": "private-new-key", "base_url": "https://models.example.test/v1",
        })
        assert response.status_code == 200
        assert response.json()["configured"] is True
        assert client.get("/api/model-config").json()["model"] == "new-model"

    logs = [record for record in caplog.records if record.name == "app.application.runtime"]
    assert len(logs) == 1
    assert "LLM_UNAVAILABLE" in logs[0].getMessage()
    assert logs[0].exc_info is None
    assert "private-" not in caplog.text
    knowledge.close.assert_called_once_with()
    retrieval.close.assert_called_once_with()
    with ProcessLock(lock_path):
        pass
