"""Stream text through the frozen model session, including consumer cleanup."""

from types import SimpleNamespace

import pytest

from app.modules.answer_models.public import Models, ModelUnavailable
from tests.test_answer_models_module import isolated_environment


def session_for(monkeypatch, tmp_path, stream):
    monkeypatch.setattr('langchain.chat_models.init_chat_model', lambda *a, **kw:
                        SimpleNamespace(stream=stream))
    return Models(tmp_path / 'model.json').open_session()


def test_incremental_text_preserves_whitespace_and_closes_on_consumer_exit(monkeypatch, tmp_path):
    calls = []
    def stream(messages):
        calls.append(messages[0].content)
        try:
            yield SimpleNamespace(content='')
            yield SimpleNamespace(content=' answer ')
            calls.append('second')
            yield SimpleNamespace(content='[1]\n')
        finally:
            calls.append('closed')
    session = session_for(monkeypatch, tmp_path, stream)
    iterator = session.stream('question')
    assert next(iterator) == ' answer '
    assert calls == ['question']
    iterator.close()
    assert calls == ['question', 'closed']


@pytest.mark.parametrize('chunks', [[], [''], [' ', '\n'], [['not text']], ['ok', None]])
def test_invalid_or_empty_stream_is_a_safe_failure(monkeypatch, tmp_path, chunks):
    closed = []
    def stream(_messages):
        try:
            for content in chunks:
                yield SimpleNamespace(content=content)
        finally:
            closed.append(True)
    session = session_for(monkeypatch, tmp_path, stream)
    with pytest.raises(ModelUnavailable, match='answer model is unavailable'):
        list(session.stream('question'))
    assert closed == [True]


def test_midstream_error_is_sanitized_without_retry(monkeypatch, tmp_path):
    calls = []
    def stream(_messages):
        calls.append(1)
        yield SimpleNamespace(content='partial')
        raise RuntimeError('private upstream data')
    iterator = session_for(monkeypatch, tmp_path, stream).stream('question')
    assert next(iterator) == 'partial'
    with pytest.raises(ModelUnavailable) as error:
        next(iterator)
    assert 'private' not in str(error.value)
    assert calls == [1]


@pytest.mark.parametrize('provider', ['openai', 'deepseek'])
@pytest.mark.parametrize('early_close', [False, True])
def test_real_sdk_stream_keeps_frozen_model_and_closes_http(monkeypatch, tmp_path, provider, early_close):
    import json
    import httpx2

    monkeypatch.setenv('LLM_PROVIDER', provider)
    requests, closed = [], []

    class Body(httpx2.SyncByteStream):
        def __iter__(self):
            for content in ['Hello ', '[1]']:
                chunk = {'id': 'chat-test', 'object': 'chat.completion.chunk', 'created': 0,
                         'model': 'environment-model', 'choices': [
                             {'index': 0, 'delta': {'content': content}, 'finish_reason': None}]}
                yield ('data: ' + json.dumps(chunk) + '\n\n').encode()
            yield b'data: [DONE]\n\n'
        def close(self):
            closed.append(True)

    def send(_client, request, **kwargs):
        requests.append(json.loads(request.content))
        return httpx2.Response(200, request=request, headers={'content-type': 'text/event-stream'}, stream=Body())

    monkeypatch.setattr(httpx2.Client, 'send', send)
    session = Models(tmp_path / 'model.json').open_session()
    monkeypatch.setenv('LLM_MODEL', 'changed-model')
    stream = session.stream('question')
    if early_close:
        assert next(stream) == 'Hello '
        stream.close()
    else:
        assert ''.join(stream) == 'Hello [1]'
    assert len(requests) == 1 and requests[0]['stream'] is True
    assert requests[0]['model'] == 'environment-model'
    assert closed
