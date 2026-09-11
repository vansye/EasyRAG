-- 修正 V1 的两处定义。
--
-- 为什么不直接改 V1：V1 已在开发库里执行过，改动它会让 Flyway 校验和不符而
-- 拒绝启动（这正是迁移工具该有的行为）。加一条 V2 是 Flyway 的正常用法，
-- 也让"改了什么、为什么"留在版本历史里，而不是悄悄重写历史。

-- 1) updated_at 去掉 ON UPDATE CURRENT_TIMESTAMP
--
-- 原定义会让本列变成"行最后被写时间"，而语义要的是"内容更新时间"。
-- 索引状态机每轮至少写三次这一行（PENDING→INDEXING→INDEXED），A-2 的
-- 重启重置更会把一批文档的时间集体推到开机时刻——用户什么都没改，
-- 列表却重排了。改由应用在真正的内容更新路径上显式写入。
ALTER TABLE document
    MODIFY COLUMN updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP;

-- 2) (document_id, seq) 升为唯一约束
--
-- 重索引是"旧 chunk 全删 + 新 chunk 插入"。这一步半途失败时，唯一约束能让
-- 不一致当场炸，而不是留下重复 seq 静默污染检索。这是最便宜的一道防线。
ALTER TABLE chunk
    DROP INDEX idx_document_seq,
    ADD UNIQUE KEY uk_document_seq (document_id, seq);
