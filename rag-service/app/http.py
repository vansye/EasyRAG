"""HTTP conversion only: business rules and resource ownership stay behind public ports."""

from dataclasses import asdict
from functools import wraps
import logging
import re
from typing import Annotated, Any

from fastapi import Body, FastAPI, File, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, BeforeValidator, ConfigDict, StrictStr
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException

from app.modules.answer_models.public import ConfigRejected, ModelUnavailable
from app.modules.knowledge.public import DatabaseUnavailable, DocumentNotFound, HistoryNotFound, InputRejected
from app.modules.qa.public import validate_question

from .application.errors import GateBusy, MutationFailed, QuestionFailed, RecoveryFailed
from .application.gate import State
from .application.runtime import RuntimeSettings, Services
from .lifecycle import lifespan_for
from .question_stream import QuestionStreamResponse


_HARD_UPLOAD_BYTES = 4 * 1024 * 1024
_UPLOAD_TOO_LARGE = '文件超过收录上限（1 MB），请拆分后再上传'
_INVALID_MODEL = '模型配置无效，请检查服务类型、接口地址、模型名和 API Key。'
_INVALID_QUESTION = 'question 不能为空且不超过 2000 字'
logger = logging.getLogger(__name__)


def _integer_parser(bits, default=None):
    def parse(value):
        if value == '' and default is not None:
            return default
        if type(value) is int:
            result = value
        else:
            if not isinstance(value, str) or re.fullmatch(r'[+-]?\d+', value.strip()) is None:
                raise ValueError('expected an integer')
            result = int(value)
        if not -(2 ** (bits - 1)) <= result < 2 ** (bits - 1):
            raise ValueError('integer is outside the supported range')
        return result

    return BeforeValidator(parse)


_Page = Annotated[int, _integer_parser(32, 0)]
_PageSize = Annotated[int, _integer_parser(32, 20)]
_DocumentId = Annotated[int, _integer_parser(64)]
_HistoryId = Annotated[int, _integer_parser(64)]


