package com.easyrag.server.document;

import com.easyrag.server.rag.RagOperationGate;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.mock;

/**
 * ready 时序竞态的回归测试（对抗性审查发现）。
 *
 * 旧行为：扫描在 RECOVERY 租约内完成。窗口期（findPendingIds 之后、
 * confirmCompletion 之前）上传的文档，其索引任务撞 RECOVERING 拿不到
 * MUTATION 租约（BUSY，不重试），而它又不在扫描快照里——两头落空，
 * 永久停在 PENDING 直到下次手动 /ready。
 *
 * 修复后：扫描后置到闸门转 READY 之后，这类文档仍是 PENDING 会被捞到。
 */
class ReadinessRaceReproTest {

    private final RagOperationGate gate = new RagOperationGate();
    private final DocumentQueryRepository documents = mock(DocumentQueryRepository.class);

    /** 每次 submit 时记录闸门状态与是否拿到租约，模拟单线程 worker 立即执行。 */
    private final List<String> submissions = new CopyOnWriteArrayList<>();
    private final IndexingTrigger immediateWorker = new IndexingTrigger() {
        @Override
        public void submit(long documentId) {
            var admission = gate.tryAcquire(RagOperationGate.Operation.MUTATION);
            submissions.add("doc" + documentId + "@" + gate.state()
                    + (admission.lease().isPresent() ? "/GOT_LEASE" : "/BUSY"));
            admission.lease().ifPresent(RagOperationGate.Lease::confirmCompletion);
        }

        @Override
        public void submit(long documentId, RagOperationGate.Lease lease) {
            throw new AssertionError("recovery scan must let each indexing task acquire its own lease");
        }
    };

    /** 模拟库内 PENDING 集合，收录会往里加，扫描按当前快照返回。 */
    private final List<Long> pendingInDatabase = new ArrayList<>(List.of(1L));

    @Test
    @DisplayName("恢复窗口内上传的新文档：撞 BUSY 后仍被本次恢复捞到，不会永久 PENDING")
    void newUploadDuringRecoveryWindowIsStillRecovered() {
        // 闸门刚拿到 RECOVERY 租约、尚未确认完成时，新文档 99 到达：
        // 收录不过闸门（正常入库为 PENDING），其索引任务立即 submit 并撞 BUSY。
        // 用 gate 状态变化作为触发点：RECOVERING 期间执行这次"上传"。
        given(documents.findPendingIds()).willAnswer(invocation -> {
            // 扫描发生时闸门必须已是 READY（修复后的顺序）
            assertThat(gate.state()).isEqualTo(RagOperationGate.State.READY);
            return List.copyOf(pendingInDatabase);
        });

        ReadinessService service = new ReadinessService(gate, documents, immediateWorker);

        // 在 ready() 之前模拟窗口内上传：文档 99 入库为 PENDING，其 submit 撞未就绪闸门
        immediateWorker.submit(99L);
        pendingInDatabase.add(99L);

        var result = service.ready();

        System.out.println(">>> submissions = " + submissions);
        System.out.println(">>> recovered   = " + result.recovered());

        // 窗口内那次 submit 确实失败了（闸门未就绪）
        assertThat(submissions.get(0)).isEqualTo("doc99@RECOVERY_REQUIRED/BUSY");
        // 关键：恢复扫描后置，99 仍是 PENDING 于是被捞到并成功取得租约
        assertThat(result.recovered()).isEqualTo(2);
        assertThat(submissions).anyMatch(entry -> entry.equals("doc99@MUTATING/GOT_LEASE"));
        assertThat(submissions).anyMatch(entry -> entry.equals("doc1@MUTATING/GOT_LEASE"));
    }

    @Test
    @DisplayName("扫描在闸门 READY 之后执行——顺序契约")
    void scanHappensAfterGateBecomesReady() {
        given(documents.findPendingIds()).willAnswer(invocation -> {
            assertThat(gate.state()).isEqualTo(RagOperationGate.State.READY);
            return List.of(1L);
        });

        new ReadinessService(gate, documents, immediateWorker).ready();

        assertThat(submissions).containsExactly("doc1@MUTATING/GOT_LEASE");
    }
}
