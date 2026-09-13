package com.easyrag.server.document;

import com.easyrag.server.rag.RagOperationGate;
import com.easyrag.server.rag.RagOperationGate.Operation;
import com.easyrag.server.rag.RagOperationGate.State;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.web.client.RestClientException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.BDDMockito.given;
import static org.mockito.BDDMockito.then;
import static org.mockito.BDDMockito.willThrow;
import static org.mockito.Mockito.mock;

/**
 * 删除编排的语义测试：真闸门 + mock 仓库与 Python 客户端。
 *
 * 守住的行为（方案 A 与 §十三 的失败语义）：先清索引后落库；清索引失败时
 * DB 不动且闸门退回 RECOVERY_REQUIRED；落库失败同样退回；任何失败路径
 * 都不能留下"删除了一半"的状态。
 */
class DocumentDeletionServiceTest {

    private RagOperationGate gate;
    private DocumentQueryRepository documents;
    private DocumentDeletionRepository deletions;
    private IndexClient indexClient;
    private DocumentDeletionService service;

    @BeforeEach
    void setUp() {
        gate = new RagOperationGate();
        documents = mock(DocumentQueryRepository.class);
        deletions = mock(DocumentDeletionRepository.class);
        indexClient = mock(IndexClient.class);
        service = new DocumentDeletionService(gate, documents, deletions, indexClient);
        // 闸门初态 RECOVERY_REQUIRED，测试从 READY 起步（等价于 /api/admin/ready 已确认）
        gate.tryAcquire(Operation.RECOVERY).lease().orElseThrow().confirmCompletion();
    }

    @Test
    @DisplayName("成功路径：先清索引后落库，闸门回到 READY")
    void deletesIndexFirstThenDatabase() {
        given(documents.existsActive(42L)).willReturn(true);
        given(deletions.deleteDocument(42L)).willReturn(true);

        service.delete(42L);

        then(indexClient).should().deleteDocument(42L);
        then(deletions).should().deleteDocument(42L);
        then(indexClient).shouldHaveNoMoreInteractions();
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @Test
    @DisplayName("文档不存在：404，不动 Python，闸门回到 READY")
    void rejectsMissingDocumentWithoutTouchingPython() {
        given(documents.existsActive(999L)).willReturn(false);

        assertThatThrownBy(() -> service.delete(999L)).isInstanceOf(DocumentDeletionService.NotFound.class);

        then(indexClient).shouldHaveNoInteractions();
        then(deletions).shouldHaveNoInteractions();
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @Test
    @DisplayName("Python 清索引失败：502，DB 不动，闸门退回 RECOVERY_REQUIRED（结果不明）")
    void indexCleanupFailureLeavesDatabaseUntouchedAndRequiresRecovery() {
        given(documents.existsActive(42L)).willReturn(true);
        willThrow(new RestClientException("connection refused")).given(indexClient).deleteDocument(42L);

        assertThatThrownBy(() -> service.delete(42L))
                .isInstanceOf(DocumentDeletionService.IndexCleanupFailed.class);

        then(deletions).shouldHaveNoInteractions();
        assertThat(gate.state()).isEqualTo(State.RECOVERY_REQUIRED);
    }

    @Test
    @DisplayName("落库失败：500，事务语义由仓库保证，闸门退回 RECOVERY_REQUIRED")
    void storeFailureRequiresRecovery() {
        given(documents.existsActive(42L)).willReturn(true);
        given(deletions.deleteDocument(42L)).willThrow(new IllegalStateException("db down"));

        assertThatThrownBy(() -> service.delete(42L))
                .isInstanceOf(DocumentDeletionService.StoreFailed.class);

        assertThat(gate.state()).isEqualTo(State.RECOVERY_REQUIRED);
    }

    @Test
    @DisplayName("守护 UPDATE 未命中（并发删除先完成）：404，闸门回到 READY")
    void concurrentDeletionMapsToNotFound() {
        given(documents.existsActive(42L)).willReturn(true);
        given(deletions.deleteDocument(42L)).willReturn(false);

        assertThatThrownBy(() -> service.delete(42L)).isInstanceOf(DocumentDeletionService.NotFound.class);

        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @Test
    @DisplayName("闸门被占用（MUTATING）：503，不碰任何存储")
    void rejectsWhenGateBusy() {
        gate.tryAcquire(Operation.MUTATION);
        given(documents.existsActive(anyLong())).willReturn(true);

        assertThatThrownBy(() -> service.delete(42L))
                .isInstanceOf(DocumentDeletionService.Unavailable.class)
                .extracting("state").isEqualTo(State.MUTATING.name());

        then(indexClient).shouldHaveNoInteractions();
        then(deletions).shouldHaveNoInteractions();
    }

    @Test
    @DisplayName("非法 id：直接拒绝，不申请闸门")
    void rejectsNonPositiveIdBeforeGate() {
        assertThatThrownBy(() -> service.delete(0L)).isInstanceOf(IllegalArgumentException.class);

        assertThat(gate.state()).isEqualTo(State.READY);
        then(indexClient).shouldHaveNoInteractions();
    }
}
