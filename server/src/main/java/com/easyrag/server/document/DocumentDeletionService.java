package com.easyrag.server.document;

import com.easyrag.server.rag.RagOperationGate;
import com.easyrag.server.rag.RagOperationGate.Operation;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * DELETE /api/documents/{id} 的编排（A-2，U2）。
 *
 * 顺序裁决（方案 A，2026-09-13）：先清 Python 索引，成功后才落库删除。
 * 与索引失败路径（A-5：落库 FAILED 后尽力清）方向相反，理由是数据流的不对称：
 * 更新/重索引有自愈路径——/embed 按 document_id 整篇替换旧向量；删除没有——
 * 已删的文档永远不会再来一次 embed，若先落库后清失败，旧向量永久残留，
 * 检索会命中已删文档（出处补全被 deleted_at 过滤后表现为"答案带 [n] 但出处为空"）。
 * 先清后落库的极端情况（清成功但落库失败）只造成"文档在列表但检索不到"，
 * reindex 可修，不会出现"已删文档被引用"。
 *
 * 失败语义（§十三）：
 * - Python 清索引失败：结果不明（分不出"请求未送达"与"已执行但响应丢失"），
 *   不确认租约——闸门退回 RECOVERY_REQUIRED，DB 不动，调用方可稍后重试。
 * - 落库失败：无法确认 MySQL 业务终态，同样不确认租约。
 * 两条都不是"删除成功了一半"：DB 未动或事务回滚，文档可原样重试删除。
 *
 * 不按 index_status 设门槛：MUTATION 租约与索引任务互斥，持有租约期间不可能
 * 有在途 embed；PENDING 文档的排队任务会在 LOAD_DOCUMENT 阶段因 deleted_at
 * 过滤返回 SKIPPED，不会把已删文档的向量写回去。
 */
@Service
public class DocumentDeletionService {

    private static final Logger LOGGER = LoggerFactory.getLogger(DocumentDeletionService.class);

    private final RagOperationGate gate;
    private final DocumentQueryRepository documents;
    private final DocumentDeletionRepository deletions;
    private final IndexClient indexClient;

    public DocumentDeletionService(RagOperationGate gate, DocumentQueryRepository documents,
                                   DocumentDeletionRepository deletions, IndexClient indexClient) {
        this.gate = gate;
        this.documents = documents;
        this.deletions = deletions;
        this.indexClient = indexClient;
    }

    public void delete(long documentId) {
        if (documentId <= 0) {
            throw new IllegalArgumentException("document_id must be positive");
        }
        RagOperationGate.Admission admission = gate.tryAcquire(Operation.MUTATION);
        if (admission.lease().isEmpty()) {
            // RECOVERY_REQUIRED / MUTATING / RECOVERING：明确反馈不可删除
            throw new Unavailable(admission.state().name());
        }
        try (RagOperationGate.Lease lease = admission.lease().orElseThrow()) {
            if (!documents.existsActive(documentId)) {
                lease.confirmCompletion();
                throw new NotFound();
            }
            try {
                indexClient.deleteDocument(documentId);
            } catch (RuntimeException failure) {
                LOGGER.warn("index cleanup for document {} failed: {}", documentId, failure.toString(), failure);
                throw new IndexCleanupFailed(failure);
            }
            boolean removed;
            try {
                removed = deletions.deleteDocument(documentId);
            } catch (RuntimeException failure) {
                LOGGER.warn("deleting document {} from MySQL failed: {}", documentId, failure.toString(), failure);
                throw new StoreFailed(failure);
            }
            if (!removed) {
                // 并发删除已先完成（守护 UPDATE 未命中）：Python 清理是幂等 no-op，按 404 返回
                lease.confirmCompletion();
                throw new NotFound();
            }
            lease.confirmCompletion();
        }
    }

    public static final class NotFound extends RuntimeException {
        NotFound() {
            super("document not found or deleted");
        }
    }

    /** 闸门不可用：HTTP 层映射为 503，携带当前闸门状态。 */
    public static final class Unavailable extends RuntimeException {
        private final String state;

        Unavailable(String state) {
            super("gate is " + state);
            this.state = state;
        }

        public String state() {
            return state;
        }
    }

    /** Python 清索引失败：文档未删除，结果不明故闸门退回需恢复。HTTP 层映射 502。 */
    public static final class IndexCleanupFailed extends RuntimeException {
        IndexCleanupFailed(Throwable cause) {
            super("index cleanup failed", cause);
        }
    }

    /** MySQL 落库失败：事务回滚，文档未删除。HTTP 层映射 500。 */
    public static final class StoreFailed extends RuntimeException {
        StoreFailed(Throwable cause) {
            super("document deletion failed", cause);
        }
    }
}