class _ApiBoundary:
    """Count streaming multipart bytes and prevent caching of all config responses."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        if scope['path'].rstrip('/') == '/api/model-config':
            original_send = send

            async def send(message):
                if message['type'] == 'http.response.start':
                    MutableHeaders(scope=message)['cache-control'] = 'no-store'
                await original_send(message)

        if scope['path'].rstrip('/') == '/api/documents' and scope['method'] == 'POST':
            length = Headers(scope=scope).get('content-length')
            try:
                too_large = length is not None and int(length) > _HARD_UPLOAD_BYTES
            except ValueError:
                return await JSONResponse({'error': '请求参数无效'}, status_code=400)(scope, receive, send)
            if too_large:
                return await JSONResponse({'error': _UPLOAD_TOO_LARGE}, status_code=400)(scope, receive, send)
            original_receive, received = receive, 0

            async def receive():
                nonlocal received
                message = await original_receive()
                if message['type'] == 'http.request':
                    received += len(message.get('body', b''))
                    if received > _HARD_UPLOAD_BYTES:
                        # The multipart parser closes its temporary files on this exception.
                        raise MultiPartException(_UPLOAD_TOO_LARGE)
                return message

        await self.app(scope, receive, send)


class _UpdateBody(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    content: StrictStr


class _QuestionBody(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    question: StrictStr


def _services(request: Request) -> Services:
    return request.app.state.services


def _worker(handler):
    @wraps(handler)
    def invoke(request: Request, *args, **kwargs):
        with _services(request).request_work():
            return handler(request, *args, **kwargs)

    return invoke


def create_app(services: Services | None = None, *, settings: RuntimeSettings | None = None) -> FastAPI:
    settings = settings if settings is not None else RuntimeSettings()
    app = FastAPI(title='EasyRAG', lifespan=lifespan_for(services, settings))
    app.add_middleware(_ApiBoundary)

    @app.exception_handler(Exception)
    async def unexpected_failure(request, failure):
        logger.warning('http_failed cause=%s', type(failure).__name__)
        headers = {'cache-control': 'no-store'} if request.url.path.rstrip('/') == '/api/model-config' else None
        return JSONResponse({'error': '服务暂时不可用，请重试'}, status_code=500, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, _failure):
        if request.url.path == '/api/model-config':
            message = _INVALID_MODEL
        elif request.url.path in ('/api/questions', '/api/questions/stream'):
            message = _INVALID_QUESTION
        else:
            message = '请求参数无效，请检查字段类型和必填项'
        return JSONResponse({'error': message}, status_code=400)

    @app.exception_handler(HTTPException)
    async def http_error(_request, failure):
        message = _UPLOAD_TOO_LARGE if failure.detail == _UPLOAD_TOO_LARGE else {
            404: '接口不存在', 405: '请求方式不支持',
        }.get(failure.status_code, '请求参数无效')
        return JSONResponse({'error': message}, status_code=failure.status_code, headers=failure.headers)

    @app.exception_handler(InputRejected)
    async def rejected(_request, failure):
        return JSONResponse({'error': str(failure)}, status_code=400)

    @app.exception_handler(DocumentNotFound)
    async def missing(_request, _failure):
        return JSONResponse({'error': '文档不存在或已删除'}, status_code=404)

    @app.exception_handler(HistoryNotFound)
    async def missing_history(_request, _failure):
        return JSONResponse({'error': '问答历史不存在或已删除'}, status_code=404)

    @app.exception_handler(DatabaseUnavailable)
    async def database_unavailable(_request, _failure):
        return JSONResponse({'error': '资料库暂时不可用'}, status_code=503)

    @app.exception_handler(GateBusy)
    async def busy(request, failure):
        status = 503 if request.url.path == '/api/questions' or failure.state == State.RECOVERY_REQUIRED else 409
        return JSONResponse({'error': str(failure), 'state': failure.state}, status_code=status)

    @app.exception_handler(MutationFailed)
    @app.exception_handler(RecoveryFailed)
    async def recovery_required(_request, failure):
        return JSONResponse({'error': str(failure), 'state': State.RECOVERY_REQUIRED}, status_code=503)

    @app.exception_handler(QuestionFailed)
    async def question_failed(_request, failure):
        return JSONResponse({'error': str(failure)}, status_code=502)

    @app.exception_handler(ConfigRejected)
    async def config_rejected(_request, failure):
        message = ('请填写 API Key；更换服务类型或接口地址后，需要重新填写密钥。'
                   if failure.code == 'API_KEY_REQUIRED' else _INVALID_MODEL)
        return JSONResponse({'error': message}, status_code=400)

    @app.exception_handler(ModelUnavailable)
    async def config_unavailable(_request, _failure):
        return JSONResponse({'error': '模型配置暂时不可用，请重试或恢复启动配置。'}, status_code=503)

    @app.get('/health')
    @_worker
    def health(request: Request):
        active = _services(request)
        return {'status': 'UP', 'service': 'easyrag-server',
                'db': {'database': 'mysql', **active.knowledge.health()},
                'retrieval': active.retrieval.health()}

    @app.get('/api/runtime')
    @_worker
    def runtime(request: Request):
        active = _services(request)
        retrieval = active.retrieval.runtime_info()
        try:
            config = active.models.get()
        except ModelUnavailable:
            config = {'configured': False}
        llm = ({key: config[key] for key in ('configured', 'provider', 'model')} if config['configured']
               else {'configured': False, 'provider': None, 'model': None})
        # Vue uses this legacy field for service reachability, including model settings.
        # Retrieval readiness is reported by the gate and layered /health response.
        return {'state': active.gate.state, 'rag_available': True,
                'llm': llm, 'embedding': retrieval['embedding']}

    @app.post('/api/admin/ready')
    @_worker
    def ready(request: Request):
        return _services(request).readiness.ready()

    @app.post('/api/documents', status_code=201)
    @_worker
    def upload(request: Request, file: UploadFile = File(...)):
        record = _services(request).documents.create(file.filename, file.file.read())
        return {key: getattr(record, key) for key in ('id', 'title', 'source_type', 'index_status')}

    @app.get('/api/documents')
    @_worker
    def documents(request: Request, page: _Page = 0, size: _PageSize = 20, status: str | None = None, q: str | None = None):
        return _services(request).knowledge.list(page=page, size=size, status=status, q=q)

    @app.get('/api/documents/{document_id}')
    @_worker
    def document(request: Request, document_id: _DocumentId):
        record = asdict(_services(request).knowledge.get(document_id))
        for internal in ('content_hash', 'indexed_at', 'deleted_at'):
            record.pop(internal)
        return record

    @app.get('/api/documents/{document_id}/chunks')
    @_worker
    def chunks(request: Request, document_id: _DocumentId):
        return {'items': [{key: value for key, value in asdict(chunk).items() if key != 'document_id'}
                          for chunk in _services(request).knowledge.chunks(document_id)]}

    @app.put('/api/documents/{document_id}')
    @_worker
    def update(request: Request, document_id: _DocumentId, body: _UpdateBody):
        return _services(request).documents.update(document_id, body.content)

    @app.post('/api/documents/{document_id}/reindex', status_code=202)
    @_worker
    def reindex(request: Request, document_id: _DocumentId):
        return _services(request).documents.reindex(document_id)

    @app.delete('/api/documents/{document_id}', status_code=204)
    @_worker
    def delete(request: Request, document_id: _DocumentId):
        _services(request).documents.delete(document_id)
        return Response(status_code=204)

    @app.post('/api/questions/stream')
    async def ask_stream(request: Request, body: _QuestionBody):
        try:
            validate_question(body.question)
        except ValueError:
            raise InputRejected(_INVALID_QUESTION) from None
        return QuestionStreamResponse(_services(request), body.question)

    @app.post('/api/questions')
    @_worker
    def ask(request: Request, body: _QuestionBody):
        try:
            return _services(request).questions.ask(body.question)
        except ValueError:
            raise InputRejected(_INVALID_QUESTION) from None

    @app.get('/api/question-history')
    @_worker
    def question_history(request: Request, page: _Page = 0, size: _PageSize = 20):
        return _services(request).knowledge.list_history(page=page, size=size)

    @app.get('/api/question-history/{history_id}')
    @_worker
    def history_detail(request: Request, history_id: _HistoryId):
        return _services(request).knowledge.get_history(history_id)

    @app.delete('/api/question-history/{history_id}', status_code=204)
    @_worker
    def delete_history(request: Request, history_id: _HistoryId):
        _services(request).knowledge.delete_history(history_id)
        return Response(status_code=204)

    @app.get('/api/model-config')
    @_worker
    def model_config(request: Request):
        return _services(request).models.get()

    @app.put('/api/model-config')
    @_worker
    def save_model_config(request: Request, body: Any = Body(...)):
        return _services(request).models.save(body)

    @app.delete('/api/model-config')
    @_worker
    def reset_model_config(request: Request):
        return _services(request).models.reset()

    return app
