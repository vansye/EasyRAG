"""Mutation permission must span removal of old vectors and asynchronous replacement."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.application.documents import DocumentChanges
from app.application.errors import GateBusy, MutationFailed
from app.application.gate import Gate, Operation, State
from app.application.indexing import IndexingQueue, QueueClosed
from app.modules.knowledge import public as knowledge
from app.modules.retrieval.public import Retrieval


@pytest.fixture
def workflow():
    gate = Gate()
    gate.try_acquire(Operation.RECOVERY).lease.confirm_completion()
    a, b, queue = Mock(spec=knowledge.Knowledge), Mock(spec=Retrieval), Mock(spec=IndexingQueue)
    record = SimpleNamespace(id=1, content='original\ntext', content_hash=knowledge.content_hash('original\ntext'), index_status='INDEXED')
    a.get.return_value = record
    pending = SimpleNamespace(id=1, index_status='PENDING')
    a.update.return_value = knowledge.UpdateResult(pending, True)
    a.prepare_reindex.return_value = pending
    calls = Mock()
    for name, target in [('get',a.get),('delete_index',b.delete_document),('update',a.update),
                         ('reindex',a.prepare_reindex),('delete_document',a.delete),('submit',queue.submit)]:
        calls.attach_mock(target, name)
    return SimpleNamespace(gate=gate,a=a,b=b,queue=queue,record=record,calls=calls,
                           changes=DocumentChanges(a,b,queue,gate))


@pytest.mark.parametrize('operation', ['update','reindex'])
def test_changed_content_holds_one_lease_until_worker_finishes(workflow, operation):
    w = workflow
    def submit(document_id, lease):
        assert w.gate.state == State.MUTATING and w.gate.owns(lease, Operation.MUTATION)
    w.queue.submit.side_effect = submit
    result = getattr(w.changes, operation)(1, *(['new content'] if operation == 'update' else []))
    assert result.id == 1 and result.index_status == 'PENDING' and result.reindexed
    assert [call[0] for call in w.calls.mock_calls] == ['get','delete_index',operation,'submit']
    assert w.gate.state == State.MUTATING
    w.queue.submit.call_args.args[1].confirm_completion()
    assert w.gate.state == State.READY


def test_same_hash_touches_only_document_and_retains_existing_state(workflow):
    w = workflow
    w.a.update.return_value = knowledge.UpdateResult(w.record, False)
    result = w.changes.update(1, '\t' + w.record.content.replace('\n','\r\n') + '\n')
    assert not result.reindexed and result.index_status == 'INDEXED'
    assert [call[0] for call in w.calls.mock_calls] == ['get','update']
    assert w.gate.state == State.READY


def test_delete_removes_vectors_before_database_and_then_confirms(workflow):
    w = workflow
    assert w.changes.delete(1) is None
    assert [call[0] for call in w.calls.mock_calls] == ['get','delete_index','delete_document']
    assert w.gate.state == State.READY


@pytest.mark.parametrize('operation', ['update','reindex','delete'])
def test_busy_mutations_do_not_touch_modules(workflow, operation):
    w = workflow
    with w.gate.try_acquire(Operation.QUERY).lease:
        with pytest.raises(GateBusy) as failure:
            getattr(w.changes,operation)(1, *(['new'] if operation == 'update' else []))
        assert failure.value.state == State.QUERYING
    assert not w.calls.mock_calls


def test_invalid_update_is_rejected_before_acquiring_or_removing_index(workflow):
    w = workflow
    w.gate.require_recovery()
    with pytest.raises(knowledge.InputRejected):
        w.changes.update(1, ' \u3000')
    assert not w.calls.mock_calls


def test_not_found_closes_cleanly_but_orphan_indexing_requires_recovery(workflow):
    w = workflow
    w.a.get.side_effect = knowledge.DocumentNotFound()
    with pytest.raises(knowledge.DocumentNotFound):
        w.changes.delete(1)
    assert w.gate.state == State.READY
    w.a.get.side_effect = None
    w.record.index_status = 'INDEXING'
    with pytest.raises(GateBusy) as failure:
        w.changes.delete(1)
    assert failure.value.state == State.RECOVERY_REQUIRED and w.gate.state == State.RECOVERY_REQUIRED
    w.b.delete_document.assert_not_called()


@pytest.mark.parametrize('stage', ['get','delete_index','update','submit'])
def test_unconfirmed_mutation_never_releases_gate_for_queries(workflow,stage):
    w = workflow
    getattr(w.calls,stage).side_effect = RuntimeError('secret payload')
    with pytest.raises(MutationFailed) as failure:
        w.changes.update(1,'new')
    assert 'secret' not in str(failure.value)
    assert w.gate.state == State.RECOVERY_REQUIRED
    assert w.gate.try_acquire(Operation.QUERY).lease is None


def test_upload_returns_committed_pending_even_if_executor_stops(workflow):
    w = workflow
    committed = SimpleNamespace(id=1,index_status='PENDING')
    w.a.create.return_value = committed
    w.queue.submit.side_effect = QueueClosed('shutdown')
    assert w.changes.create('note.md',b'hello') is committed
    w.a.create.assert_called_once_with('note.md',b'hello')
    w.queue.submit.assert_called_once_with(1)
