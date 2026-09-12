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
 * 再扫描并 submit PENDING 文档。两个理由，缺一不可：
 *
 * 1. RECOVERING 态下 MUTATION 会被拒，顺序反了的话刚提交的任务全部撞 BUSY。
 * 2. 扫描也必须后置：RECOVERING 期间上传的文档，其索引任务已撞 BUSY 被消费
 *    且不重试，若扫描快照取在它入库之前，它两头落空、永久停在 PENDING。
 *    转 READY 后再扫，这类文档仍是 PENDING，会被本次捞到。
 *
 * 第 2 点是第一版漏掉的：当时只把 confirm 提到 submit 之前，扫描仍留在租约
 * 内，于是留下一个真实竞态窗口（由对抗性审查发现，ReadinessRaceReproTest
 * 锚定了旧行为）。写了守顺序的测试不等于顺序想全了。
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
        // 先扫描后确认会留下竞态（见类注释第 2 点），所以顺序是：确认就绪 → 扫描。
        // 扫描失败时必须把闸门退回 RECOVERY_REQUIRED——"不知道有哪些 PENDING 待
        // 推进"就等于恢复没走完，闸门停在 READY 会让这个失败静默消失。
        //
        // 退回用"再取一个 RECOVERY 租约但不确认完成"实现：不能在扫描全程持有租约，
        // 那会让闸门在扫描期间回到 RECOVERING，重新打开刚修掉的那个窗口。
        try (RagOperationGate.Lease lease = admission.lease().orElseThrow()) {
            lease.confirmCompletion();
        }
        List<Long> pending;
        try {
            // 扫描时闸门已是 READY：窗口期内撞过 BUSY 的文档仍是 PENDING，会被捞到；
            // 扫描之后上传的文档，其 submit 自己就能取得 MUTATION 租约。
            //
            // 可能重复提交（扫到的文档其 submit 也许正在进行）——安全：index() 的
            // findPending 只认 PENDING，重复提交得到 SKIPPED 并正常释放租约。宁可
            // 多做一次幂等操作，也不要留下永久 PENDING 的文档。
            pending = documents.findPendingIds();
        } catch (RuntimeException scanFailure) {
            markRecoveryIncomplete();
            throw scanFailure;
        }
        pending.forEach(indexingTrigger::submit);
        return new ReadinessResult(gate.state().name(), pending.size());
    }

    /** 把闸门退回 RECOVERY_REQUIRED：取一个 RECOVERY 租约后不确认完成即释放。 */
    private void markRecoveryIncomplete() {
        gate.tryAcquire(RagOperationGate.Operation.RECOVERY).lease()
                .ifPresent(RagOperationGate.Lease::close);
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
