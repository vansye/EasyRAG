package com.easyrag.server.document;

import com.easyrag.server.document.DocumentManagementRepository.DocumentDetail;
import com.easyrag.server.document.DocumentManagementRepository.DocumentRecord;
import com.easyrag.server.rag.RagOperationGate;
import com.easyrag.server.rag.RagOperationGate.Operation;
import com.easyrag.server.rag.RagOperationGate.State;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;
import org.mockito.ArgumentCaptor;
import org.springframework.core.task.TaskRejectedException;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.web.client.ResourceAccessException;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.LocalDateTime;
import java.util.HexFormat;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class DocumentManagementServiceTest {

    private static final long ID = 42L;
    private static final String ORIGINAL = "# 旧标题\r\n\r\n正文😀\r\n";
    private final DocumentManagementRepository documents = mock(DocumentManagementRepository.class);
    private final IndexClient indexClient = mock(IndexClient.class);
    private final IndexingTrigger trigger = mock(IndexingTrigger.class);
    private RagOperationGate gate;
    private DocumentManagementService service;

    @BeforeEach
    void setUp() {
        gate = new RagOperationGate();
        gate.tryAcquire(Operation.RECOVERY).lease().orElseThrow().confirmCompletion();
        service = new DocumentManagementService(documents, gate, indexClient, trigger);
        when(documents.findActive(ID)).thenReturn(Optional.of(document("INDEXED")));
    }

    @Test
    void missingAndDeletedDocumentsAreNotFoundWithoutClosingReadyGate() {
        when(documents.findActive(ID)).thenReturn(Optional.empty());
        assertThatThrownBy(() -> service.update(ID, "新正文")).isInstanceOf(DocumentManagementService.NotFound.class);
        assertThatThrownBy(() -> service.delete(ID)).isInstanceOf(DocumentManagementService.NotFound.class);
        assertThatThrownBy(() -> service.reindex(ID)).isInstanceOf(DocumentManagementService.NotFound.class);
        assertThat(gate.state()).isEqualTo(State.READY);
        verifyNoInteractions(indexClient, trigger);
    }

    @ParameterizedTest
    @ValueSource(strings = {"INDEXED", "FAILED", "PENDING"})
    void unchangedNormalizedContentOnlyTouchesTimestampAndPreservesStatus(String status) {
        when(documents.findActive(ID)).thenReturn(Optional.of(document(status)));

        var result = service.update(ID, "  # 旧标题\n\n正文😀\n  ");

        assertThat(result).isEqualTo(new DocumentManagementService.ChangeResult(ID, status, false));
        verify(documents).touchUpdatedAt(ID);
        verify(documents, never()).replaceContentAndClearChunks(eq(ID), anyString(), anyString(), anyString(), anyList());
        assertThat(gate.state()).isEqualTo(State.READY);
        verifyNoInteractions(indexClient, trigger);
    }

    @Test
    void changedContentRefreshesMetadataAndHandsTheHeldLeaseToAsyncIndexing() {
        String changed = "---\ntitle: 新标题\ntags: [new, \"知识\"]\n---\n\n# 正文\n已更新😀";
        when(indexClient.deleteDocument(ID)).thenAnswer(invocation -> {
            assertThat(gate.state()).isEqualTo(State.MUTATING);
            return 1;
        });
        doAnswer(invocation -> {
            assertThat(gate.tryAcquire(Operation.QUERY).lease()).isEmpty();
            return null;
        }).when(documents).replaceContentAndClearChunks(ID, changed, hash(changed), "新标题", List.of("new", "知识"));

        assertThat(service.update(ID, changed))
                .isEqualTo(new DocumentManagementService.ChangeResult(ID, "PENDING", true));

        var lease = ArgumentCaptor.forClass(RagOperationGate.Lease.class);
        var sequence = inOrder(indexClient, documents, trigger);
        sequence.verify(documents).findActive(ID);
        sequence.verify(indexClient).deleteDocument(ID);
        sequence.verify(documents).replaceContentAndClearChunks(ID, changed, hash(changed), "新标题", List.of("new", "知识"));
        sequence.verify(trigger).submit(eq(ID), lease.capture());
        assertThat(gate.state()).isEqualTo(State.MUTATING);
        assertThat(gate.tryAcquire(Operation.QUERY).lease()).isEmpty();
        assertThat(lease.getValue().confirmCompletion()).isTrue();
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @ParameterizedTest
    @ValueSource(strings = {"FAILED", "PENDING", "INDEXED"})
    void manualReindexClearsOldChunksAndQueuesACompleteRebuildWithoutChangingContent(String status) {
        when(documents.findActive(ID)).thenReturn(Optional.of(document(status)));

        assertThat(service.reindex(ID))
                .isEqualTo(new DocumentManagementService.ChangeResult(ID, "PENDING", true));

        var lease = ArgumentCaptor.forClass(RagOperationGate.Lease.class);
        var sequence = inOrder(indexClient, documents, trigger);
        sequence.verify(documents).findActive(ID);
        sequence.verify(indexClient).deleteDocument(ID);
        sequence.verify(documents).clearChunksAndMarkPending(ID);
        sequence.verify(trigger).submit(eq(ID), lease.capture());
        verify(documents, never()).replaceContentAndClearChunks(eq(ID), anyString(), anyString(), anyString(), anyList());
        verify(documents, never()).touchUpdatedAt(ID);
        assertThat(gate.state()).isEqualTo(State.MUTATING);
        lease.getValue().confirmCompletion();
    }

    @Test
    void executorRejectionAfterSaveDoesNotReleaseAnUnfinishedMutationAsReady() {
        doThrow(new TaskRejectedException("executor-secret"))
                .when(trigger).submit(eq(ID), any(RagOperationGate.Lease.class));

        assertThatThrownBy(() -> service.reindex(ID))
                .isInstanceOf(DocumentManagementService.MutationFailed.class)
                .hasMessageNotContaining("executor-secret");

        verify(documents).clearChunksAndMarkPending(ID);
        assertThat(gate.state()).isEqualTo(State.RECOVERY_REQUIRED);
        assertThat(gate.tryAcquire(Operation.QUERY).lease()).isEmpty();
    }

    @ParameterizedTest
    @EnumSource(value = State.class, names = {"QUERYING", "MUTATING", "RECOVERING", "RECOVERY_REQUIRED"})
    void busyMutationDoesNotReadOrChangeDocumentsOrCallPython(State state) {
        var held = gate.tryAcquire(switch (state) {
            case QUERYING -> Operation.QUERY;
            case RECOVERING -> Operation.RECOVERY;
            default -> Operation.MUTATION;
        }).lease().orElseThrow();
        if (state == State.RECOVERY_REQUIRED) {
            held.close();
        }
        try {
            assertThatThrownBy(() -> service.update(ID, "新正文"))
                    .isInstanceOfSatisfying(DocumentManagementService.Busy.class,
                            failure -> assertThat(failure.state()).isEqualTo(state));
            assertThatThrownBy(() -> service.reindex(ID)).isInstanceOf(DocumentManagementService.Busy.class);
            assertThatThrownBy(() -> service.delete(ID)).isInstanceOf(DocumentManagementService.Busy.class);
            verifyNoInteractions(documents, indexClient, trigger);
            assertThat(gate.state()).isEqualTo(state);
        } finally {
            held.close();
        }
    }

    @Test
    void deleteClearsIndexBeforeSoftDeleteAndHoldsGateUntilDatabaseCommitReturns() {
        when(indexClient.deleteDocument(ID)).thenAnswer(invocation -> {
            assertThat(gate.tryAcquire(Operation.QUERY).lease()).isEmpty();
            assertThat(TransactionSynchronizationManager.isActualTransactionActive()).isFalse();
            return 1;
        });
        doAnswer(invocation -> {
            assertThat(gate.state()).isEqualTo(State.MUTATING);
            return null;
        }).when(documents).softDeleteAndClearChunks(ID);

        service.delete(ID);

        var sequence = inOrder(indexClient, documents);
        sequence.verify(documents).findActive(ID);
        sequence.verify(indexClient).deleteDocument(ID);
        sequence.verify(documents).softDeleteAndClearChunks(ID);
        assertThat(gate.state()).isEqualTo(State.READY);
        verifyNoInteractions(trigger);
    }

    @Test
    void failedIndexCleanupKeepsContentAndChunksAndClosesQueryGate() {
        when(indexClient.deleteDocument(ID)).thenThrow(new ResourceAccessException("transport-secret"));

        assertThatThrownBy(() -> service.update(ID, "新正文"))
                .isInstanceOf(DocumentManagementService.MutationFailed.class)
                .hasMessageNotContaining("transport-secret");

        verify(documents, never()).replaceContentAndClearChunks(eq(ID), anyString(), anyString(), anyString(), anyList());
        verifyNoInteractions(trigger);
        assertThat(gate.state()).isEqualTo(State.RECOVERY_REQUIRED);
        assertThat(gate.tryAcquire(Operation.QUERY).lease()).isEmpty();
    }

    @Test
    void failedDeleteCleanupDoesNotSoftDelete() {
        when(indexClient.deleteDocument(ID)).thenThrow(new ResourceAccessException("timeout"));

        assertThatThrownBy(() -> service.delete(ID)).isInstanceOf(DocumentManagementService.MutationFailed.class);

        verify(documents, never()).softDeleteAndClearChunks(ID);
        assertThat(gate.state()).isEqualTo(State.RECOVERY_REQUIRED);
    }

    @Test
    void uncertainDatabaseCommitAfterCleanupRequiresRecovery() {
        doThrow(new DataAccessResourceFailureException("database-secret"))
                .when(documents).softDeleteAndClearChunks(ID);

        assertThatThrownBy(() -> service.delete(ID))
                .isInstanceOf(DocumentManagementService.MutationFailed.class)
                .hasMessageNotContaining("database-secret");

        assertThat(gate.state()).isEqualTo(State.RECOVERY_REQUIRED);
        verifyNoInteractions(trigger);
    }

    @Test
    void lingeringIndexingDocumentCannotBeRequeuedByAReadyGate() {
        when(documents.findActive(ID)).thenReturn(Optional.of(document("INDEXING")));

        assertThatThrownBy(() -> service.reindex(ID))
                .isInstanceOfSatisfying(DocumentManagementService.Busy.class,
                        failure -> assertThat(failure.state()).isEqualTo(State.RECOVERY_REQUIRED));

        assertThat(gate.state()).isEqualTo(State.RECOVERY_REQUIRED);
        verifyNoInteractions(indexClient, trigger);
    }

    @ParameterizedTest
    @NullAndEmptySource
    @ValueSource(strings = {" \r\n\t", "\ud800"})
    void rejectsEmptyOrInvalidUtf8ContentBeforeAnyEffects(String content) {
        assertThatThrownBy(() -> service.update(ID, content)).isInstanceOf(DocumentIntakeService.Rejected.class);
        verifyNoInteractions(documents, indexClient, trigger);
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @Test
    void rejectsContentOverOneMegabyteInUtf8Bytes() {
        assertThatThrownBy(() -> service.update(ID, "中".repeat(350000)))
                .isInstanceOf(DocumentIntakeService.Rejected.class)
                .hasMessageContaining("1050000").hasMessageContaining("1048576");
        verifyNoInteractions(documents, indexClient, trigger);
    }

    @Test
    void rejectsAnOuterTransactionBeforeTakingTheGate() {
        TransactionSynchronizationManager.setActualTransactionActive(true);
        try {
            assertThatThrownBy(() -> service.delete(ID)).isInstanceOf(IllegalStateException.class);
            assertThatThrownBy(() -> service.reindex(ID)).isInstanceOf(IllegalStateException.class);
            assertThatThrownBy(() -> service.update(ID, "新正文")).isInstanceOf(IllegalStateException.class);
            verifyNoInteractions(documents, indexClient, trigger);
            assertThat(gate.state()).isEqualTo(State.READY);
        } finally {
            TransactionSynchronizationManager.setActualTransactionActive(false);
        }
    }

    private static DocumentRecord document(String status) {
        var timestamp = LocalDateTime.of(2026, 9, 14, 10, 0);
        return new DocumentRecord(new DocumentDetail(ID, "旧标题", ORIGINAL, List.of("old"),
                "UPLOAD", "原始笔记.md", status, status.equals("FAILED") ? "old failure" : null,
                1, timestamp, timestamp), hash(ORIGINAL));
    }

    private static String hash(String content) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                    .digest(content.replace("\r\n", "\n").replace("\r", "\n").strip().getBytes(StandardCharsets.UTF_8)));
        } catch (java.security.NoSuchAlgorithmException impossible) {
            throw new AssertionError(impossible);
        }
    }
}
