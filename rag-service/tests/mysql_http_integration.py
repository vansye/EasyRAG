"""Real MySQL/Chroma and HTTP model transports through the final public REST API."""

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.application.runtime import RuntimeSettings, Services
from app.application.process_lock import ProcessLock
from app.application.recovery import verify_consistency
from app.http import create_app
from app.modules.answer_models.public import Models
from app.modules.knowledge.public import Knowledge
from app.modules.retrieval.public import Retrieval
from tests.mysql_rebuild_integration import embedding_server, seed_documents, services, source_truth


@pytest.fixture
def chat_server(monkeypatch):
    for name in ('LANGSMITH_TRACING', 'LANGCHAIN_TRACING_V2'):
        monkeypatch.setenv(name, 'false')
    monkeypatch.setenv('LLM_MODEL', '')
    monkeypatch.setenv('LLM_API_KEY', '')
    state = SimpleNamespace(calls=[], after_judge=None, fail=False, verdict=None)

    class Handler(BaseHTTPRequestHandler):
        timeout = 3

        def log_message(self, *_):
            pass

        def do_POST(self):
            if self.path != '/v1/chat/completions':
                self.send_error(404)
                return
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            prompt = payload['messages'][0]['content']
            state.calls.append((payload['model'], prompt))
            if state.fail:
                status, body = 503, {'error': {'message': 'private-upstream-response'}}
            else:
                if '只输出 JSON' in prompt:
                    evidence = prompt.split('检索片段：\n', 1)[1].split('\n\n判定规则：', 1)[0]
                    answer = json.dumps({'verdict': state.verdict or ('SUFFICIENT' if evidence.strip() else 'NONE')})
                    callback, state.after_judge = state.after_judge, None
                    if callback is not None:
                        callback()
                else:
                    answer = '报名口令是蓝舟8642。[1]' if '蓝舟8642' in prompt else '报名口令是白鹭3718。[1]'
                status, body = 200, {
                    'id': 'chat-contract', 'object': 'chat.completion', 'created': 1, 'model': payload['model'],
                    'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': answer}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2},
                }
            data = json.dumps(body).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, name='contract-chat')
    thread.start()
    state.url = f'http://127.0.0.1:{server.server_port}/v1'
    try:
        yield state
    finally:
        try:
            server.shutdown()
            thread.join(timeout=5)
        finally:
            server.server_close()
        assert not thread.is_alive()


@pytest.fixture
def workspace(services, tmp_path, chat_server, monkeypatch):
    models = Models(tmp_path / 'models.json')
    active = Services(services.a, services.b, models)
    futures = []
    submit = active.queue.submit

    def record(*args, **kwargs):
        future = submit(*args, **kwargs)
        futures.append(future)
        return future

    monkeypatch.setattr(active.queue, 'submit', record)
    app = create_app(active, settings=RuntimeSettings(_env_file=None, runtime_lock_file=tmp_path / 'backend.lock'))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, active=active, storage=services, models=models,
                              chat=chat_server, futures=futures, app=app)


def indexed(workspace):
    result = workspace.futures[-1].result(timeout=10)
    assert result.outcome == 'INDEXED', result
    assert workspace.client.get('/api/runtime').json()['state'] == 'READY'


def configure(workspace, model):
    response = workspace.client.put('/api/model-config', json={
        'provider': 'openai', 'model': model, 'base_url': workspace.chat.url, 'api_key': 'contract-test-key',
    })
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert 'contract-test-key' not in response.text


