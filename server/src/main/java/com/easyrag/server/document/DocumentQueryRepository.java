package com.easyrag.server.document;

import com.fasterxml.jackson.annotation.JsonProperty;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.Optional;

/**
 * document 的收录写入与读侧查询。
 *
 * 写入只有一条路径：insertPending（收录入口）。状态流转（PENDING →
 * INDEXING → INDEXED / FAILED）属于索引编排，归 DocumentIndexRepository；
 * 两个仓库不做对方的事。
 *
 * 列表的 chunk_count 用实时 COUNT 子查询（A1-3）：document.chunk_count 列
 * 只在 ChunkRepository.replace() 一处维护，A-2 加入删除后会出现第二条
 * chunk 变更路径，列的正确性变成「每条路径都记得更新」的持续义务；
 * COUNT 在构造上不可能过期。口径：列是缓存，COUNT 是事实。
 */
@Repository
public class DocumentQueryRepository {

    private static final JsonMapper JSON = JsonMapper.builder().build();
    private final JdbcTemplate jdbcTemplate;

    public DocumentQueryRepository(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    @Transactional
    public long insertPending(NewDocument document) {
        GeneratedKeyHolder keys = new GeneratedKeyHolder();
        jdbcTemplate.update(connection -> {
            var statement = connection.prepareStatement("""
                    INSERT INTO document (source_type, source_uri, title, content, content_hash, tags, index_status)
                    VALUES (?, ?, ?, ?, ?, ?, 'PENDING')
                    """, Statement.RETURN_GENERATED_KEYS);
            statement.setString(1, document.sourceType());
            statement.setString(2, document.sourceUri());
            statement.setString(3, document.title());
            statement.setString(4, document.content());
            statement.setString(5, document.contentHash());
            statement.setString(6, JSON.writeValueAsString(document.tags()));
            return statement;
        }, keys);
        return Objects.requireNonNull(keys.getKey(), "generated document id is missing").longValue();
    }

    public DocumentPage findPage(String status, int page, int size) {
        boolean filtered = status != null && !status.isBlank();
        String where = filtered ? "deleted_at IS NULL AND index_status = ?" : "deleted_at IS NULL";

        Long total = filtered
                ? jdbcTemplate.queryForObject("SELECT COUNT(*) FROM document WHERE " + where, Long.class, status)
                : jdbcTemplate.queryForObject("SELECT COUNT(*) FROM document WHERE " + where, Long.class);

        // 用 formatted 而非 text block 拼接：收尾引号前的空白会被 text block
        // 剥掉、拼接处也没有换行，"WHERE """ + where + """ORDER BY" 实际拼出
        // "WHEREdeleted_at...ORDER BY"。mock JdbcTemplate 不解析 SQL，这个错
        // 只有真 MySQL 的 IT 能暴露（单测 454 绿照跑不误）。
        String itemsSql = """
                SELECT d.id, d.title, d.source_type, d.tags, d.index_status, d.updated_at,
                       (SELECT COUNT(*) FROM chunk c WHERE c.document_id = d.id) AS chunk_count
                FROM document d
                WHERE %s
                ORDER BY d.updated_at DESC, d.id DESC
                LIMIT ? OFFSET ?
                """.formatted(where);
        List<DocumentSummary> items = filtered
                ? jdbcTemplate.query(itemsSql, this::mapSummary, status, size, (long) page * size)
                : jdbcTemplate.query(itemsSql, this::mapSummary, size, (long) page * size);
        return new DocumentPage(total == null ? 0 : total, items);
    }

    /** 恢复扫描用（A1-2）：现存全部 PENDING 文档的 id，按 id 升序。 */
    public List<Long> findPendingIds() {
        return jdbcTemplate.queryForList(
                "SELECT id FROM document WHERE deleted_at IS NULL AND index_status = 'PENDING' ORDER BY id", Long.class);
    }

    /**
     * 单篇详情（A-2）。chunk_count 沿用列表的实时 COUNT 口径（A1-3：列是缓存，
     * COUNT 是事实）；index_error 如实透出，FAILED 的排查入口就在这里。
     */
    public Optional<DocumentDetail> findDetail(long documentId) {
        return jdbcTemplate.query("""
                SELECT d.id, d.title, d.content, d.tags, d.source_type, d.source_uri,
                       d.index_status, d.index_error, d.created_at, d.updated_at,
                       (SELECT COUNT(*) FROM chunk c WHERE c.document_id = d.id) AS chunk_count
                FROM document d
                WHERE d.id = ? AND d.deleted_at IS NULL
                """, this::mapDetail, documentId).stream().findFirst();
    }

    /** 切片透明度接口的数据侧（A-2 §三）：一篇的 chunk 按 seq 排序。 */
    public List<ChunkRow> findChunks(long documentId) {
        return jdbcTemplate.query("""
                SELECT id, seq, text, char_start, char_end, heading_path, token_count
                FROM chunk
                WHERE document_id = ?
                ORDER BY seq
                """, this::mapChunkRow, documentId);
    }

    /** 切片接口的 404 判定：文档现存且未软删（chunk 硬删保证已删文档无残留切片）。 */
    public boolean existsActive(long documentId) {
        Long count = jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM document WHERE id = ? AND deleted_at IS NULL", Long.class, documentId);
        return count != null && count > 0;
    }

    private DocumentDetail mapDetail(ResultSet resultSet, int rowNumber) throws SQLException {
        return new DocumentDetail(
                resultSet.getLong("id"),
                resultSet.getString("title"),
                resultSet.getString("content"),
                readTags(resultSet.getString("tags")),
                resultSet.getString("source_type"),
                resultSet.getString("source_uri"),
                resultSet.getString("index_status"),
                resultSet.getString("index_error"),
                resultSet.getInt("chunk_count"),
                resultSet.getTimestamp("created_at").toLocalDateTime(),
                resultSet.getTimestamp("updated_at").toLocalDateTime());
    }

    private ChunkRow mapChunkRow(ResultSet resultSet, int rowNumber) throws SQLException {
        return new ChunkRow(
                resultSet.getLong("id"),
                resultSet.getInt("seq"),
                resultSet.getString("text"),
                resultSet.getInt("char_start"),
                resultSet.getInt("char_end"),
                resultSet.getString("heading_path"),
                resultSet.getInt("token_count"));
    }

    /**
     * 按批量 chunk_id 查出处（问答流的溯源补全，U4）。
     *
     * JOIN document 拿标题（业务真相在 MySQL，Python 只有 chunk_id——见子
     * Issue C §四"为什么出处由 Java 补"）。软删文档的 chunk 不返回：软删
     * 后检索自然查不到新向量，但旧向量在删除同步（A-2）落地前仍可能命中，
     * 这里挡住"答案引用已删文档"的口径。
     */
    public List<ChunkSource> findSources(List<Long> chunkIds) {
        if (chunkIds == null || chunkIds.isEmpty()) {
            return List.of();
        }
        String placeholders = String.join(",", java.util.Collections.nCopies(chunkIds.size(), "?"));
        return jdbcTemplate.query("""
                SELECT c.id, c.document_id, d.title, c.text, c.char_start, c.char_end, c.heading_path
                FROM chunk c JOIN document d ON d.id = c.document_id
                WHERE c.id IN (%s) AND d.deleted_at IS NULL
                ORDER BY c.id
                """.formatted(placeholders), this::mapSource, chunkIds.toArray());
    }

    private ChunkSource mapSource(ResultSet resultSet, int rowNumber) throws SQLException {
        return new ChunkSource(
                resultSet.getLong("id"),
                resultSet.getLong("document_id"),
                resultSet.getString("title"),
                resultSet.getString("text"),
                resultSet.getInt("char_start"),
                resultSet.getInt("char_end"),
                resultSet.getString("heading_path"));
    }

    public record ChunkSource(@JsonProperty("chunk_id") long chunkId,
                              @JsonProperty("document_id") long documentId,
                              @JsonProperty("title") String title,
                              @JsonProperty("text") String text,
                              @JsonProperty("byte_start") int byteStart,
                              @JsonProperty("byte_end") int byteEnd,
                              @JsonProperty("heading_path") String headingPath) {}

    private DocumentSummary mapSummary(ResultSet resultSet, int rowNumber) throws SQLException {
        return new DocumentSummary(
                resultSet.getLong("id"),
                resultSet.getString("title"),
                resultSet.getString("source_type"),
                readTags(resultSet.getString("tags")),
                resultSet.getString("index_status"),
                resultSet.getInt("chunk_count"),
                resultSet.getTimestamp("updated_at").toLocalDateTime());
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

    public record NewDocument(String sourceType, String sourceUri, String title,
                              String content, String contentHash, List<String> tags) {
        public NewDocument {
            tags = List.copyOf(tags);
        }
    }

    public record DocumentSummary(@JsonProperty("id") long id,
                                   @JsonProperty("title") String title,
                                   @JsonProperty("source_type") String sourceType,
                                   @JsonProperty("tags") List<String> tags,
                                   @JsonProperty("index_status") String indexStatus,
                                   @JsonProperty("chunk_count") int chunkCount,
                                   @JsonProperty("updated_at") LocalDateTime updatedAt) {
        public DocumentSummary {
            tags = List.copyOf(tags);
        }
    }

    public record DocumentDetail(@JsonProperty("id") long id,
                                 @JsonProperty("title") String title,
                                 @JsonProperty("content") String content,
                                 @JsonProperty("tags") List<String> tags,
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

    /** 对外 byte_start/byte_end 映射自物理列 char_start/char_end（B-13 口径，同 ChunkSource）。 */
    public record ChunkRow(@JsonProperty("id") long chunkId,
                           @JsonProperty("seq") int seq,
                           @JsonProperty("text") String text,
                           @JsonProperty("byte_start") int byteStart,
                           @JsonProperty("byte_end") int byteEnd,
                           @JsonProperty("heading_path") String headingPath,
                           @JsonProperty("token_count") int tokenCount) {}

    public record DocumentPage(@JsonProperty("total") long total,
                               @JsonProperty("items") List<DocumentSummary> items) {
        public DocumentPage {
            items = List.copyOf(items);
        }
    }
}
