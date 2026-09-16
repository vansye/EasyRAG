"""The Vue contracts stay stable across the Java-to-FastAPI cutover."""

import asyncio
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.application.gate import Operation, State
from app.application.runtime import RuntimeSettings, Services
from app.http import create_app
from app.modules.answer_models.public import Models
from app.modules.knowledge.public import (
    DatabaseUnavailable, Document, DocumentNotFound, DocumentPage, DocumentSummary,
    Knowledge, Source, StoredChunk, UpdateResult, prepare_upload,
)
from app.modules.retrieval.public import Retrieval, SearchHit


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('LLM_MODEL', '')
    monkeypatch.setenv('LLM_API_KEY', '')
    a, b = Mock(spec=Knowledge), Mock(spec=Retrieval)
    now = datetime(2026, 9, 15, 12, 30)
    document = Document(7, 'UPLOAD', 'note.md', 'Note', '# Note\n事实', 'private-hash',
                        ('tag',), 'INDEXED', None, 1, now, now)
    a.get.return_value = document
    a.list.return_value = DocumentPage(1, (
        DocumentSummary(7, 'Note', 'UPLOAD', ('tag',), 'INDEXED', 1, now),
    ))
    a.chunks.return_value = (StoredChunk(11, 7, 0, '事实', 7, 13, 'Note', 2),)
    a.health.return_value = {'status': 'UP'}
    a.snapshots.return_value = ()
    a.pending_ids.return_value = ()
    a.save_history = Mock(return_value=SimpleNamespace(id=41, created_at=now))
    a.update.return_value = UpdateResult(replace(document, index_status='PENDING'), True)

    def create(filename, data):
        prepared = prepare_upload(filename, data)
        return replace(document, title=prepared.title, index_status='PENDING')

    a.create.side_effect = create
    b.health.return_value = {
        'status': 'UP', 'chroma': {'status': 'UP'}, 'tokenizer': {'status': 'UP'},
        'embedding': {'status': 'UP', 'provider': 'ollama', 'model': 'bge-m3', 'dim': 1024},
    }
    b.runtime_info.return_value = {
        'status': 'UP', 'embedding': {'provider': 'ollama', 'model': 'bge-m3', 'dim': 1024},
        'chroma': {'status': 'UP'},
    }
    b.inspect.return_value = ()
    models = Models(tmp_path / 'llm.json')
    services = Services(a, b, models)

    def submit(document_id, lease=None):
        if lease is not None:
            lease.confirm_completion()

    services.queue.submit = Mock(side_effect=submit)
    settings = RuntimeSettings(_env_file=None, runtime_lock_file=tmp_path / 'backend.lock')
    app = create_app(services, settings=settings)
    with TestClient(app) as client:
        yield SimpleNamespace(a=a, b=b, models=models, services=services, client=client, app=app)


def test_health_is_process_up_even_when_dependencies_are_down(workspace):
    w = workspace
    w.a.health.return_value = {'status': 'DOWN', 'error': 'INVALID_CONFIGURATION'}
    w.b.health.return_value['status'] = 'DOWN'
    response = w.client.get('/health')
    assert response.status_code == 200
    assert response.json()['status'] == 'UP'
    assert response.json()['service'] == 'easyrag-server'
    assert response.json()['db'] == {
        'database': 'mysql', 'status': 'DOWN', 'error': 'INVALID_CONFIGURATION',
    }
    assert response.json()['retrieval']['status'] == 'DOWN'


def test_runtime_is_read_only_and_does_not_probe_models_or_tokenizers(workspace, monkeypatch):
    w = workspace
    w.models.save({'provider': 'openai', 'model': 'example', 'api_key': 'never-expose-key',
                   'base_url': 'http://127.0.0.1:18080/v1'})
    session = Mock(side_effect=AssertionError('must not call model'))
    monkeypatch.setattr(w.models, 'open_session', session)
    result = w.client.get('/api/runtime')
    assert result.status_code == 200
    assert result.json() == {
        'state': 'RECOVERY_REQUIRED', 'rag_available': True,
        'llm': {'configured': True, 'provider': 'openai', 'model': 'example'},
        'embedding': {'provider': 'ollama', 'model': 'bge-m3', 'dim': 1024},
    }
    w.b.health.assert_not_called()
    session.assert_not_called()
    assert 'never-expose-key' not in result.text and '18080' not in result.text
    assert w.services.gate.state == State.RECOVERY_REQUIRED


def test_retrieval_failure_keeps_service_availability_and_model_settings_accessible(workspace):
    w = workspace
    w.b.runtime_info.return_value['status'] = 'DOWN'
    w.b.health.return_value['status'] = 'DOWN'
    response = w.client.get('/api/runtime')
    assert response.status_code == 200 and response.json()['rag_available'] is True
    assert w.client.get('/api/model-config').status_code == 200
    assert w.client.post('/api/admin/ready').status_code == 503
    assert w.services.gate.state == State.RECOVERY_REQUIRED
    # Canonical redirects must carry the same no-cache policy as the config response.
    redirect = w.client.get('/api/model-config/', follow_redirects=False)
    assert redirect.status_code == 307 and redirect.headers['cache-control'] == 'no-store'


