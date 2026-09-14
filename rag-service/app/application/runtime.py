"""Composition root; resources close only after all admitted work finishes."""

from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Condition

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.modules.answer_models.public import Models
from app.modules.knowledge.public import Knowledge
from app.modules.retrieval.public import Retrieval

from .documents import DocumentChanges
from .errors import GateBusy
from .gate import Gate
from .indexing import Indexer, IndexingQueue
from .questions import Questions
from .recovery import Readiness


SERVICE_DIR = Path(__file__).resolve().parents[2]


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env',env_file_encoding='utf-8',extra='ignore',hide_input_in_errors=True)
    backend_host: str = '127.0.0.1'
    backend_port: int = Field(default=8080,gt=0,le=65535)
    runtime_lock_file: Path = SERVICE_DIR / 'data/backend.lock'

    @field_validator('runtime_lock_file')
    @classmethod
    def _service_relative(cls, path):
        return path if path.is_absolute() else SERVICE_DIR / path


class Services:
    def __init__(self, knowledge: Knowledge, retrieval: Retrieval, models: Models):
        self.knowledge, self.retrieval, self.models = knowledge,retrieval,models
        self._requests = Condition()
        self._active_requests = 0
        self._accepting_requests = True
        self.gate = Gate()
        self.queue = IndexingQueue(Indexer(knowledge,retrieval,self.gate))
        self.documents = DocumentChanges(knowledge,retrieval,self.queue,self.gate)
        self.questions = Questions(knowledge,retrieval,models,self.gate)
        self.readiness = Readiness(knowledge,retrieval,self.queue,self.gate)

    @classmethod
    def create(cls):
        with ExitStack() as cleanup:
            knowledge = Knowledge()
            cleanup.callback(knowledge.close)
            retrieval = Retrieval()
            cleanup.callback(retrieval.close)
            services = cls(knowledge,retrieval,Models())
            cleanup.pop_all()
            return services

    @contextmanager
    def request_work(self):
        """Track inside the sync worker, including reads and uploads without a gate lease."""
        with self._requests:
            if not self._accepting_requests:
                raise GateBusy(self.gate.state)
            self._active_requests += 1
        try:
            yield
        finally:
            with self._requests:
                self._active_requests -= 1
                self._requests.notify_all()

    def close(self):
        with self._requests:
            self._accepting_requests = False
        self.gate.stop()
        with self._requests:
            self._requests.wait_for(lambda: self._active_requests == 0)
        self.queue.close()
        self.gate.wait_idle()
        try:
            self.retrieval.close()
        finally:
            self.knowledge.close()
