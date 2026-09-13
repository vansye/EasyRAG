package com.easyrag.server.document;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

/**
 * 删除的落库侧（A-2，子 Issue A §一.4）：document 软删（删了什么可追溯、
 * 历史引用不悬空），chunk 硬删（派生物无保留价值——删掉后检索自然引用不到，
 * 这就是 U2 的实现）。
 */
@Repository
public class DocumentDeletionRepository {

    private final JdbcTemplate jdbcTemplate;

    public DocumentDeletionRepository(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    /**
     * 软删 document 并硬删其全部 chunk，同一事务。
     *
     * @return false 表示文档本就不存在或已删（守护 UPDATE 未命中），调用方按 404 处理
     */
    @Transactional
    public boolean deleteDocument(long documentId) {
        if (documentId <= 0) {
            throw new IllegalArgumentException("document_id must be positive");
        }
        int updated = jdbcTemplate.update("""
                UPDATE document SET deleted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND deleted_at IS NULL
                """, documentId);
        if (updated != 1) {
            return false;
        }
        jdbcTemplate.update("DELETE FROM chunk WHERE document_id = ?", documentId);
        return true;
    }
}