def test_documents_keep_public_shapes_and_utf8_byte_offsets(workspace):
    w = workspace
    response = w.client.get('/api/documents')
    assert response.status_code == 200
    assert response.json()['total'] == 1
    w.a.list.assert_called_once_with(page=0, size=20, status=None, q=None)
    assert set(response.json()['items'][0]) == {
        'id', 'title', 'source_type', 'tags', 'index_status', 'chunk_count', 'updated_at',
    }
    detail = w.client.get('/api/documents/7').json()
    assert set(detail) == {
        'id', 'title', 'content', 'tags', 'source_type', 'source_uri', 'index_status',
        'index_error', 'chunk_count', 'created_at', 'updated_at',
    }
    assert 'private-hash' not in str(detail)
    chunks = w.client.get('/api/documents/7/chunks').json()
    assert chunks == {'items': [{'id': 11, 'seq': 0, 'text': '事实', 'byte_start': 7,
                                'byte_end': 13, 'heading_path': 'Note', 'token_count': 2}]}


def test_upload_is_201_pending_and_validates_actual_size(workspace):
    w = workspace
    response = w.client.post('/api/documents', files={'file': ('note.md', b'# Note\nbody')})
    assert response.status_code == 201
    assert response.json() == {'id': 7, 'title': 'Note', 'source_type': 'UPLOAD', 'index_status': 'PENDING'}
    w.services.queue.submit.assert_called_once_with(7)
    oversize = b'x' * (1024 * 1024 + 1)
    response = w.client.post('/api/documents', files={'file': ('big.md', oversize)})
    assert response.status_code == 400
    assert '1,048,577' in response.json()['error']


def test_upload_hard_limit_rejects_before_calling_intake(workspace):
    w = workspace
    response = w.client.post('/api/documents', files={'file': ('big.md', b'x' * (4 * 1024 * 1024))})
    assert response.status_code == 400
    w.a.create.assert_not_called()


def test_chunked_upload_hard_limit_is_counted_without_content_length(workspace):
    w = workspace

    async def exercise():
        async def body():
            yield b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="big.md"\r\n\r\n'
            for _ in range(65):
                yield b'x' * 65536
            yield b'\r\n--boundary--\r\n'

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=w.app), base_url='http://test') as client:
            return await client.post('/api/documents', content=body(),
                                     headers={'content-type': 'multipart/form-data; boundary=boundary'})

    response = asyncio.run(exercise())
    assert response.status_code == 400
    assert set(response.json()) == {'error'}
    w.a.create.assert_not_called()


def test_ready_and_mutation_status_codes(workspace):
    w = workspace
    response = w.client.put('/api/documents/7', json={'content': 'new'})
    assert response.status_code == 503 and response.json()['state'] == 'RECOVERY_REQUIRED'
    assert w.client.post('/api/admin/ready').json() == {'state': 'READY', 'recovered': 0}
    with w.services.gate.try_acquire(Operation.QUERY).lease:
        assert w.client.post('/api/documents/7/reindex').status_code == 409
        assert w.client.post('/api/admin/ready').status_code == 409
    response = w.client.put('/api/documents/7', json={'content': 'new'})
    assert response.status_code == 200
    assert response.json() == {'id': 7, 'index_status': 'PENDING', 'reindexed': True}
    assert w.client.post('/api/documents/7/reindex').status_code == 202
    response = w.client.delete('/api/documents/7')
    assert response.status_code == 204 and response.content == b''


@pytest.mark.parametrize('method,path,payload', [
    ('get', '/api/documents?page=oops', None),
    ('get', '/api/documents?page=1.0', None),
    ('get', '/api/documents?page=2147483648', None),
    ('get', '/api/documents/bad/chunks', None),
    ('get', '/api/documents/9223372036854775808', None),
    ('post', '/api/documents', None),
    ('put', '/api/documents/7', {'content': 123}),
    ('put', '/api/documents/7', {'content': None}),
    ('post', '/api/questions', {'question': 123}),
    ('post', '/api/questions', {'question': ' '}),
    ('post', '/api/questions', {'question': 'x' * 2001}),
], ids=['page', 'decimal-page', 'page-overflow', 'id', 'id-overflow', 'file', 'content-number',
        'content-null', 'question-number', 'blank', 'long'])
def test_request_errors_are_400_with_sanitized_error(workspace, method, path, payload):
    response = workspace.client.request(method, path, json=payload)
    assert response.status_code == 400
    assert set(response.json()) == {'error'}
    assert 'input' not in response.text and '2001' not in response.text


def test_empty_pagination_parameters_keep_legacy_defaults(workspace):
    response = workspace.client.get('/api/documents?page=&size=')
    assert response.status_code == 200
    workspace.a.list.assert_called_once_with(page=0, size=20, status=None, q=None)


