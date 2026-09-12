package com.easyrag.server.document;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Component;

/**
 * 异步索引推进（A-1 §三 IndexingTrigger 的真实现）。
 *
 * submit 只做一件事：把 documentId 交给单线程执行器。全部编排逻辑在
 * DocumentIndexingService.index() —— 闸门申请、五阶段推进、失败清理都在
 * 那里，本类不重复任何业务判断。
 *
 * 两条边界：
 * 1. 闸门未就绪（RECOVERY_REQUIRED）时 index() 返回 Outcome.BUSY，文档
 *    停在 PENDING——不重试、不改状态。何时重推由 ReadinessService 的
 *    恢复扫描决定（A1-2），执行器不自作主张。
 * 2. 异常只记日志：submit 的调用方（收录接口）早已返回，异常无人可抛；
 *    index() 内部已把失败落为 FAILED + index_error，这里再抛只会污染
 *    执行器线程的日志。
 */
@Component
public class AsyncIndexingTrigger implements IndexingTrigger {

    private static final Logger LOGGER = LoggerFactory.getLogger(AsyncIndexingTrigger.class);

    private final DocumentIndexingService indexingService;

    public AsyncIndexingTrigger(DocumentIndexingService indexingService) {
        this.indexingService = indexingService;
    }

    @Override
    @Async("indexingExecutor")
    public void submit(long documentId) {
        try {
            DocumentIndexingService.IndexingResult result = indexingService.index(documentId);
            if (result.outcome() == DocumentIndexingService.Outcome.BUSY) {
                LOGGER.info("document {} left PENDING: gate not ready, rescan on /api/admin/ready", documentId);
            }
        } catch (RuntimeException failure) {
            // index() 对可预期失败已有完整落库与日志；到这里的是未预期路径
            LOGGER.error("unexpected failure indexing document {}", documentId, failure);
        }
    }
}
