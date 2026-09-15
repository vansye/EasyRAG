package com.easyrag.server.document;

import com.easyrag.server.rag.RagOperationGate;
import com.easyrag.server.rag.AsyncIndexingConfig;
import org.springframework.core.task.TaskRejectedException;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.BDDMockito.given;
import static org.mockito.BDDMockito.then;
import static org.mockito.Mockito.mock;

/**
 * 异步触发器的行为边界：只做转发，不吞业务信号——index() 的返回值与
 * 可预期异常都在其内部处理完毕，本类的职责只剩两件：
 * 调用 index()，以及不让未预期异常炸掉执行器线程。
 *
 * @Async 的线程语义由 Spring 容器保证，不在单元测试范围；这里直接调
 * submit() 验证同步路径下的行为。
 */
class AsyncIndexingTriggerTest {

    private final DocumentIndexingService indexingService = mock(DocumentIndexingService.class);
    private final AsyncIndexingTrigger trigger = new AsyncIndexingTrigger(indexingService);

    @Test
    @DisplayName("submit 转发给 index()，一次一条")
    void forwardsToIndexingService() {
        given(indexingService.index(42L)).willReturn(new DocumentIndexingService.IndexingResult(
                42L, DocumentIndexingService.Outcome.INDEXED, false, null, List.of(), 1L));

        trigger.submit(42L);

        then(indexingService).should().index(42L);
    }

    @Test
    @DisplayName("index() 返回 BUSY（闸门未就绪）：不抛错，文档留在 PENDING 等恢复扫描")
    void busyOutcomeIsNotAnError() {
        given(indexingService.index(7L)).willReturn(new DocumentIndexingService.IndexingResult(
                7L, DocumentIndexingService.Outcome.BUSY, true, null, List.of(), 1L));

        assertThatCode(() -> trigger.submit(7L)).doesNotThrowAnyException();
    }

    @Test
    @DisplayName("index() 抛未预期异常：吞掉并记日志，不污染执行器线程")
    void unexpectedFailureIsContained() {
        given(indexingService.index(9L)).willThrow(new IllegalStateException("boom"));

        assertThatCode(() -> trigger.submit(9L)).doesNotThrowAnyException();
        then(indexingService).should().index(9L);
    }

    @Test
    void passesTheMutationLeaseToTheExistingIndexingWorkflow() {
        var gate = new RagOperationGate();
        gate.tryAcquire(RagOperationGate.Operation.RECOVERY).lease().orElseThrow().confirmCompletion();
        var lease = gate.tryAcquire(RagOperationGate.Operation.MUTATION).lease().orElseThrow();
        given(indexingService.index(42L, lease)).willAnswer(invocation -> {
            assertThat(gate.state()).isEqualTo(RagOperationGate.State.MUTATING);
            lease.confirmCompletion();
            return new DocumentIndexingService.IndexingResult(42L,
                    DocumentIndexingService.Outcome.INDEXED, false, null, List.of(), 1L);
        });

        trigger.submit(42L, lease);

        then(indexingService).should().index(42L, lease);
        assertThat(gate.state()).isEqualTo(RagOperationGate.State.READY);
    }

    @Test
    void unexpectedFailureOfAHeldMutationLeaseRequiresRecovery() {
        var gate = new RagOperationGate();
        gate.tryAcquire(RagOperationGate.Operation.RECOVERY).lease().orElseThrow().confirmCompletion();
        var lease = gate.tryAcquire(RagOperationGate.Operation.MUTATION).lease().orElseThrow();
        given(indexingService.index(9L, lease)).willThrow(new IllegalStateException("failure"));

        assertThatCode(() -> trigger.submit(9L, lease)).doesNotThrowAnyException();

        assertThat(gate.state()).isEqualTo(RagOperationGate.State.RECOVERY_REQUIRED);
        assertThat(gate.tryAcquire(RagOperationGate.Operation.QUERY).lease()).isEmpty();
    }

    @Test
    void executorShutdownRejectsSubmissionsInsteadOfSilentlyLosingTheirLease() {
        var executor = new AsyncIndexingConfig().indexingExecutor();
        executor.shutdown();

        assertThatThrownBy(() -> executor.execute(() -> {})).isInstanceOf(TaskRejectedException.class);
    }
}
