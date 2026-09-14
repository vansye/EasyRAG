"""Document changes coordinate A/B; each module keeps its own transactions and data."""

import logging
from dataclasses import dataclass

from app.modules.knowledge.public import DocumentNotFound, Knowledge, content_hash, validate_content
from app.modules.retrieval.public import Retrieval

from .errors import GateBusy, MutationFailed
from .gate import Gate, Operation, State
from .indexing import IndexingQueue, QueueClosed


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChangeResult:
    id: int
    index_status: str
    reindexed: bool


class DocumentChanges:
    def __init__(self, knowledge: Knowledge, retrieval: Retrieval, queue: IndexingQueue, gate: Gate):
        self._knowledge, self._retrieval, self._queue, self._gate = knowledge, retrieval, queue, gate

    def create(self, filename: str, content_bytes: bytes):
        document = self._knowledge.create(filename, content_bytes)
        try:
            self._queue.submit(document.id)
        except QueueClosed:
            logger.info('document_left_pending document_id=%s cause=shutdown', document.id)
        return document

    def _acquire(self):
        admission = self._gate.try_acquire(Operation.MUTATION)
        if admission.lease is None:
            raise GateBusy(admission.state)
        return admission.lease

    def _load(self, document_id, lease):
        try:
            document = self._knowledge.get(document_id)
        except DocumentNotFound:
            lease.confirm_completion()
            raise
        if document.index_status == 'INDEXING':
            raise GateBusy(State.RECOVERY_REQUIRED)
        return document

    def update(self, document_id: int, content: str) -> ChangeResult:
        validate_content(content)
        return self._schedule(document_id, content)

    def reindex(self, document_id: int) -> ChangeResult:
        return self._schedule(document_id, None)

    def _schedule(self, document_id, content):
        lease = self._acquire()
        handed_off, stage = False, 'LOAD_DOCUMENT'
        try:
            document = self._load(document_id, lease)
            if content is not None and content_hash(content) == document.content_hash:
                stage = 'TOUCH_DOCUMENT'
                result = self._knowledge.update(document_id, content)
                lease.confirm_completion()
                return ChangeResult(document_id, result.document.index_status, False)
            stage = 'DELETE_INDEX'
            self._retrieval.delete_document(document_id)
            stage = 'SAVE_DOCUMENT'
            if content is None:
                self._knowledge.prepare_reindex(document_id)
            else:
                self._knowledge.update(document_id, content)
            stage = 'SUBMIT_INDEXING'
            self._queue.submit(document_id, lease)
            handed_off = True
            return ChangeResult(document_id, 'PENDING', True)
        except (DocumentNotFound, GateBusy):
            raise
        except Exception as failure:
            logger.warning('document_change_unconfirmed document_id=%s stage=%s cause=%s',
                           document_id, stage, type(failure).__name__)
            raise MutationFailed() from None
        finally:
            if not handed_off:
                lease.close()

    def delete(self, document_id: int):
        lease, stage = self._acquire(), 'LOAD_DOCUMENT'
        with lease:
            try:
                self._load(document_id, lease)
                stage = 'DELETE_INDEX'
                self._retrieval.delete_document(document_id)
                stage = 'DELETE_DOCUMENT'
                self._knowledge.delete(document_id)
                lease.confirm_completion()
            except (DocumentNotFound, GateBusy):
                raise
            except Exception as failure:
                logger.warning('document_delete_unconfirmed document_id=%s stage=%s cause=%s',
                               document_id, stage, type(failure).__name__)
                raise MutationFailed() from None
