"""Exercise ASGI sends directly: TestClient otherwise buffers streamed output."""

import asyncio
from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace

import pytest

from app.application.questions import QuestionEvent
from app.http import create_app
from tests.test_application_questions import workflow


def make_services(generate):
    active, closed = Event(), Event()
    @contextmanager
    def request_work():
        active.set()
        try:
            yield
        finally:
            active.clear()
            closed.set()
    return SimpleNamespace(questions=SimpleNamespace(stream=generate), request_work=request_work,
                           active=active, closed=closed)


async def run_request(services, on_send, *, disconnect=None, body=b'{"question":"question"}'):
    app = create_app(services)
    app.state.services = services
    first = True
    async def receive():
        nonlocal first
        if first:
            first = False
            return {'type':'http.request', 'body':body, 'more_body':False}
        if disconnect is not None:
            await disconnect.wait()
            return {'type':'http.disconnect'}
        await asyncio.Future()
    scope = {'type':'http','asgi':{'version':'3.0','spec_version':'2.3'},'http_version':'1.1',
             'method':'POST','scheme':'http','path':'/api/questions/stream','raw_path':b'/api/questions/stream',
             'query_string':b'', 'root_path':'', 'headers':[(b'content-type',b'application/json')],
             'client':('test',123),'server':('test',80)}
    await asyncio.wait_for(app(scope, receive, on_send), 8)


def test_delta_reaches_client_before_generation_finishes():
    release = Event()
    def generate(question, control):
        yield QuestionEvent('delta','first')
        assert release.wait(5), 'HTTP buffered the first delta'
        yield QuestionEvent('done', {'history_id':1})
    services = make_services(generate)
    messages = []
    async def send(message):
        messages.append(message)
        if b'event: delta' in message.get('body',b''):
            assert services.active.is_set()
            release.set()
    try:
        asyncio.run(run_request(services, send))
    finally:
        release.set()
    data = b''.join(m.get('body',b'') for m in messages)
    assert b'event: delta' in data and data.count(b'event: done') == 1
    assert services.closed.is_set()


def test_disconnect_unblocks_full_queue_and_closes_worker():
    ended = Event()
    def generate(question, control):
        try:
            for index in range(10000):
                yield QuestionEvent('delta',str(index))
        finally:
            ended.set()
    services = make_services(generate)
    async def scenario():
        disconnect = asyncio.Event()
        async def send(message):
            if b'event: delta' in message.get('body',b''):
                await asyncio.sleep(.1)
                disconnect.set()
                await asyncio.Future()
        await run_request(services, send, disconnect=disconnect)
    asyncio.run(scenario())
    assert ended.is_set() and services.closed.is_set()


def test_error_after_headers_is_one_safe_terminal_event():
    def generate(question, control):
        yield QuestionEvent('delta','partial')
        raise RuntimeError('private upstream body')
    services = make_services(generate)
    messages=[]
    async def send(message):
        messages.append(message)
    asyncio.run(run_request(services, send))
    data=b''.join(m.get('body',b'') for m in messages)
    assert data.count(b'event: error') == 1 and b'event: done' not in data
    assert b'private' not in data and services.closed.is_set()


def test_invalid_question_fails_before_stream_starts():
    def generate(*args):
        pytest.fail('invalid input reached worker')
    services=make_services(generate)
    messages=[]
    async def send(message):
        messages.append(message)
    asyncio.run(run_request(services, send, body=b'{"question":"  "}'))
    assert messages[0]['status'] == 400 and not services.active.is_set()


def test_history_write_failure_has_error_without_done(workflow):
    from app.modules.knowledge.public import DatabaseUnavailable
    from tests.test_question_stream import setup_stream
    setup_stream(workflow)
    workflow.a.save_history.side_effect = DatabaseUnavailable('private SQL')
    services = make_services(workflow.questions.stream)
    messages = []
    async def send(message):
        messages.append(message)
    asyncio.run(run_request(services, send))
    data = b''.join(m.get('body', b'') for m in messages)
    assert data.count(b'event: error') == 1 and b'event: done' not in data
    assert b'private' not in data
    from app.application.gate import State
    assert workflow.gate.state == State.READY