def test_public_crud_qa_model_snapshot_and_deletion_sync(workspace):
    w, question = workspace, '阅读活动的报名口令是什么？'
    health = w.client.get('/health')
    assert health.status_code == 200 and health.json()['db']['status'] == 'UP'
    assert health.json()['retrieval']['status'] == 'UP'
    assert w.client.post('/api/admin/ready').status_code == 200
    configure(w, 'first-model')
    body = '---\ntitle: 100%_ 活动\ntags: [guide]\n---\n# 阅读活动\n\n报名口令是白鹭3718。\n'
    created = w.client.post('/api/documents', files={'file': ('contract.md', body.encode('utf-8'))})
    assert created.status_code == 201 and created.json()['index_status'] == 'PENDING'
    document_id = created.json()['id']
    indexed(w)
    assert w.client.get('/api/documents', params={'q': '%_'}).json()['total'] == 1
    assert w.client.get('/api/documents', params={'q': '%absent_'}).json()['total'] == 0
    initial_chunks = w.client.get(f'/api/documents/{document_id}/chunks').json()['items']
    assert initial_chunks
    for chunk in initial_chunks:
        assert body.encode('utf-8')[chunk['byte_start']:chunk['byte_end']].decode('utf-8') == chunk['text']

    before_question = source_truth(w.storage.a)
    before_vectors = w.storage.b.inspect()

    # Save during the real HTTP judgment call. Generation must retain this question's session.
    w.chat.after_judge = lambda: w.models.save({
        'provider': 'openai', 'model': 'second-model', 'base_url': w.chat.url, 'api_key': '',
    })
    answer = w.client.post('/api/questions', json={'question': question})
    assert answer.status_code == 200 and answer.json()['status'] == 'ANSWERED'
    assert '白鹭3718' in answer.json()['answer']
    assert [call[0] for call in w.chat.calls] == ['first-model', 'first-model']
    assert answer.json()['sources'][0]['chunk_id'] == answer.json()['trace'][-1]['retrieved'][0]['chunk_id']
    assert answer.json()['sources'][0]['document_id'] == document_id
    first = answer.json()
    first_id = first['history_id']
    assert first['model'] == {'provider': 'openai', 'model': 'first-model'}
    assert isinstance(first['elapsed_ms'], int) and first['elapsed_ms'] >= 0
    first_snapshot = {'id': first_id, 'question': question,
                      **{key: value for key, value in first.items() if key != 'history_id'}}
    assert w.client.get(f'/api/question-history/{first_id}').json() == first_snapshot
    assert source_truth(w.storage.a) == before_question and w.storage.b.inspect() == before_vectors

    same = w.client.put(f'/api/documents/{document_id}', json={'content': '\t' + body.replace('\n', '\r\n') + '\n'})
    assert same.status_code == 200 and same.json()['reindexed'] is False
    assert w.client.get(f'/api/documents/{document_id}').json()['content'] == body
    assert w.client.get(f'/api/documents/{document_id}/chunks').json()['items'] == initial_chunks

    updated = '# 阅读活动\n\n报名口令是蓝舟8642。\n'
    change = w.client.put(f'/api/documents/{document_id}', json={'content': updated})
    assert change.status_code == 200 and change.json()['reindexed'] is True
    indexed(w)
    assert all(entry.tags == () for entry in w.storage.b.inspect())
    answer = w.client.post('/api/questions', json={'question': question})
    assert answer.status_code == 200 and '蓝舟8642' in answer.json()['answer']
    assert '白鹭3718' not in str(answer.json()['sources'])
    assert [call[0] for call in w.chat.calls[-2:]] == ['second-model', 'second-model']
    second_id = answer.json()['history_id']
    assert second_id != first_id and answer.json()['model']['model'] == 'second-model'
    assert w.client.get(f'/api/question-history/{first_id}').json() == first_snapshot
    assert w.client.post(f'/api/documents/{document_id}/reindex').status_code == 202
    indexed(w)
    w.chat.fail = True
    failure = w.client.post('/api/questions', json={'question': question})
    assert failure.status_code == 502 and 'private-upstream-response' not in failure.text
    assert w.client.get('/api/question-history').json()['total'] == 2
    w.chat.fail = False
    removed = w.client.delete(f'/api/documents/{document_id}')
    assert removed.status_code == 204 and removed.content == b''
    assert w.client.get(f'/api/documents/{document_id}').status_code == 404
    assert w.client.get('/api/documents').json()['total'] == 0
    assert w.storage.b.inspect() == ()
    refused = w.client.post('/api/questions', json={'question': question})
    assert refused.status_code == 200 and refused.json()['status'] == 'REFUSED'
    assert refused.json()['sources'] == [] and refused.json()['trace'][-1]['retrieved'] == []
    assert w.client.delete('/api/model-config').json()['configured'] is False
    refused_id = refused.json()['history_id']
    assert w.client.get(f'/api/question-history/{first_id}').json() == first_snapshot
    page = w.client.get('/api/question-history', params={'size': 1}).json()
    assert page['total'] == 3 and [item['id'] for item in page['items']] == [refused_id]
    assert set(page['items'][0]) == {'id', 'question', 'status', 'created_at', 'model', 'elapsed_ms'}
    assert w.client.get('/api/question-history?page=1&size=1').json()['items'][0]['id'] == second_id
    assert w.client.get('/api/question-history?page=2&size=1').json()['items'][0]['id'] == first_id
    assert w.client.get('/api/question-history?page=3&size=1').json() == {'total': 3, 'items': []}

    # Existing records remain usable without a model or retrieval service.
    model_calls = len(w.chat.calls)
    w.storage.b.close()
    assert w.client.post('/api/admin/ready').status_code == 503
    before_delete = source_truth(w.storage.a)
    assert w.client.get('/api/question-history').status_code == 200
    assert w.client.get(f'/api/question-history/{first_id}').json() == first_snapshot
    removed_history = w.client.delete(f'/api/question-history/{second_id}')
    assert removed_history.status_code == 204 and removed_history.content == b''
    assert source_truth(w.storage.a) == before_delete and len(w.chat.calls) == model_calls
    assert w.client.get('/api/question-history').json()['total'] == 2
    assert w.client.get(f'/api/question-history/{second_id}').status_code == 404
    assert w.client.delete(f'/api/question-history/{second_id}').status_code == 404


