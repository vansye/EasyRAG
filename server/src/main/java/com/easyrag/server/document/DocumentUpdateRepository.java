package com.easyrag.server.document;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.json.JsonMapper;

import java.util.List;
import java.util.Optional;

/**
 * 更新与重索引的落库侧（A-2，U3）。
 *
 * 与 DocumentIndexRepository 的分工：那里管索引状态机推进（PENDING →
 * INDEXING → INDEXED/FAILED），这里管用户发起的正文变更与重索引排队。
 * 两个仓库都写 index_status，但语义不同——一个是索引编排的内部流转，
 * 一个是把文档重新放回队列起点。
 */
@Repository
public class DocumentUpdateRepository {

    private static final JsonMapper JSON = JsonMapper.builder().build();
    private final JdbcTemplate jdbcTemplate;

    public DocumentUpdateRepository(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    /** 变更检测要的现状：哈希（比对用）、标题（JSON 更新时的标题兜底）、当前状态。 */
    public Optional<CurrentDocument> findCurrent(long documentId) {
        requireDocumentId(documentId);
        return jdbcTemplate.query("""
                SELECT id, title, content_hash, index_status FROM document
                WHERE id = ? AND deleted_at IS NULL
                """, (resultSet, rowNumber) -> new CurrentDocument(
                resultSet.getLong("id"), resultSet.getString("title"),
                resultSet.getString("content_hash"), resultSet.getString("index_status")),
                documentId).stream().findFirst();
    }

    /**
     * 哈希变化：写入新正文并回到 PENDING，等待重索引。
     *
     * 不在这里删旧 chunk——ChunkRepository.replace() 会在重索引时原子地
     * 删旧插新。中间窗口保留旧 chunk 是有意的：若先删，Python 索引里的旧
     * 向量仍可能被检索命中，而 MySQL 已无对应行，出处补全返回空——用户
     * 看到"答案带 [n] 引用但没有出处"。保留旧 chunk 则答案与出处一致（都是
     * 旧内容），只是字节偏移指向新正文，前端按 sources 里的 text 展示即可。
     * 这就是 PENDING 的含义：已收录，尚未生效。
     */
    @Transactional
    public boolean updateContent(long documentId, DocumentContent content) {
        requireDocumentId(documentId);
        return jdbcTemplate.update("""
                UPDATE document
                SET content = ?, content_hash = ?, title = ?, tags = ?,
                    index_status = 'PENDING', index_error = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND deleted_at IS NULL
                """, content.content(), content.contentHash(), content.title(),
                JSON.writeValueAsString(content.tags()), documentId) == 1;
    }

    /**
     * 哈希不变：只动 updated_at，不碰正文与 chunks。
     *
     * 子 Issue A §一.2 的明文要求——哈希相同不代表 UTF-8 字节位置相同
     * （规范化吃掉了换行差异），悄悄替换正文会让已落库 chunk 的字节偏移
     * 全部错位。要保存仅格式不同的新正文，就必须重切重建索引，不能走这条。
     */
    @Transactional
    public boolean touch(long documentId) {
        requireDocumentId(documentId);
        return jdbcTemplate.update("""
                UPDATE document SET updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND deleted_at IS NULL
                """, documentId) == 1;
    }

    /**
     * 手动重索引：把文档放回队列起点。
     *
     * 允许的起始状态 = FAILED 与 INDEXED（2026-09-13 裁决，选项一）。
     * FAILED 是题面明确的"满足恢复条件后可手动重试"；INDEXED 也放行是因为
     * 换 tokenizer / 切片参数后需要重算的入口——PUT 同哈希会跳过重索引，
     * 若 reindex 也只认 FAILED，M4 做切分策略对照实验就只剩全量 reset 一条路。
     * PENDING / INDEXING 不放行：任务已在队列或在途，重复排队没有意义。
     */
    @Transactional
    public MarkResult markForReindex(long documentId) {
        requireDocumentId(documentId);
        int updated = jdbcTemplate.update("""
                UPDATE document SET index_status = 'PENDING', index_error = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND deleted_at IS NULL AND index_status IN ('FAILED', 'INDEXED')
                """, documentId);
        if (updated == 1) {
            return MarkResult.QUEUED;
        }
        // 没更新到：要么文档不在（404），要么状态不允许（409）——分开报，
        // 否则"已在索引中"会被误报成"文档不存在"
        Long present = jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM document WHERE id = ? AND deleted_at IS NULL", Long.class, documentId);
        return present != null && present > 0 ? MarkResult.IN_FLIGHT : MarkResult.MISSING;
    }

    private static void requireDocumentId(long documentId) {
        if (documentId <= 0) {
            throw new IllegalArgumentException("document_id must be positive");
        }
    }

    public enum MarkResult {
        QUEUED, IN_FLIGHT, MISSING
    }

    public record CurrentDocument(long documentId, String title, String contentHash, String indexStatus) {}
}