def test_missing_document_and_unavailable_database_are_explicit(workspace):
    workspace.a.get.side_effect = DocumentNotFound()
    response = workspace.client.get('/api/documents/99')
    assert response.status_code == 404 and set(response.json()) == {'error'}
    workspace.a.list.side_effect = DatabaseUnavailable('mysql://private-credentials')
    response = workspace.client.get('/api/documents')
    assert response.status_code == 503 and 'private-credentials' not in response.text


def test_question_uses_trace_rank_sources_and_keeps_technical_errors_distinct(workspace, monkeypatch):
    w = workspace
    assert w.client.post('/api/questions', json={'question': 'What?'}).status_code == 503
    assert w.client.post('/api/admin/ready').status_code == 200
    w.b.search.return_value = (SearchHit(30, 3, 'First', '', .9), SearchHit(11, 7, 'Second', '', .8))
    w.a.sources.return_value = (Source(11, 7, 'Second', 'Second', 0, 6, ''),
                                Source(30, 3, 'First', 'First', 0, 5, ''))
    session = Mock()
    session.model_info = {'provider': 'openai', 'model': 'question-model'}
    session.complete.side_effect = ['{"verdict":"SUFFICIENT"}', 'First [1], second [2].']
    monkeypatch.setattr(w.models, 'open_session', Mock(return_value=session))
    response = w.client.post('/api/questions', json={'question': 'What?'})
    assert response.status_code == 200
    assert response.json()['status'] == 'ANSWERED'
    assert [source['chunk_id'] for source in response.json()['sources']] == [30, 11]
    assert response.json()['trace'][-1]['retrieved'][0]['rank'] == 1
    assert response.json()['history_id'] == 41
    assert response.json()['created_at'] == '2026-09-15T12:30:00'
    assert response.json()['model'] == {'provider': 'openai', 'model': 'question-model'}
    assert isinstance(response.json()['elapsed_ms'], int)
    w.a.save_history.assert_called_once()
    w.models.open_session.assert_called_once_with()
    session.complete.side_effect = RuntimeError('upstream-secret')
    response = w.client.post('/api/questions', json={'question': 'What?'})
    assert response.status_code == 502 and 'upstream-secret' not in response.text


def test_model_configuration_preserves_fields_errors_and_no_store(workspace):
    w = workspace
    assert w.client.get('/api/model-config').json()['configured'] is False
    update = {'provider': 'openai', 'model': 'local-model', 'base_url': 'http://127.0.0.1:18080/v1',
              'api_key': 'private-model-key'}
    response = w.client.put('/api/model-config', json=update)
    assert response.status_code == 200
    assert set(response.json()) == {'configured', 'provider', 'model', 'base_url', 'api_key_configured', 'source'}
    assert response.headers['cache-control'] == 'no-store'
    assert 'private-model-key' not in response.text
    response = w.client.put('/api/model-config', json={**update, 'model': 'new-model', 'api_key': ''})
    assert response.status_code == 200
    response = w.client.put('/api/model-config', json={**update, 'base_url': 'https://example.com/v1', 'api_key': ''})
    assert response.status_code == 400 and 'API Key' in response.json()['error']
    assert response.headers['cache-control'] == 'no-store'
    response = w.client.put('/api/model-config', content='{"api_key":"private-model-key",',
                            headers={'content-type': 'application/json'})
    assert response.status_code == 400 and 'private-model-key' not in response.text
    assert response.headers['cache-control'] == 'no-store'
    response = w.client.delete('/api/model-config')
    assert response.status_code == 200 and response.json()['source'] == 'environment'


def test_unexpected_configuration_failure_is_sanitized_and_never_cached(workspace, monkeypatch):
    monkeypatch.setattr(workspace.models, 'get', Mock(side_effect=RuntimeError('private-model-key')))
    client = TestClient(workspace.app, raise_server_exceptions=False)
    try:
        response = client.get('/api/model-config')
    finally:
        client.close()
    assert response.status_code == 500 and set(response.json()) == {'error'}
    assert 'private-model-key' not in response.text
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('path', ['/chunk', '/embed', '/query', '/reset', '/index/reset', '/runtime', '/model-config'])
def test_internal_legacy_endpoints_are_not_exposed(workspace, path):
    assert workspace.client.post(path).status_code == 404


@pytest.mark.parametrize('page,size', [(-1,20),(0,0),(0,101)])
def test_history_invalid_pagination_is_rejected_before_database_access(workspace,page,size):
    w = workspace
    w.a._transaction.side_effect = DatabaseUnavailable('database unavailable')
    w.a.list_history.side_effect = lambda **params: Knowledge.list_history(w.a, **params)
    response = w.client.get('/api/question-history', params={'page': page, 'size': size})
    assert response.status_code == 400 and set(response.json()) == {'error'}
    w.a._transaction.assert_not_called()
