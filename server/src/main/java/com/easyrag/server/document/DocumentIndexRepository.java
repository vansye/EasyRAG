package com.easyrag.server.document;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

@Repository
public class DocumentIndexRepository {

    private static final JsonMapper JSON = JsonMapper.builder().build();
    private final JdbcTemplate jdbcTemplate;
    private final ChunkRepository chunkRepository;

    public DocumentIndexRepository(JdbcTemplate jdbcTemplate, ChunkRepository chunkRepository) {
        this.jdbcTemplate = jdbcTemplate;
        this.chunkRepository = chunkRepository;
    }

    public Optional<PendingDocument> findPending(long documentId) {
        requireDocumentId(documentId);
        return jdbcTemplate.query("""
                SELECT id, content, title, tags FROM document
                WHERE id = ? AND deleted_at IS NULL AND index_status = 'PENDING'
                """, (resultSet, rowNumber) -> new PendingDocument(resultSet.getLong("id"),
                resultSet.getString("content"), resultSet.getString("title"),
                readTags(resultSet.getString("tags"))), documentId).stream().findFirst();
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public List<ChunkRepository.StoredChunk> saveChunksAndMarkIndexing(PendingDocument document, ChunkBatch batch) {
        requireDocumentId(document.documentId());
        requireTransition(jdbcTemplate.update("""
                UPDATE document SET index_status = 'INDEXING', index_error = NULL
                WHERE id = ? AND deleted_at IS NULL AND index_status = 'PENDING'
                """, document.documentId()), document.documentId());
        return chunkRepository.replace(document.documentId(), document.content(), batch);
    }

    @Transactional
    public void markIndexed(long documentId) {
        requireDocumentId(documentId);
        requireTransition(jdbcTemplate.update("""
                UPDATE document SET index_status = 'INDEXED', index_error = NULL, indexed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND deleted_at IS NULL AND index_status = 'INDEXING'
                """, documentId), documentId);
    }

    @Transactional
    public void markFailed(long documentId, String error) {
        requireDocumentId(documentId);
        if (error == null || error.isBlank() || !StandardCharsets.UTF_8.newEncoder().canEncode(error)
                || error.codePointCount(0, error.length()) > 1024) {
            throw new IllegalArgumentException("index_error must be non-blank valid UTF-8 and at most 1024 Unicode code points");
        }
        requireTransition(jdbcTemplate.update("""
                UPDATE document SET index_status = 'FAILED', index_error = ?
                WHERE id = ? AND deleted_at IS NULL AND index_status IN ('PENDING', 'INDEXING')
                """, error, documentId), documentId);
    }

    private static List<String> readTags(String storedTags) {
        if (storedTags == null) {
            return List.of();
        }
        JsonNode tags = JSON.readTree(storedTags);
        if (tags.isNull()) {
            return List.of();
        }
        if (!tags.isArray()) {
            throw new IllegalStateException("document tags must be an array of strings");
        }
        List<String> values = new ArrayList<>(tags.size());
        for (JsonNode tag : tags) {
            if (!tag.isString()) {
                throw new IllegalStateException("document tags must be an array of strings");
            }
            values.add(tag.stringValue());
        }
        return List.copyOf(values);
    }

    private static void requireDocumentId(long documentId) {
        if (documentId <= 0) {
            throw new IllegalArgumentException("document_id must be positive");
        }
    }

    private static void requireTransition(int updated, long documentId) {
        if (updated != 1) {
            throw new IllegalStateException("document is missing, deleted, or not in an eligible indexing state: " + documentId);
        }
    }

    public record PendingDocument(long documentId, String content, String title, List<String> tags) {
        public PendingDocument {
            tags = List.copyOf(tags);
        }
    }
}
