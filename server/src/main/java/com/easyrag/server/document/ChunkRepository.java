package com.easyrag.server.document;

import org.springframework.jdbc.core.ConnectionCallback;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import java.sql.Connection;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

@Repository
public class ChunkRepository {

    private final JdbcTemplate jdbcTemplate;

    public ChunkRepository(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public List<StoredChunk> replace(long documentId, String expectedContent, ChunkBatch batch) {
        if (documentId <= 0) {
            throw new IllegalArgumentException("document_id must be positive");
        }
        batch.validateAgainst(expectedContent);
        int isolationLevel = jdbcTemplate.execute((ConnectionCallback<Integer>) Connection::getTransactionIsolation);
        if (isolationLevel != Connection.TRANSACTION_READ_COMMITTED) {
            throw new IllegalStateException("chunk replacement requires READ_COMMITTED transaction isolation");
        }
        String currentContent = jdbcTemplate.queryForObject("""
                SELECT content FROM document WHERE id = ? AND deleted_at IS NULL FOR UPDATE
                """, String.class, documentId);
        if (!expectedContent.equals(currentContent)) {
            throw new IllegalStateException("document content changed since chunking");
        }

        jdbcTemplate.update("DELETE FROM chunk WHERE document_id = ?", documentId);
        List<StoredChunk> saved = new ArrayList<>(batch.chunks().size());
        for (ChunkBatch.Chunk chunk : batch.chunks()) {
            GeneratedKeyHolder keys = new GeneratedKeyHolder();
            jdbcTemplate.update(connection -> {
                var statement = connection.prepareStatement("""
                        INSERT INTO chunk (document_id, seq, text, char_start, char_end, heading_path, token_count)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, Statement.RETURN_GENERATED_KEYS);
                statement.setLong(1, documentId);
                statement.setInt(2, chunk.seq());
                statement.setString(3, chunk.text());
                statement.setInt(4, chunk.byteStart());
                statement.setInt(5, chunk.byteEnd());
                statement.setString(6, chunk.headingPath());
                statement.setInt(7, chunk.tokenCount());
                return statement;
            }, keys);
            long chunkId = Objects.requireNonNull(keys.getKey(), "generated chunk id is missing").longValue();
            saved.add(new StoredChunk(chunkId, documentId, chunk.seq(), chunk.text(), chunk.byteStart(),
                    chunk.byteEnd(), chunk.headingPath(), chunk.tokenCount()));
        }
        jdbcTemplate.update("UPDATE document SET chunk_count = ? WHERE id = ?", saved.size(), documentId);
        return List.copyOf(saved);
    }

    public record StoredChunk(long chunkId, long documentId, int seq, String text,
                              int byteStart, int byteEnd, String headingPath, int tokenCount) {}
}
