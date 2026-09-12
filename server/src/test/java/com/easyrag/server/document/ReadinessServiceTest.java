package com.easyrag.server.document;

import com.easyrag.server.rag.RagOperationGate;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.List;
import java.util.ArrayList;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.BDDMockito.given;
import static org.mockito.BDDMockito.then;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;

/**
 * 就绪恢复的行为测试。闸门用真实实例而非 mock——RagOperationGate 无依赖，
 * 真实状态机才能验证「RECOVERY_REQUIRED → READY」的迁移与互斥语义；
 * 只有数据访问与触发器是 mock 的。
 *
 * 守住的关键顺序：确认就绪（闸门转 READY）必须先于提交 PENDING，
 * 否则刚恢复的任务撞 RECOVERING 态全部 BUSY。
 */
class ReadinessServiceTest {

    private final RagOperationGate gate = new RagOperationGate();
    private final DocumentQueryRepository documents = mock(DocumentQueryRepository.class);
    private final IndexingTrigger indexingTrigger = mock(IndexingTrigger.class);
    private final ReadinessService service = new ReadinessService(gate, documents, indexingTrigger);

    @Test
    @DisplayName("从 RECOVERY_REQUIRED 恢复：转 READY，扫描到的 PENDING 逐篇重提，recovered 计数正确")
    void recoversGateAndResubmitsPendingDocuments() {
        given(documents.findPendingIds()).willReturn(List.of(1L, 3L, 7L));

        ReadinessService.ReadinessResult result = service.ready();

        assertThat(result.state()).isEqualTo("READY");
        assertThat(result.recovered()).isEqualTo(3);
        ArgumentCaptor<Long> submitted = ArgumentCaptor.forClass(Long.class);
        then(indexingTrigger).should(times(3)).submit(submitted.capture());
        assertThat(submitted.getAllValues()).containsExactly(1L, 3L, 7L);
    }

    @Test
    @DisplayName("顺序契约：每次 submit 执行时闸门必须已是 READY")
    void gateMustBeReadyBeforeAnySubmission() {
        given(documents.findPendingIds()).willReturn(List.of(1L, 2L));
        List<String> statesAtSubmit = new ArrayList<>();
        doAnswer(invocation -> {
            statesAtSubmit.add(gate.state().name());
            return null;
        }).when(indexingTrigger).submit(anyLong());

        service.ready();

        // 若 confirmCompletion 晚于 submit，这里会捕获到 RECOVERING
        assertThat(statesAtSubmit).containsExactly("READY", "READY");
    }

    @Test
    @DisplayName("无 PENDING 时同样转 READY，recovered=0")
    void recoversWithNothingPending() {
        given(documents.findPendingIds()).willReturn(List.of());

        ReadinessService.ReadinessResult result = service.ready();

        assertThat(result.state()).isEqualTo("READY");
        assertThat(result.recovered()).isZero();
        then(indexingTrigger).shouldHaveNoInteractions();
    }

    @Test
    @DisplayName("有未结束活动（QUERYING）时：不夺取闸门，抛 Busy 并携带状态")
    void refusesWhenGateHasActiveQueries() {
        // 闸门初态 RECOVERY_REQUIRED 不放行 QUERY，先走一轮恢复转 READY
        try (var recovery = gate.tryAcquire(RagOperationGate.Operation.RECOVERY).lease().orElseThrow()) {
            recovery.confirmCompletion();
        }
        try (RagOperationGate.Lease ignored = gate.tryAcquire(RagOperationGate.Operation.QUERY).lease().orElseThrow()) {
            assertThatThrownBy(service::ready)
                    .isInstanceOf(ReadinessService.Busy.class)
                    .hasMessageContaining("QUERYING");
        }
        then(indexingTrigger).shouldHaveNoInteractions();
        // 释放 QUERY 后闸门回到 READY
        assertThat(gate.state()).isEqualTo(RagOperationGate.State.READY);
    }

    @Test
    @DisplayName("扫描抛错时闸门回到 RECOVERY_REQUIRED，不提交任何文档")
    void scanFailureLeavesGateRequiringRecovery() {
        given(documents.findPendingIds()).willThrow(new IllegalStateException("db down"));

        assertThatThrownBy(service::ready).isInstanceOf(IllegalStateException.class);

        // lease 未 confirmCompletion，close() 使状态落回 RECOVERY_REQUIRED——
        // 这正是闸门的设计：未确认完成的恢复必须可被观测并重做
        assertThat(gate.state()).isEqualTo(RagOperationGate.State.RECOVERY_REQUIRED);
        then(indexingTrigger).shouldHaveNoInteractions();
    }

    @Test
    @DisplayName("READY 态重复调用恢复：幂等，返回 recovered=0")
    void repeatedReadyIsIdempotent() {
        given(documents.findPendingIds()).willReturn(List.of());

        service.ready();
        ReadinessService.ReadinessResult again = service.ready();

        assertThat(again.state()).isEqualTo("READY");
        assertThat(again.recovered()).isZero();
    }
}
