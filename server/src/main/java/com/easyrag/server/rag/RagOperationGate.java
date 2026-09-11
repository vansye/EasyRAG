package com.easyrag.server.rag;

import org.springframework.stereotype.Component;

import java.util.HashSet;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;

@Component
public final class RagOperationGate {

    private final Set<Lease> queries = new HashSet<>();
    private State state = State.RECOVERY_REQUIRED;
    private Lease exclusiveLease;

    public synchronized State state() {
        return state;
    }

    public synchronized Admission tryAcquire(Operation operation) {
        Objects.requireNonNull(operation, "operation");
        boolean allowed = switch (operation) {
            case QUERY -> state == State.READY || state == State.QUERYING;
            case MUTATION -> state == State.READY;
            case RECOVERY -> state == State.READY || state == State.RECOVERY_REQUIRED;
        };
        if (!allowed) {
            return new Admission(state, Optional.empty());
        }
        Lease lease = new Lease(this, operation);
        if (operation == Operation.QUERY) {
            queries.add(lease);
        } else {
            exclusiveLease = lease;
        }
        state = switch (operation) {
            case QUERY -> State.QUERYING;
            case MUTATION -> State.MUTATING;
            case RECOVERY -> State.RECOVERING;
        };
        return new Admission(state, Optional.of(lease));
    }

    private synchronized boolean finish(Lease lease, boolean confirmed) {
        if (lease.operation == Operation.QUERY) {
            if (!queries.remove(lease)) {
                return false;
            }
            if (queries.isEmpty()) {
                state = State.READY;
            }
            return true;
        }
        if (exclusiveLease != lease) {
            return false;
        }
        exclusiveLease = null;
        state = confirmed ? State.READY : State.RECOVERY_REQUIRED;
        return true;
    }

    public enum Operation {
        QUERY, MUTATION, RECOVERY
    }

    public enum State {
        RECOVERY_REQUIRED, READY, QUERYING, MUTATING, RECOVERING
    }

    public record Admission(State state, Optional<Lease> lease) {}

    public static final class Lease implements AutoCloseable {

        private final RagOperationGate owner;
        private final Operation operation;

        private Lease(RagOperationGate owner, Operation operation) {
            this.owner = owner;
            this.operation = operation;
        }

        public boolean confirmCompletion() {
            return owner.finish(this, true);
        }

        @Override
        public void close() {
            owner.finish(this, false);
        }
    }
}
