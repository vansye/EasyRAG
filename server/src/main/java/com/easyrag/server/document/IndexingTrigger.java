package com.easyrag.server.document;

/**
 * 收录完成后的索引推进入口。
 *
 * 语义约束：submit 必须在 document 行已持久化（事务已提交）之后调用，
 * 传入的 documentId 必须真实存在。索引何时开始、何时完成由实现方决定，
 * 收录方不等待、不感知结果（A-1 文档 §九：收录本身不过闸门，只有索引过闸门）。
 *
 * 实现见 AsyncIndexingTrigger（单线程执行器 + @Async）。
 */
public interface IndexingTrigger {

    void submit(long documentId);
}
