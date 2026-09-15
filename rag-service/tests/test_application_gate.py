"""Workflow permits protect work, including request-to-worker thread handoff."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.application.gate import Gate, Operation, State


def ready_gate():
    gate = Gate()
    gate.try_acquire(Operation.RECOVERY).lease.confirm_completion()
    return gate


def test_startup_requires_explicit_successful_recovery():
    gate = Gate()
    assert gate.state == State.RECOVERY_REQUIRED
    assert gate.try_acquire(Operation.QUERY).lease is None
    assert gate.try_acquire(Operation.MUTATION).lease is None
    with gate.try_acquire(Operation.RECOVERY).lease:
        assert gate.state == State.RECOVERING
    assert gate.state == State.RECOVERY_REQUIRED
    lease = gate.try_acquire(Operation.RECOVERY).lease
    assert lease.confirm_completion()
    assert gate.state == State.READY


def test_queries_share_access_and_exclude_mutations_until_all_finish():
    gate = ready_gate()
    first = gate.try_acquire(Operation.QUERY).lease
    second = gate.try_acquire(Operation.QUERY).lease
    assert gate.state == State.QUERYING
    assert gate.try_acquire(Operation.MUTATION).lease is None
    assert gate.try_acquire(Operation.RECOVERY).lease is None
    first.close()
    assert gate.state == State.QUERYING
    second.close()
    assert gate.state == State.READY


@pytest.mark.parametrize('operation', [Operation.MUTATION, Operation.RECOVERY])
def test_unconfirmed_exclusive_work_requires_recovery(operation):
    gate = ready_gate()
    with pytest.raises(RuntimeError), gate.try_acquire(operation).lease:
        for other in Operation:
            assert gate.try_acquire(other).lease is None
        raise RuntimeError('workflow failed before terminal confirmation')
    assert gate.state == State.RECOVERY_REQUIRED


def test_query_failure_does_not_close_healthy_gate():
    gate = ready_gate()
    with pytest.raises(RuntimeError), gate.try_acquire(Operation.QUERY).lease:
        raise RuntimeError('model is unavailable')
    assert gate.state == State.READY


def test_completion_is_idempotent_and_old_lease_cannot_release_new_work():
    gate = ready_gate()
    old = gate.try_acquire(Operation.MUTATION).lease
    assert old.confirm_completion()
    current = gate.try_acquire(Operation.MUTATION).lease
    assert not old.confirm_completion()
    old.close()
    assert gate.state == State.MUTATING and gate.owns(current, Operation.MUTATION)
    current.close()
    assert gate.state == State.RECOVERY_REQUIRED


def test_lease_can_be_transferred_without_a_thread_owned_lock():
    gate = ready_gate()
    lease = gate.try_acquire(Operation.MUTATION).lease
    with ThreadPoolExecutor(max_workers=1) as worker:
        assert worker.submit(lease.confirm_completion).result(timeout=2)
    assert gate.state == State.READY


def test_inconsistency_blocks_new_work_until_existing_queries_finish():
    gate = ready_gate()
    first = gate.try_acquire(Operation.QUERY).lease
    second = gate.try_acquire(Operation.QUERY).lease
    gate.require_recovery()
    assert gate.state == State.RECOVERY_REQUIRED
    for operation in Operation:
        assert gate.try_acquire(operation).lease is None
    first.close()
    second.close()
    assert gate.state == State.RECOVERY_REQUIRED
    with gate.try_acquire(Operation.RECOVERY).lease as recovery:
        assert recovery.confirm_completion()
    assert gate.state == State.READY


def test_gate_does_not_accept_a_foreign_or_query_lease_as_a_mutation():
    gate, other = ready_gate(), ready_gate()
    foreign = other.try_acquire(Operation.MUTATION).lease
    query = gate.try_acquire(Operation.QUERY).lease
    assert not gate.owns(foreign, Operation.MUTATION)
    assert not gate.owns(query, Operation.MUTATION)
    query.close()
    foreign.close()


def test_ready_scan_failure_cannot_be_erased_by_a_new_workers_late_confirmation():
    gate = ready_gate()
    # ready() has opened admission; an upload starts while its PENDING scan runs.
    worker = gate.try_acquire(Operation.MUTATION).lease
    gate.require_recovery()  # The scan fails after that worker acquired its lease.
    assert worker.confirm_completion()
    assert gate.state == State.RECOVERY_REQUIRED


def test_shutdown_stops_all_admission_and_waits_for_disconnected_query_work():
    gate = ready_gate()
    query = gate.try_acquire(Operation.QUERY).lease
    gate.stop()
    for operation in Operation:
        assert gate.try_acquire(operation).lease is None
    with ThreadPoolExecutor(max_workers=1) as closer:
        idle = closer.submit(gate.wait_idle)
        try:
            assert not idle.done()
        finally:
            query.close()
        idle.result(timeout=2)
    assert gate.state == State.RECOVERY_REQUIRED


def test_shutdown_allows_an_existing_transferred_mutation_to_finish():
    gate = ready_gate()
    worker = gate.try_acquire(Operation.MUTATION).lease
    gate.stop()
    assert gate.owns(worker, Operation.MUTATION)
    worker.confirm_completion()
    gate.wait_idle()
    assert gate.state == State.RECOVERY_REQUIRED
