package com.easyrag.server.document;

import com.easyrag.server.rag.RagOperationGate;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * 就绪恢复入口（A1-1 / A1-2）。
 *
 * 闸门初态是 RECOVERY_REQUIRED：进程刚起时无法确认索引与 MySQL 是否一致
 * （上次可能崩在 EMBED 之后、MARK_INDEXED 之前），这个态存在的意义就是
 * 强迫「确认」成为显式动作，而不是重启后假装一致。
 *
 * 执行顺序有一个不可交换的点：必须先 confirmCompletion() 把闸门转 READY，
 * 再 submit PENDING 文档。RECOVERING 态下 MUTATION 会被拒，顺序反了的话
 * 刚恢复提交的任务全部撞 BUSY，又变成「上传了但永远不索引」。
 *
 * recovered 是重新提交的 PENDING 篇数，不是索引成功数——索引在后台异步
 * 进行，结果看各文档的 index_status（A1-1 接口契约）。
 */
@Service
public class ReadinessService {

    private final RagOperationGate gate;
    private final DocumentQueryRepository documents;
    private final IndexingTrigger indexingTrigger;

    public ReadinessService(RagOperationGate gate, DocumentQueryRepository documents,
                            IndexingTrigger indexingTrigger) {
        this.gate = gate;
        this.documents = documents;
        this.indexingTrigger = indexingTrigger;
    }

    public ReadinessResult ready() {
        RagOperationGate.Admission admission = gate.tryAcquire(RagOperationGate.Operation.RECOVERY);
        if (admission.lease().isEmpty()) {
            // QUERYING / MUTATING / RECOVERING：有未结束的活动，不夺取闸门
            throw new Busy(admission.state());
        }
        List<Long> pending;
        try (RagOperationGate.Lease lease = admission.lease().orElseThrow()) {
            pending = documents.findPendingIds();
            lease.confirmCompletion();
        }
        pending.forEach(indexingTrigger::submit);
        return new ReadinessResult(gate.state().name(), pending.size());
    }

    public record ReadinessResult(String state, int recovered) {}

    /** 有未结束的变更：HTTP 层映射为 409，携带当前闸门状态。 */
    public static final class Busy extends RuntimeException {
        private final RagOperationGate.State state;

        Busy(RagOperationGate.State state) {
            super("gate is " + state);
            this.state = state;
        }

        public RagOperationGate.State state() {
            return state;
        }
    }
}
