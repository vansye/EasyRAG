package com.easyrag.server.document;

import com.easyrag.server.rag.RagOperationGate;
import com.easyrag.server.rag.RagOperationGate.Operation;
import com.fasterxml.jackson.annotation.JsonProperty;
import org.springframework.stereotype.Service;

import java.util.Optional;

/**
 * PUT /api/documents/{id} 与 POST /api/documents/{id}/reindex 的编排（A-2，U3）。
 *
 * 与收录（intake）的关键差异：**更新要过 MUTATION 闸门**。收录只是插入新行，
 * 对现有索引无影响；更新会改动已被索引的正文，§十三 明文要求"排队中的更新
 * 不能提前修改当前正文"——若不过闸门，正在进行的索引（已读取旧正文、正在
 * 算 embedding）会把旧向量配上新正文落库，chunk 的字节偏移全部错位。
 *
 * 变更检测（§一.2）：
 * - 哈希不变 → 只更新 updated_at，不动正文、不重索引。正文原封不动是硬要求：
 *   规范化吃掉了换行差异，哈希相同不代表字节位置相同，替换正文会让已落库
 *   chunk 的偏移错位。
 * - 哈希变化 → 写新正文、回到 PENDING、触发异步重索引。
 *
 * 顺序约束（与 ReadinessService 同一条）：必须先 confirmCompletion() 释放
 * 闸门，再 submit 索引任务。DocumentIndexingService.index() 自己要申请
 * MUTATION 租约，持租提交必然让它撞 BUSY——文档停在 PENDING，直到手动恢复。
 *
 * 不像 DELETE 那样调用 Python：更新有自愈路径，重索引的 /embed 按 document_id
 * 整篇替换旧向量，不需要先清。这与删除的不对称正是 DELETE 选"先清后落库"
 * 的理由（见 DocumentDeletionService）。
 */
@Service
public class DocumentUpdateService {

    private final RagOperationGate gate;
    private final DocumentUpdateRepository documents;
    private final IndexingTrigger indexingTrigger;

    public DocumentUpdateService(RagOperationGate gate, DocumentUpdateRepository documents,
                                 IndexingTrigger indexingTrigger) {
        this.gate = gate;
        this.documents = documents;
        this.indexingTrigger = indexingTrigger;
    }

    /** 上传文件形态的更新：校验扩展名与大小，标题可降级到文件名。 */
    public UpdateResult updateFromUpload(long documentId, String filename, byte[] bytes) {
        return update(documentId, current -> DocumentContent.fromUpload(filename, bytes).content());
    }

    /** JSON 形态的更新：没有文件名，标题降级到文档现有标题。 */
    public UpdateResult updateFromText(long documentId, String text) {
        return update(documentId, current -> DocumentContent.fromText(text, current.title()));
    }

    private UpdateResult update(long documentId, ContentParser parser) {
        if (documentId <= 0) {
            throw new IllegalArgumentException("document_id must be positive");
        }
        RagOperationGate.Admission admission = gate.tryAcquire(Operation.MUTATION);
        if (admission.lease().isEmpty()) {
            throw new Unavailable(admission.state().name());
        }
        boolean reindex;
        DocumentUpdateRepository.CurrentDocument current;
        try (RagOperationGate.Lease lease = admission.lease().orElseThrow()) {
            Optional<DocumentUpdateRepository.CurrentDocument> found = documents.findCurrent(documentId);
            if (found.isEmpty()) {
                lease.confirmCompletion();
                throw new NotFound();
            }
            current = found.orElseThrow();
            // 解析在租约内：校验失败（不支持的类型、超限、非 UTF-8）不该改任何状态，
            // 而 Rejected 逃出 try 时 close() 会把闸门退回 RECOVERY_REQUIRED——
            // 那是误报，校验失败根本没碰索引。所以这里捕获后显式确认完成。
            DocumentContent content;
            try {
                content = parser.parse(current);
            } catch (DocumentIntakeService.Rejected rejected) {
                lease.confirmCompletion();
                throw rejected;
            }
            reindex = !content.contentHash().equals(current.contentHash());
            boolean applied = reindex
                    ? documents.updateContent(documentId, content)
                    : documents.touch(documentId);
            if (!applied) {
                // 并发删除已先完成（守护 UPDATE 未命中）
                lease.confirmCompletion();
                throw new NotFound();
            }
            lease.confirmCompletion();
        }
        if (reindex) {
            // 必须在租约释放后：index() 自己要 MUTATION 租约（见类注释）
            indexingTrigger.submit(documentId);
            return new UpdateResult(documentId, "PENDING", true);
        }
        return new UpdateResult(documentId, current.indexStatus(), false);
    }

    /** 手动重索引：状态允许则回到 PENDING 并排队（裁决：FAILED 与 INDEXED 均可）。 */
    public UpdateResult reindex(long documentId) {
        if (documentId <= 0) {
            throw new IllegalArgumentException("document_id must be positive");
        }
        RagOperationGate.Admission admission = gate.tryAcquire(Operation.MUTATION);
        if (admission.lease().isEmpty()) {
            throw new Unavailable(admission.state().name());
        }
        try (RagOperationGate.Lease lease = admission.lease().orElseThrow()) {
            DocumentUpdateRepository.MarkResult result = documents.markForReindex(documentId);
            lease.confirmCompletion();
            switch (result) {
                case MISSING -> throw new NotFound();
                case IN_FLIGHT -> throw new InFlight();
                case QUEUED -> { }
            }
        }
        indexingTrigger.submit(documentId);
        return new UpdateResult(documentId, "PENDING", true);
    }

    private interface ContentParser {
        DocumentContent parse(DocumentUpdateRepository.CurrentDocument current);
    }

    /** reindexed=false 表示哈希未变、跳过了重索引（U3 的"改了但内容等价"分支）。 */
    public record UpdateResult(@JsonProperty("id") long id,
                               @JsonProperty("index_status") String indexStatus,
                               @JsonProperty("reindexed") boolean reindexed) {}

    public static final class NotFound extends RuntimeException {
        NotFound() {
            super("document not found or deleted");
        }
    }

    /** 已在队列或索引在途：HTTP 层映射 409，与"文档不存在"必须可区分。 */
    public static final class InFlight extends RuntimeException {
        InFlight() {
            super("document is already queued or being indexed");
        }
    }

    /** 闸门不可用：HTTP 层映射 503，携带当前闸门状态。 */
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
}
