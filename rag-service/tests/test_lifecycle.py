"""A cancelled HTTP task does not end its worker's lease or process ownership."""

import asyncio
from contextlib import suppress
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from app.application.gate import Operation, State
from app.application.process_lock import ProcessLock, RuntimeLockUnavailable
from app.application.runtime import RuntimeSettings, Services
from app.http import create_app
from app.modules.answer_models.public import Models
from app.modules.knowledge.public import Knowledge
from app.modules.retrieval.public import Retrieval, SearchHit


@pytest.mark.parametrize('native_cancel', [False, True])
def test_stream_disconnect_waits_for_model_and_shutdown(tmp_path, native_cancel):
    started, release, stream_closed = Event(), Event(), Event()
    a, b, models = Mock(spec=Knowledge), Mock(spec=Retrieval), Mock(spec=Models)
    services = Services(a, b, models)
    services.gate.try_acquire(Operation.RECOVERY).lease.confirm_completion()
    b.search.return_value = (SearchHit(1, 1, 'Evidence', '', .9),)
    from app.modules.knowledge.public import Source
    a.sources.return_value = (Source(1, 1, 'Note', 'Evidence', 0, 8, ''),)
    models.open_session.return_value.complete.return_value = '{"verdict":"SUFFICIENT"}'
    def stream(prompt):
        try:
            started.set()
            assert release.wait(5)
            yield 'Answer [1]'
        finally:
            stream_closed.set()
    models.open_session.return_value.stream.side_effect = stream
    lock_path = tmp_path / 'stream.lock'
    app = create_app(services, settings=RuntimeSettings(_env_file=None, runtime_lock_file=lock_path))
    async def exercise():
        from tests.test_http_stream import run_request
        disconnect = asyncio.Event()
        async def send(message):
            pass
        context = app.router.lifespan_context(app)
        await context.__aenter__()
        try:
            request = asyncio.create_task(run_request(services, send, disconnect=disconnect))
            shutdown = None
            try:
                assert await asyncio.to_thread(started.wait, 2)
                if native_cancel:
                    request.cancel()
                else:
                    disconnect.set()
                await asyncio.sleep(.05)
                assert not request.done()
                assert services.gate.try_acquire(Operation.MUTATION).lease is None
                shutdown = asyncio.create_task(context.__aexit__(None, None, None))
                await asyncio.sleep(.05)
                assert not shutdown.done()
                a.close.assert_not_called()
                b.close.assert_not_called()
                with pytest.raises(RuntimeLockUnavailable):
                    with ProcessLock(lock_path):
                        pass
            finally:
                release.set()
                with suppress(asyncio.CancelledError):
                    await asyncio.wait_for(request, 3)
                if shutdown is not None:
                    await asyncio.wait_for(shutdown, 3)
            a.save_history.assert_not_called()
            assert stream_closed.is_set()
            a.close.assert_called_once()
            b.close.assert_called_once()
        finally:
            if shutdown is None:
                await context.__aexit__(None, None, None)
        with ProcessLock(lock_path):
            pass
    asyncio.run(exercise())


@pytest.mark.parametrize('operation', ['question', 'upload'])
def test_cancelled_worker_finishes_before_resources_close_and_lock_releases(tmp_path, operation):
    started, release = Event(), Event()
    a, b, models = Mock(spec=Knowledge), Mock(spec=Retrieval), Mock(spec=Models)
    services = Services(a, b, models)
    services.gate.try_acquire(Operation.RECOVERY).lease.confirm_completion()
    lock_path = tmp_path / 'backend.lock'
    app = create_app(services, settings=RuntimeSettings(_env_file=None, runtime_lock_file=lock_path))

    def block():
        started.set()
        assert release.wait(5), 'test did not release the admitted worker'

    if operation == 'question':
        def complete(_prompt):
            block()
            return '{"verdict":"NONE"}'
        models.open_session.return_value.complete.side_effect = complete
        b.search.return_value = (SearchHit(1, 1, 'Unrelated evidence', '', 0.1),)
    else:
        def create(_filename, _data):
            block()
            return SimpleNamespace(id=1, title='Note', source_type='UPLOAD', index_status='PENDING')
        a.create.side_effect = create
        a.get.return_value = SimpleNamespace(index_status='INDEXED')

    async def exercise():
        context = app.router.lifespan_context(app)
        await context.__aenter__()
        shutdown = None
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                call = (client.post('/api/questions', json={'question': 'Question?'}) if operation == 'question'
                        else client.post('/api/documents', files={'file': ('note.md', b'Note')}))
                request = asyncio.create_task(call)
                assert await asyncio.to_thread(started.wait, 2)
                request.cancel()
                with suppress(asyncio.CancelledError):
                    await request
                if operation == 'question':
                    assert services.gate.state == State.QUERYING
                    assert services.gate.try_acquire(Operation.MUTATION).lease is None
                shutdown = asyncio.create_task(context.__aexit__(None, None, None))
                await asyncio.sleep(.05)
                assert not shutdown.done(), 'shutdown ended while the HTTP worker still owned resources'
                a.close.assert_not_called()
                b.close.assert_not_called()
                with pytest.raises(RuntimeLockUnavailable):
                    with ProcessLock(lock_path):
                        pass
        finally:
            release.set()
            if shutdown is None:
                shutdown = asyncio.create_task(context.__aexit__(None, None, None))
            await asyncio.wait_for(shutdown, 3)
        a.close.assert_called_once_with()
        b.close.assert_called_once_with()
        with ProcessLock(lock_path):
            pass

    asyncio.run(exercise())


def test_cancelled_construction_and_shutdown_keep_lock_until_cleanup_finishes(tmp_path, monkeypatch):
    constructing, finish_construction, closing, finish_close = Event(), Event(), Event(), Event()
    resources = Mock()

    def build():
        constructing.set()
        assert finish_construction.wait(5)
        return resources

    def close():
        closing.set()
        assert finish_close.wait(5)

    resources.close.side_effect = close
    monkeypatch.setattr(Services, 'create', build)
    path = tmp_path / 'backend.lock'
    app = create_app(settings=RuntimeSettings(_env_file=None, runtime_lock_file=path))

    async def exercise():
        context = app.router.lifespan_context(app)
        startup = asyncio.create_task(context.__aenter__())
        try:
            assert await asyncio.to_thread(constructing.wait, 2)
            startup.cancel()
            await asyncio.sleep(.02)
            assert not startup.done()
            with pytest.raises(RuntimeLockUnavailable):
                with ProcessLock(path):
                    pass
            finish_construction.set()
            assert await asyncio.to_thread(closing.wait, 2)
            startup.cancel()
            await asyncio.sleep(.02)
            assert not startup.done()
            with pytest.raises(RuntimeLockUnavailable):
                with ProcessLock(path):
                    pass
        finally:
            finish_construction.set()
            finish_close.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(startup, 3)
        resources.close.assert_called_once_with()
        with ProcessLock(path):
            pass

    asyncio.run(exercise())


def test_process_lock_is_acquired_before_constructing_any_resources(tmp_path, monkeypatch):
    build = Mock()
    monkeypatch.setattr(Services, 'create', build)
    path = tmp_path / 'backend.lock'
    app = create_app(settings=RuntimeSettings(_env_file=None, runtime_lock_file=path))

    async def exercise():
        with ProcessLock(path), pytest.raises(RuntimeLockUnavailable):
            async with app.router.lifespan_context(app):
                pass
        build.assert_not_called()

    asyncio.run(exercise())
