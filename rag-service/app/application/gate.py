"""Short locked transitions, with workflow leases that may cross threads."""

from dataclasses import dataclass
from enum import StrEnum
from threading import Condition, Lock


class Operation(StrEnum):
    QUERY = 'QUERY'
    MUTATION = 'MUTATION'
    RECOVERY = 'RECOVERY'


class State(StrEnum):
    RECOVERY_REQUIRED = 'RECOVERY_REQUIRED'
    READY = 'READY'
    QUERYING = 'QUERYING'
    MUTATING = 'MUTATING'
    RECOVERING = 'RECOVERING'


@dataclass(frozen=True)
class Admission:
    state: State
    lease: 'Lease | None'


class Gate:
    def __init__(self):
        self._lock = Lock()
        self._changed = Condition(self._lock)
        self._queries: set[Lease] = set()
        self._exclusive: Lease | None = None
        self._recovery_required = True
        self._generation = 0
        self._stopping = False

    def _state(self):
        if self._exclusive is not None:
            return State.RECOVERING if self._exclusive.operation == Operation.RECOVERY else State.MUTATING
        if self._recovery_required:
            return State.RECOVERY_REQUIRED
        return State.QUERYING if self._queries else State.READY

    @property
    def state(self) -> State:
        with self._lock:
            return self._state()

    def try_acquire(self, operation: Operation) -> Admission:
        operation = Operation(operation)
        with self._lock:
            state = self._state()
            allowed = self._exclusive is None and not self._stopping
            if operation != Operation.QUERY:
                allowed = allowed and not self._queries
            if operation != Operation.RECOVERY:
                allowed = allowed and not self._recovery_required
            if not allowed:
                return Admission(state, None)
            lease = Lease(self, operation, self._generation)
            if operation == Operation.QUERY:
                self._queries.add(lease)
            else:
                self._exclusive = lease
            return Admission(self._state(), lease)

    def owns(self, lease: 'Lease', operation: Operation) -> bool:
        with self._lock:
            return lease.operation == operation and (
                lease in self._queries if operation == Operation.QUERY else self._exclusive is lease)

    def require_recovery(self):
        with self._lock:
            self._recovery_required = True
            self._generation += 1

    def stop(self):
        with self._lock:
            self._stopping = True
            self._recovery_required = True
            self._generation += 1

    def wait_idle(self):
        with self._changed:
            self._changed.wait_for(lambda: not self._queries and self._exclusive is None)

    def _finish(self, lease: 'Lease', confirmed: bool) -> bool:
        with self._changed:
            if lease.operation == Operation.QUERY:
                if lease not in self._queries:
                    return False
                self._queries.remove(lease)
            else:
                if self._exclusive is not lease:
                    return False
                self._exclusive = None
                self._recovery_required = not confirmed or lease._generation != self._generation
            self._changed.notify_all()
            return True


class Lease:
    def __init__(self, owner: Gate, operation: Operation, generation: int):
        self._owner = owner
        self.operation = operation
        self._generation = generation

    def confirm_completion(self) -> bool:
        return self._owner._finish(self, True)

    def close(self):
        self._owner._finish(self, False)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