def test_unavailable_retrieval_keeps_intake_pending_and_models_usable(workspace):
    w = workspace
    w.storage.b.close()
    response = w.client.get('/health')
    assert response.status_code == 200 and response.json()['db']['status'] == 'UP'
    assert response.json()['retrieval']['status'] == 'DOWN'
    assert w.client.get('/api/runtime').json()['rag_available'] is True
    configure(w, 'still-configurable')
    uploaded = w.client.post('/api/documents', files={'file': ('pending.txt', b'pending content')})
    assert uploaded.status_code == 201
    assert w.futures[-1].result(timeout=5).outcome == 'BUSY'
    document_id = uploaded.json()['id']
    assert w.client.get(f'/api/documents/{document_id}').json()['index_status'] == 'PENDING'
    assert w.client.post('/api/admin/ready').status_code == 503
    assert w.client.post('/api/questions', json={'question': 'anything?'}).status_code == 503
    assert w.chat.calls == []


def test_real_maintenance_process_obeys_lock_and_rebuilds_the_selected_database(services, tmp_path):
    seed_documents(services)
    before = source_truth(services.a)
    services.a.close()
    services.b.close()
    db, index = services.sandbox.settings(), services.settings
    lock_path = tmp_path / 'maintenance.lock'
    environment = {
        **os.environ, 'MYSQL_HOST': db.mysql_host, 'MYSQL_PORT': str(db.mysql_port),
        'MYSQL_DATABASE': db.mysql_database, 'MYSQL_USER': db.mysql_user,
        'MYSQL_PASSWORD': db.mysql_password.get_secret_value(),
        'CHROMA_DIR': str(index.chroma_dir), 'CHUNK_TOKENIZER_PATH': str(index.chunk_tokenizer_path),
        'EMBEDDING_PROVIDER': 'ollama', 'EMBEDDING_MODEL': index.embedding_model,
        'EMBEDDING_DIM': str(index.embedding_dim), 'EMBEDDING_BASE_URL': index.embedding_base_url,
        'EMBEDDING_API_KEY': '', 'EMBEDDING_TIMEOUT_SECONDS': '3',
        'CHUNK_MAX_TOKENS': str(index.chunk_max_tokens), 'CHUNK_MIN_TOKENS': str(index.chunk_min_tokens),
        'RUNTIME_LOCK_FILE': str(lock_path),
    }

    def run(command):
        return subprocess.run([sys.executable, '-m', 'app.maintenance', command], env=environment,
                              capture_output=True, text=True, timeout=30)

    with ProcessLock(lock_path):
        blocked = run('rebuild-index')
        assert blocked.returncode == 1
        assert json.loads(blocked.stderr)['cause'] == 'RuntimeLockUnavailable'
    initialized = run('init-db')
    assert initialized.returncode == 0
    assert json.loads(initialized.stdout)['status'] == 'OK'
    rebuilt = run('rebuild-index')
    assert rebuilt.returncode == 0, rebuilt.stderr
    assert json.loads(rebuilt.stdout)['documents'] == 3
    a, b = Knowledge(db), Retrieval(index)
    try:
        assert source_truth(a) == before
        assert all(snapshot.document.index_status == 'INDEXED' for snapshot in a.snapshots())
        verify_consistency(a.snapshots(), b.inspect())
    finally:
        b.close()
        a.close()


def test_partial_answer_history_roundtrips_without_indexing_generated_text(workspace):
    w = workspace
    assert w.client.post('/api/admin/ready').status_code == 200
    configure(w, 'partial-model')
    w.client.post('/api/documents', files={'file': ('partial.txt', '报名口令是白鹭3718。'.encode('utf-8'))})
    indexed(w)
    w.chat.verdict = 'PARTIAL'
    before = source_truth(w.storage.a), w.storage.b.inspect()
    response = w.client.post('/api/questions', json={'question': '报名口令和未收录的日程？'})
    assert response.status_code == 200 and response.json()['status'] == 'PARTIAL'
    detail = w.client.get('/api/question-history/' + str(response.json()['history_id'])).json()
    assert detail['status'] == 'PARTIAL' and detail['trace'][-1]['decision'] == 'PARTIAL'
    assert detail['sources'] == response.json()['sources']
    assert (source_truth(w.storage.a), w.storage.b.inspect()) == before


@pytest.mark.parametrize('path', [
    '/api/question-history?page=-1', '/api/question-history?size=0',
    '/api/question-history?size=101', '/api/question-history?page=oops',
    '/api/question-history?page=2147483648', '/api/question-history/invalid',
    '/api/question-history/9223372036854775808',
])
def test_history_rejects_invalid_pagination_and_ids(workspace, path):
    response = workspace.client.get(path)
    assert response.status_code == 400 and set(response.json()) == {'error'}
    assert workspace.chat.calls == []


def test_empty_history_and_missing_record_are_explicit(workspace):
    assert workspace.client.get('/api/question-history').json() == {'total': 0, 'items': []}
    assert workspace.client.get('/api/question-history/404').status_code == 404
    assert workspace.client.delete('/api/question-history/404').status_code == 404
