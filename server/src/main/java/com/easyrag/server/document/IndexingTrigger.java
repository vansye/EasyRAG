package com.easyrag.server.document;

import org.springframework.stereotype.Component;

/**
 * 收录完成后的索引推进入口。
 *
 * 语义约束：submit 必须在 document 行已持久化（事务已提交）之后调用，
 * 传入的 documentId 必须真实存在。索引何时开始、何时完成由实现方决定，
 * 收录方不等待、不感知结果（A-1 文档 §九：收录本身不过闸门，只有索引过闸门）。
 */
public interface IndexingTrigger {

    void submit(long documentId);

    /**
     * PR-1 的占位实现：异步索引执行器（@Async 版本）在下一个 PR 接入前，
     * 收录只落库为 PENDING——U1 只要求列表可见，索引推进是独立增量。
     * 接入真正的执行器时删除本类。
     */
    @Component
    class Noop implements IndexingTrigger {

        @Override
        public void submit(long documentId) {
            // 故意为空
        }
    }
}
