package com.easyrag.server.document;

import com.fasterxml.jackson.annotation.JsonProperty;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.json.JsonMapper;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

@Repository
public class DocumentManagementRepository {

    private static final JsonMapper JSON = JsonMapper.builder().build();
    private final JdbcTemplate jdbc;

    public DocumentManagementRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public Optional<DocumentRecord> findActive(long documentId) {
        return jdbc.query("""
                SELECT d.id, d.title, d.content, d.content_hash, d.tags, d.source_type, d.source_uri,
                       d.index_status, d.index_error, d.created_at, d.updated_at,
                       (SELECT COUNT(*) FROM chunk c WHERE c.document_id = d.id) AS chunk_count
                FROM document d WHERE d.id = ? AND d.deleted_at IS NULL
                """, (row, index) -> new DocumentRecord(new DocumentDetail(
                row.getLong("id"), row.getString("title"), row.getString("content"),
                DocumentQueryRepository.readTags(row.getString("tags")), row.getString("source_type"),
                row.getString("source_uri"), row.getString("index_status"), row.getString("index_error"),
                row.getInt("chunk_count"), row.getTimestamp("created_at").toLocalDateTime(),
                row.getTimestamp("updated_at").toLocalDateTime()), row.getString("content_hash")), documentId)
                .stream().findFirst();
    }

    @Transactional
    public void touchUpdatedAt(long documentId) {
        requireUpdated(jdbc.update("""
                UPDATE document SET updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND deleted_at IS NULL AND index_status <> 'INDEXING'
                """, documentId), documentId);
    }

    @Transactional
    public void replaceContentAndClearChunks(long documentId, String content, String contentHash,
                                             String title, List<String> tags) {
        requireUpdated(jdbc.update("""
                UPDATE document SET content = ?, content_hash = ?, title = ?, tags = ?,
                                    index_status = 'PENDING', index_error = NULL, chunk_count = 0,
                                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND deleted_at IS NULL AND index_status <> 'INDEXING'
                """, content, contentHash, title, JSON.writeValueAsString(tags), documentId), documentId);
        jdbc.update("DELETE FROM chunk WHERE document_id = ?", documentId);
    }

    @Transactional
    public void clearChunksAndMarkPending(long documentId) {
        requireUpdated(jdbc.update("""
                UPDATE document SET index_status = 'PENDING', index_error = NULL, chunk_count = 0
                WHERE id = ? AND deleted_at IS NULL AND index_status <> 'INDEXING'
                """, documentId), documentId);
        jdbc.update("DELETE FROM chunk WHERE document_id = ?", documentId);
    }

    @Transactional
    public void softDeleteAndClearChunks(long documentId) {
        requireUpdated(jdbc.update("""
                UPDATE document SET deleted_at = CURRENT_TIMESTAMP, chunk_count = 0
                WHERE id = ? AND deleted_at IS NULL AND index_status <> 'INDEXING'
                """, documentId), documentId);
        jdbc.update("DELETE FROM chunk WHERE document_id = ?", documentId);
    }

    private static void requireUpdated(int count, long documentId) {
        if (count != 1) {
            throw new IllegalStateException("document is missing, deleted, or still indexing: " + documentId);
        }
    }

    public record DocumentRecord(DocumentDetail detail, String contentHash) {}

    public record DocumentDetail(long id, String title, String content, List<String> tags,
                                 @JsonProperty("source_type") String sourceType,
                                 @JsonProperty("source_uri") String sourceUri,
                                 @JsonProperty("index_status") String indexStatus,
                                 @JsonProperty("index_error") String indexError,
                                 @JsonProperty("chunk_count") int chunkCount,
                                 @JsonProperty("created_at") LocalDateTime createdAt,
                                 @JsonProperty("updated_at") LocalDateTime updatedAt) {
        public DocumentDetail {
            tags = List.copyOf(tags);
        }
    }

}
