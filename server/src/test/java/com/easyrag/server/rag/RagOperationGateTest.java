package com.easyrag.server.rag;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.EnumSource;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.stream.Stream;

import static com.easyrag.server.rag.RagOperationGate.Operation;
import static com.easyrag.server.rag.RagOperationGate.State;
import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class RagOperationGateTest {

    @Test
    void startsWithRecoveryRequired() {
        assertThat(new RagOperationGate().state()).isEqualTo(State.RECOVERY_REQUIRED);
    }

    @ParameterizedTest
    @EnumSource(value = Operation.class, names = {"QUERY", "MUTATION"})
    void rejectsRegularWorkBeforeRecovery(Operation operation) {
        RagOperationGate gate = new RagOperationGate();
        assertRejected(gate.tryAcquire(operation), State.RECOVERY_REQUIRED);
        assertThat(gate.state()).isEqualTo(State.RECOVERY_REQUIRED);
    }

    @Test
    void recoveryMustBeConfirmedBeforeTheGateOpens() {
        RagOperationGate gate = new RagOperationGate();
        try (var recovery = acquire(gate, Operation.RECOVERY)) {
            assertThat(gate.state()).isEqualTo(State.RECOVERING);
            assertRejected(gate.tryAcquire(Operation.QUERY), State.RECOVERING);
            assertRejected(gate.tryAcquire(Operation.MUTATION), State.RECOVERING);
            assertTrue(recovery.confirmCompletion());
        }
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @ParameterizedTest
    @EnumSource(Operation.class)
    void admitsEachOperationWhenReady(Operation operation) {
        RagOperationGate gate = readyGate();
        try (var lease = acquire(gate, operation)) {
            assertThat(gate.state()).isEqualTo(activeState(operation));
            assertTrue(lease.confirmCompletion());
        }
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @ParameterizedTest
    @MethodSource("conflictingOperations")
    void rejectsConflictingWorkWithoutChangingTheOwner(Operation active, Operation requested) {
        RagOperationGate gate = readyGate();
        try (var lease = acquire(gate, active)) {
            assertRejected(gate.tryAcquire(requested), activeState(active));
            assertThat(gate.state()).isEqualTo(activeState(active));
            assertTrue(lease.confirmCompletion());
        }
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @ParameterizedTest
    @ValueSource(booleans = {false, true})
    void queriesShareTheGateAndOnlyTheLastQueryReleasesIt(boolean confirmFirstQuery) {
        RagOperationGate gate = readyGate();
        var firstQuery = acquire(gate, Operation.QUERY);
        var secondQuery = acquire(gate, Operation.QUERY);
        if (confirmFirstQuery) {
            assertTrue(firstQuery.confirmCompletion());
        } else {
            firstQuery.close();
        }
        assertThat(gate.state()).isEqualTo(State.QUERYING);
        assertRejected(gate.tryAcquire(Operation.MUTATION), State.QUERYING);
        assertRejected(gate.tryAcquire(Operation.RECOVERY), State.QUERYING);
        assertFalse(firstQuery.confirmCompletion());
        firstQuery.close();
        assertThat(gate.state()).isEqualTo(State.QUERYING);
        secondQuery.close();
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @Test
    void anExceptionalReadOnlyQueryDoesNotRequireIndexRecovery() {
        RagOperationGate gate = readyGate();
        var originalFailure = new IllegalStateException("query failed");
        var observedFailure = assertThrows(IllegalStateException.class, () -> {
            try (var query = acquire(gate, Operation.QUERY)) {
                throw originalFailure;
            }
        });
        assertThat(observedFailure).isSameAs(originalFailure);
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @ParameterizedTest
    @EnumSource(value = Operation.class, names = {"MUTATION", "RECOVERY"})
    void closingUnconfirmedExclusiveWorkRequiresRecovery(Operation operation) {
        RagOperationGate gate = readyGate();
        var lease = acquire(gate, operation);
        lease.close();
        assertThat(gate.state()).isEqualTo(State.RECOVERY_REQUIRED);
        assertRejected(gate.tryAcquire(Operation.QUERY), State.RECOVERY_REQUIRED);
        assertRejected(gate.tryAcquire(Operation.MUTATION), State.RECOVERY_REQUIRED);
        assertFalse(lease.confirmCompletion());
        lease.close();
        assertThat(gate.state()).isEqualTo(State.RECOVERY_REQUIRED);
    }

    @ParameterizedTest
    @EnumSource(value = Operation.class, names = {"MUTATION", "RECOVERY"})
    void exceptionalExclusiveWorkStaysClosedAndPreservesTheOriginalFailure(Operation operation) {
        RagOperationGate gate = readyGate();
        var originalFailure = new IllegalStateException("result not confirmed");
        var observedFailure = assertThrows(IllegalStateException.class, () -> {
            try (var lease = acquire(gate, operation)) {
                throw originalFailure;
            }
        });
        assertThat(observedFailure).isSameAs(originalFailure);
        assertThat(gate.state()).isEqualTo(State.RECOVERY_REQUIRED);
    }

    @ParameterizedTest
    @EnumSource(Operation.class)
    void closingAnAlreadyConfirmedLeaseDoesNotUndoCompletion(Operation operation) {
        RagOperationGate gate = readyGate();
        var lease = acquire(gate, operation);
        assertTrue(lease.confirmCompletion());
        assertFalse(lease.confirmCompletion());
        lease.close();
        lease.close();
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @ParameterizedTest
    @MethodSource("allOperationPairs")
    void aCompletedLeaseCannotReleaseItsSuccessor(Operation previous, Operation next) {
        RagOperationGate gate = readyGate();
        var previousLease = acquire(gate, previous);
        assertTrue(previousLease.confirmCompletion());
        try (var nextLease = acquire(gate, next)) {
            previousLease.close();
            assertFalse(previousLease.confirmCompletion());
            assertThat(gate.state()).isEqualTo(activeState(next));
            assertTrue(nextLease.confirmCompletion());
        }
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @Test
    void aLateUnconfirmedMutationResultCannotReleaseRecovery() throws Exception {
        RagOperationGate gate = readyGate();
        var mutation = acquire(gate, Operation.MUTATION);
        var allowLateResult = new CountDownLatch(1);
        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<Boolean> lateResult = executor.submit(() -> {
                allowLateResult.await();
                return mutation.confirmCompletion();
            });
            mutation.close();
            assertRejected(gate.tryAcquire(Operation.MUTATION), State.RECOVERY_REQUIRED);
            assertRejected(gate.tryAcquire(Operation.QUERY), State.RECOVERY_REQUIRED);
            try (var recovery = acquire(gate, Operation.RECOVERY)) {
                allowLateResult.countDown();
                assertFalse(lateResult.get(5, TimeUnit.SECONDS));
                mutation.close();
                assertThat(gate.state()).isEqualTo(State.RECOVERING);
                assertRejected(gate.tryAcquire(Operation.QUERY), State.RECOVERING);
                assertTrue(recovery.confirmCompletion());
            }
            assertThat(gate.state()).isEqualTo(State.READY);
        } finally {
            allowLateResult.countDown();
            stop(executor);
        }
    }

    @ParameterizedTest
    @MethodSource("conflictingOperations")
    void busyAdmissionReturnsWhileTheActiveWorkIsStillHeld(Operation active, Operation requested)
            throws Exception {
        RagOperationGate gate = readyGate();
        var workStarted = new CountDownLatch(1);
        var finishWork = new CountDownLatch(1);
        ExecutorService executor = Executors.newFixedThreadPool(2);
        try {
            Future<Boolean> activeWork = executor.submit(() -> {
                try (var lease = acquire(gate, active)) {
                    workStarted.countDown();
                    finishWork.await();
                    return lease.confirmCompletion();
                }
            });
            assertTrue(workStarted.await(5, TimeUnit.SECONDS));
            Future<RagOperationGate.Admission> admission = executor.submit(() -> gate.tryAcquire(requested));
            assertRejected(admission.get(5, TimeUnit.SECONDS), activeState(active));
            assertThat(finishWork.getCount()).isEqualTo(1);
            assertThat(activeWork.isDone()).isFalse();
            finishWork.countDown();
            assertTrue(activeWork.get(5, TimeUnit.SECONDS));
            assertThat(gate.state()).isEqualTo(State.READY);
        } finally {
            finishWork.countDown();
            stop(executor);
        }
    }

    @ParameterizedTest
    @EnumSource(value = Operation.class, names = {"MUTATION", "RECOVERY"})
    void simultaneousExclusiveContendersHaveExactlyOneOwner(Operation operation) throws Exception {
        RagOperationGate gate = readyGate();
        int contenders = 8;
        var start = new CountDownLatch(1);
        var attempted = new CountDownLatch(contenders);
        var releaseOwner = new CountDownLatch(1);
        var admissions = new ConcurrentLinkedQueue<RagOperationGate.Admission>();
        ExecutorService executor = Executors.newFixedThreadPool(contenders);
        List<Future<?>> workers = new ArrayList<>();
        try {
            for (int workerIndex = 0; workerIndex < contenders; workerIndex++) {
                workers.add(executor.submit(() -> {
                    start.await();
                    var admission = gate.tryAcquire(operation);
                    admissions.add(admission);
                    attempted.countDown();
                    if (admission.lease().isPresent()) {
                        try (var lease = admission.lease().orElseThrow()) {
                            releaseOwner.await();
                            assertTrue(lease.confirmCompletion());
                        }
                    }
                    return null;
                }));
            }
            start.countDown();
            assertTrue(attempted.await(5, TimeUnit.SECONDS));
            assertThat(admissions).hasSize(contenders);
            assertThat(admissions.stream().filter(admission -> admission.lease().isPresent()).count()).isEqualTo(1);
            assertThat(admissions).allSatisfy(admission -> assertThat(admission.state()).isEqualTo(activeState(operation)));
            assertThat(gate.state()).isEqualTo(activeState(operation));
            releaseOwner.countDown();
            for (Future<?> worker : workers) {
                worker.get(5, TimeUnit.SECONDS);
            }
            assertThat(gate.state()).isEqualTo(State.READY);
        } finally {
            start.countDown();
            releaseOwner.countDown();
            stop(executor);
        }
    }

    @Test
    void concurrentQueriesCanAllHoldReadLeases() throws Exception {
        RagOperationGate gate = readyGate();
        int readers = 8;
        var start = new CountDownLatch(1);
        var attempted = new CountDownLatch(readers);
        var finishQueries = new CountDownLatch(1);
        var admissions = new ConcurrentLinkedQueue<RagOperationGate.Admission>();
        ExecutorService executor = Executors.newFixedThreadPool(readers);
        List<Future<?>> workers = new ArrayList<>();
        try {
            for (int readerIndex = 0; readerIndex < readers; readerIndex++) {
                workers.add(executor.submit(() -> {
                    start.await();
                    var admission = gate.tryAcquire(Operation.QUERY);
                    admissions.add(admission);
                    attempted.countDown();
                    if (admission.lease().isPresent()) {
                        try (var query = admission.lease().orElseThrow()) {
                            finishQueries.await();
                        }
                    }
                    return null;
                }));
            }
            start.countDown();
            assertTrue(attempted.await(5, TimeUnit.SECONDS));
            assertThat(admissions).hasSize(readers);
            assertThat(admissions).allSatisfy(admission -> {
                assertThat(admission.state()).isEqualTo(State.QUERYING);
                assertThat(admission.lease()).isPresent();
            });
            assertRejected(gate.tryAcquire(Operation.MUTATION), State.QUERYING);
            assertRejected(gate.tryAcquire(Operation.RECOVERY), State.QUERYING);
            finishQueries.countDown();
            for (Future<?> worker : workers) {
                worker.get(5, TimeUnit.SECONDS);
            }
            assertThat(gate.state()).isEqualTo(State.READY);
        } finally {
            start.countDown();
            finishQueries.countDown();
            stop(executor);
        }
    }

    @Test
    void aNewInstanceDoesNotInheritAnotherInstancesReadiness() {
        RagOperationGate oldGate = readyGate();
        try (var oldMutation = acquire(oldGate, Operation.MUTATION)) {
            RagOperationGate newGate = new RagOperationGate();
            assertThat(newGate.state()).isEqualTo(State.RECOVERY_REQUIRED);
            assertRejected(newGate.tryAcquire(Operation.QUERY), State.RECOVERY_REQUIRED);
            assertRejected(newGate.tryAcquire(Operation.MUTATION), State.RECOVERY_REQUIRED);
            assertTrue(oldMutation.confirmCompletion());
            assertThat(newGate.state()).isEqualTo(State.RECOVERY_REQUIRED);
        }
    }

    @Test
    void nullOperationsAreRejectedWithoutChangingReadiness() {
        RagOperationGate gate = new RagOperationGate();
        assertThrows(NullPointerException.class, () -> gate.tryAcquire(null));
        assertThat(gate.state()).isEqualTo(State.RECOVERY_REQUIRED);
        try (var recovery = acquire(gate, Operation.RECOVERY)) {
            assertThrows(NullPointerException.class, () -> gate.tryAcquire(null));
            assertThat(gate.state()).isEqualTo(State.RECOVERING);
            assertTrue(recovery.confirmCompletion());
        }
        assertThrows(NullPointerException.class, () -> gate.tryAcquire(null));
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @Test
    void componentScanningCreatesOneClosedSingletonWithoutStartingBusinessWork() {
        try (var context = new AnnotationConfigApplicationContext()) {
            context.scan(RagOperationGate.class.getPackageName());
            context.refresh();
            assertThat(context.getBeansOfType(RagOperationGate.class)).hasSize(1);
            RagOperationGate gate = context.getBean(RagOperationGate.class);
            assertThat(context.getBean(RagOperationGate.class)).isSameAs(gate);
            assertThat(gate.state()).isEqualTo(State.RECOVERY_REQUIRED);
        }
    }

    private static RagOperationGate readyGate() {
        RagOperationGate gate = new RagOperationGate();
        try (var recovery = acquire(gate, Operation.RECOVERY)) {
            assertTrue(recovery.confirmCompletion());
        }
        assertThat(gate.state()).isEqualTo(State.READY);
        return gate;
    }

    private static RagOperationGate.Lease acquire(RagOperationGate gate, Operation operation) {
        var admission = gate.tryAcquire(operation);
        assertThat(admission.state()).isEqualTo(activeState(operation));
        assertThat(admission.lease()).isPresent();
        return admission.lease().orElseThrow();
    }

    private static void assertRejected(RagOperationGate.Admission admission, State expectedState) {
        assertThat(admission.state()).isEqualTo(expectedState);
        assertThat(admission.lease()).isEmpty();
    }

    private static State activeState(Operation operation) {
        return switch (operation) {
            case QUERY -> State.QUERYING;
            case MUTATION -> State.MUTATING;
            case RECOVERY -> State.RECOVERING;
        };
    }

    private static Stream<Arguments> conflictingOperations() {
        return Stream.of(Operation.values()).flatMap(active -> Stream.of(Operation.values())
                .filter(requested -> active != Operation.QUERY || requested != Operation.QUERY)
                .map(requested -> Arguments.of(active, requested)));
    }

    private static Stream<Arguments> allOperationPairs() {
        return Stream.of(Operation.values()).flatMap(previous -> Stream.of(Operation.values())
                .map(next -> Arguments.of(previous, next)));
    }

    private static void stop(ExecutorService executor) throws InterruptedException {
        executor.shutdownNow();
        assertTrue(executor.awaitTermination(5, TimeUnit.SECONDS));
    }
}
