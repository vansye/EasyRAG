package com.easyrag.server.document;

import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

import java.util.Objects;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * 更新与重索引的 SQL 行为测试：真实 MySQL、随机隔离库。
 *
 * 索引触发器被 mock——异步索引的正确性由 DocumentIndexingIT（三进程真链路）
 * 保证，这里验收的是 U3 在 MySQL 层的语义：哈希变化写新正文并回到 PENDING、
 * 哈希不变不动正文与 chunks、reindex 的状态门槛。
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE)
@ActiveProfiles("local")
@Tag("requires-mysql")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
@DirtiesContext(classMode = DirtiesContext.ClassMode.AFTER_CLASS)
class DocumentUpdateIT {

    private static final String TEST_DATABASE = "easyrag_update_it_" + UUID.randomUUID().toString().replace("-", "");
    private static final String ORIGINAL = "# 原标题\n原正文内容";

    @Autowired
    private JdbcTemplate jdbcTemplate;

    @Autowired
    private DocumentUpdateService service;

    @Autowired
    private DocumentQueryRepository documents;

    @Autowired
    private com.easyrag.server.rag.RagOperationGate gate;

    @MockitoBean
    private IndexingTrigger indexingTrigger;

    @DynamicPropertySource
    static void isolatedDatabase(DynamicPropertyRegistry registry) {
        String url = "jdbc:mysql://${MYSQL_HOST:localhost}:${MYSQL_PORT:3306}/" + TEST_DATABASE
                + "?createDatabaseIfNotExist=true&useUnicode=true&characterEncoding=UTF-8"
                + "&serverTimezone=Asia/Shanghai&useSSL=false&allowPublicKeyRetrieval=true";
        registry.add("spring.datasource.url", () -> url);
        registry.add("spring.flyway.url", () -> url);
        registry.add("spring.flyway.user", () -> "${spring.datasource.username}");
        registry.add("spring.flyway.password", () -> "${spring.datasource.password}");
        registry.add("spring.flyway.enabled", () -> true);
    }

    @BeforeEach
    void clearAndMakeReady() {
        assertIsolatedDatabase();
        jdbcTemplate.update("DELETE FROM document");
        if (gate.state() != com.easyrag.server.rag.RagOperationGate.State.READY) {
            gate.tryAcquire(com.easyrag.server.rag.RagOperationGate.Operation.RECOVERY)
                    .lease().orElseThrow().confirmCompletion();
        }
    }

    @AfterAll
    void dropOnlyIsolatedTestDatabase() {
        assertIsolatedDatabase();
        jdbcTemplate.execute("DROP DATABASE `" + TEST_DATABASE + "`");
        System.out.println("Dropped isolated MySQL schema: " + TEST_DATABASE);
    }

    @Test
    void changedContentRewritesDocumentAndReturnsToPending() {
        long id = insertIndexedDocument("笔记.md", ORIGINAL);
        insertChunk(id, 0);

        var result = service.updateFromText(id, "# 新标题\n完全不同的新正文");

        assertThat(result.reindexed()).isTrue();
        assertThat(result.indexStatus()).isEqualTo("PENDING");
        var stored = documents.findDetail(id).orElseThrow();
        assertThat(stored.content()).isEqualTo("# 新标题\n完全不同的新正文");
        assertThat(stored.title()).isEqualTo("新标题");
        assertThat(stored.indexStatus()).isEqualTo("PENDING");
        assertThat(stored.indexError()).isNull();
        // 旧 chunk 保留到重索引原子替换为止（否则旧向量命中时出处为空）
        assertThat(stored.chunkCount()).isEqualTo(1);
    }

