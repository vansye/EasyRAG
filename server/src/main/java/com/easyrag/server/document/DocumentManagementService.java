package com.easyrag.server.document;

import com.easyrag.server.rag.RagOperationGate;
import com.fasterxml.jackson.annotation.JsonProperty;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.nio.charset.StandardCharsets;

/** 先撤掉旧索引，再提交原文变更；异步重建完成前始终持有同一变更租约。 */
@Service
public class DocumentManagementService {

    private static final Logger LOGGER = LoggerFactory.getLogger(DocumentManagementService.class);
    private final DocumentManagementRepository documents;
    private final RagOperationGate gate;
    private final IndexClient indexClient;
    private final IndexingTrigger indexingTrigger;

    public DocumentManagementService(DocumentManagementRepository documents, RagOperationGate gate,
                                     IndexClient indexClient, IndexingTrigger indexingTrigger) {
        this.documents = documents;
        this.gate = gate;
        this.indexClient = indexClient;
        this.indexingTrigger = indexingTrigger;
    }

    public ChangeResult update(long documentId, String content) {
        validateContent(content);
        return scheduleIndexing(documentId, content);
    }

    public ChangeResult reindex(long documentId) {
        return scheduleIndexing(documentId, null);
    }

    private ChangeResult scheduleIndexing(long documentId, String newContent) {
        RagOperationGate.Lease lease = acquireMutation();
        boolean handedOff = false;
        String stage = "LOAD_DOCUMENT";
        try {
            var document = findForMutation(documentId, lease);
            String hash = newContent == null ? null : DocumentIntakeService.contentHash(newContent);
            if (newContent != null && hash.equals(document.contentHash())) {
                // 相同哈希不代表相同字节位置：保留当前原文、切片、元数据与索引状态。
                stage = "TOUCH_DOCUMENT";
                documents.touchUpdatedAt(documentId);
                lease.confirmCompletion();
                return new ChangeResult(documentId, document.detail().indexStatus(), false);
            }
            stage = "DELETE_INDEX";
            indexClient.deleteDocument(documentId);
            stage = "SAVE_DOCUMENT";
            if (newContent == null) {
                documents.clearChunksAndMarkPending(documentId);
            } else {
                var metadata = DocumentIntakeService.FrontMatter.parse(newContent);
                String title = DocumentIntakeService.resolveTitle(metadata.title(), metadata.body(),
                        fallbackTitle(document.detail()));
                documents.replaceContentAndClearChunks(documentId, newContent, hash, title, metadata.tags());
            }
            // 仓库的短事务已提交。租约随任务进入现有执行器，不提前开放问答。
            stage = "SUBMIT_INDEXING";
            indexingTrigger.submit(documentId, lease);
            handedOff = true;
            return new ChangeResult(documentId, "PENDING", true);
        } catch (NotFound | Busy failure) {
            throw failure;
        } catch (RuntimeException failure) {
            throw failed(documentId, stage, failure);
        } finally {
            if (!handedOff) {
                lease.close();
            }
        }
    }

    public void delete(long documentId) {
        RagOperationGate.Lease lease = acquireMutation();
        String stage = "LOAD_DOCUMENT";
        try (lease) {
            findForMutation(documentId, lease);
            stage = "DELETE_INDEX";
            indexClient.deleteDocument(documentId);
            stage = "DELETE_DOCUMENT";
            documents.softDeleteAndClearChunks(documentId);
            lease.confirmCompletion();
        } catch (NotFound | Busy failure) {
            throw failure;
        } catch (RuntimeException failure) {
            throw failed(documentId, stage, failure);
        }
    }

    private RagOperationGate.Lease acquireMutation() {
        if (TransactionSynchronizationManager.isActualTransactionActive()) {
            throw new IllegalStateException("document management cannot run inside a database transaction");
        }
        var admission = gate.tryAcquire(RagOperationGate.Operation.MUTATION);
        return admission.lease().orElseThrow(() -> new Busy(admission.state()));
    }

    private DocumentManagementRepository.DocumentRecord findForMutation(long documentId, RagOperationGate.Lease lease) {
        var document = documents.findActive(documentId);
        if (document.isEmpty()) {
            lease.confirmCompletion();
            throw new NotFound();
        }
        if (document.orElseThrow().detail().indexStatus().equals("INDEXING")) {
            // 重启留下的 INDEXING 不能因一次 ready 调用就当作已结束的任务。
            throw new Busy(RagOperationGate.State.RECOVERY_REQUIRED);
        }
        return document.orElseThrow();
    }

    private static String fallbackTitle(DocumentManagementRepository.DocumentDetail document) {
        if (!document.sourceType().equals("UPLOAD")) {
            return document.title();
        }
        String filename = document.sourceUri();
        int dot = filename.lastIndexOf('.');
        return dot < 0 ? filename : filename.substring(0, dot);
    }

    private static void validateContent(String content) {
        if (content == null || content.isBlank()) {
            throw new DocumentIntakeService.Rejected("正文不能为空");
        }
        if (!StandardCharsets.UTF_8.newEncoder().canEncode(content)) {
            throw new DocumentIntakeService.Rejected("正文不是有效的 UTF-8 文本");
        }
        int bytes = content.getBytes(StandardCharsets.UTF_8).length;
        if (bytes > DocumentIntakeService.MAX_CONTENT_BYTES) {
            throw new DocumentIntakeService.Rejected("正文 " + bytes + " 字节超过上限 "
                    + DocumentIntakeService.MAX_CONTENT_BYTES + " 字节（1 MB），请拆分后再保存");
        }
    }

    private static MutationFailed failed(long documentId, String stage, RuntimeException failure) {
        LOGGER.atWarn().addKeyValue("document_id", documentId).addKeyValue("stage", stage)
                .addKeyValue("failure", failure.getClass().getSimpleName()).log("document_management_failed");
        return new MutationFailed("资料变更未能确认完成，问答已暂停，请先检查恢复状态", failure);
    }

    public record ChangeResult(long id, @JsonProperty("index_status") String indexStatus, boolean reindexed) {}

    public static final class NotFound extends RuntimeException {
        public NotFound() {
            super("资料不存在或已删除");
        }
    }

    public static final class Busy extends RuntimeException {
        private final RagOperationGate.State state;

        public Busy(RagOperationGate.State state) {
            super("资料暂不可修改");
            this.state = state;
        }

        public RagOperationGate.State state() {
            return state;
        }
    }

    public static final class MutationFailed extends RuntimeException {
        public MutationFailed(String message, RuntimeException cause) {
            super(message, cause);
        }
    }
}