    @Test
    void unchangedHashKeepsContentAndChunksExactlyAsStored() {
        long id = insertIndexedDocument("笔记.md", ORIGINAL);
        long chunkId = insertChunk(id, 0);

        // 仅换行符不同：规范化后哈希相同，属"内容等价"
        var result = service.updateFromText(id, ORIGINAL.replace("\n", "\r\n"));

        assertThat(result.reindexed()).isFalse();
        assertThat(result.indexStatus()).isEqualTo("INDEXED");
        var stored = documents.findDetail(id).orElseThrow();
        // §一.2 的硬要求：正文原封不动——哈希相同不代表字节位置相同，
        // 悄悄替换会让已落库 chunk 的偏移全部错位
        assertThat(stored.content()).isEqualTo(ORIGINAL);
        assertThat(stored.indexStatus()).isEqualTo("INDEXED");
        assertThat(documents.findChunks(id)).singleElement()
                .satisfies(chunk -> assertThat(chunk.chunkId()).isEqualTo(chunkId));
    }

    @Test
    void reindexAllowsFailedAndIndexedButRejectsInFlight() {
        long failed = insertIndexedDocument("失败.md", ORIGINAL);
        jdbcTemplate.update(
                "UPDATE document SET index_status = 'FAILED', index_error = 'stage=EMBED' WHERE id = ?", failed);
        long indexed = insertIndexedDocument("已索引.md", ORIGINAL);
        long pending = insertIndexedDocument("排队中.md", ORIGINAL);
        jdbcTemplate.update("UPDATE document SET index_status = 'PENDING' WHERE id = ?", pending);

        assertThat(service.reindex(failed).indexStatus()).isEqualTo("PENDING");
        assertThat(service.reindex(indexed).indexStatus()).isEqualTo("PENDING");
        // FAILED 重排队时清掉 index_error，否则旧错误会挂在新一轮上
        assertThat(documents.findDetail(failed).orElseThrow().indexError()).isNull();

        assertThatThrownBy(() -> service.reindex(pending))
                .isInstanceOf(DocumentUpdateService.InFlight.class);
        assertThatThrownBy(() -> service.reindex(999999L))
                .isInstanceOf(DocumentUpdateService.NotFound.class);
    }

    @Test
    void softDeletedDocumentIsNeitherUpdatableNorReindexable() {
        long id = insertIndexedDocument("已删.md", ORIGINAL);
        jdbcTemplate.update("UPDATE document SET deleted_at = CURRENT_TIMESTAMP WHERE id = ?", id);

        assertThatThrownBy(() -> service.updateFromText(id, "新正文"))
                .isInstanceOf(DocumentUpdateService.NotFound.class);
        assertThatThrownBy(() -> service.reindex(id))
                .isInstanceOf(DocumentUpdateService.NotFound.class);
    }

    private long insertIndexedDocument(String filename, String content) {
        GeneratedKeyHolder keys = new GeneratedKeyHolder();
        jdbcTemplate.update(connection -> {
            var statement = connection.prepareStatement("""
                    INSERT INTO document (source_type, source_uri, title, content, content_hash, tags,
                                          index_status, indexed_at)
                    VALUES ('UPLOAD', ?, ?, ?, ?, '[]', 'INDEXED', CURRENT_TIMESTAMP)
                    """, java.sql.Statement.RETURN_GENERATED_KEYS);
            statement.setString(1, filename);
            statement.setString(2, "原标题");
            statement.setString(3, content);
            statement.setString(4, DocumentContent.fromText(content, "原标题").contentHash());
            return statement;
        }, keys);
        return Objects.requireNonNull(keys.getKey(), "generated id is missing").longValue();
    }

    private long insertChunk(long documentId, int seq) {
        GeneratedKeyHolder keys = new GeneratedKeyHolder();
        jdbcTemplate.update(connection -> {
            var statement = connection.prepareStatement("""
                    INSERT INTO chunk (document_id, seq, text, char_start, char_end, heading_path, token_count)
                    VALUES (?, ?, '原正文内容', 0, 12, '', 4)
                    """, java.sql.Statement.RETURN_GENERATED_KEYS);
            statement.setLong(1, documentId);
            statement.setInt(2, seq);
            return statement;
        }, keys);
        return Objects.requireNonNull(keys.getKey(), "generated chunk id is missing").longValue();
    }

    private void assertIsolatedDatabase() {
        assertThat(TEST_DATABASE).matches("easyrag_update_it_[0-9a-f]{32}");
        assertThat(jdbcTemplate.queryForObject("SELECT DATABASE()", String.class)).isEqualTo(TEST_DATABASE);
    }
}
