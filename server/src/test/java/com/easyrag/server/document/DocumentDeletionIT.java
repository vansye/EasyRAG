package com.easyrag.server.document;

import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;
import java.util.Objects;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.BDDMockito.given;
import static org.mockito.BDDMockito.willThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 删除链路的集成测试：真 MySQL + 真 HTTP（MockMvc）+ 真闸门，Python 客户端被
 * mock——向量侧的正确性由 Python 自己的测试与三进程 e2e 保证，这里验收的是
 * U2 在 MySQL 层的语义：删掉后列表不见、切片消失、问答出处不再返回它。
 *
 * 守住的行为（方案 A）：清索引失败时 DB 完全不动；成功时软删 + 硬删同事务。
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("local")
@Tag("requires-mysql")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
@DirtiesContext(classMode = DirtiesContext.ClassMode.AFTER_CLASS)
class DocumentDeletionIT {

    private static final String TEST_DATABASE = "easyrag_deletion_it_" + UUID.randomUUID().toString().replace("-", "");

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private JdbcTemplate jdbcTemplate;

    @Autowired
    private DocumentQueryRepository documents;

    @Autowired
    private ReadinessService readiness;

    @Autowired
    private com.easyrag.server.rag.RagOperationGate gate;

    @MockitoBean
    private IndexClient indexClient;

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
        // 闸门初态 RECOVERY_REQUIRED：不确认就绪，删除会一律 503（这本身也是一条验收）
        if (gate.state() != com.easyrag.server.rag.RagOperationGate.State.READY) {
            readiness.ready();
        }
    }

    @AfterAll
    void dropOnlyIsolatedTestDatabase() {
        assertIsolatedDatabase();
        jdbcTemplate.execute("DROP DATABASE `" + TEST_DATABASE + "`");
        System.out.println("Dropped isolated MySQL schema: " + TEST_DATABASE);
    }

    @Test
    void deleteRemovesDocumentAndChunksFromAllReadPaths() throws Exception {
        long id = insertIndexedDocument("待删除.md");
        long chunkId = insertChunk(id, 0);
        long neighborId = insertIndexedDocument("邻居.md");
        long neighborChunkId = insertChunk(neighborId, 0);
        given(indexClient.deleteDocument(id)).willReturn(3);

        mockMvc.perform(delete("/api/documents/{id}", id)).andExpect(status().isNoContent());

        verify(indexClient).deleteDocument(id);
        assertThat(jdbcTemplate.queryForObject(
                "SELECT deleted_at IS NULL FROM document WHERE id = ?", Boolean.class, id)).isFalse();
        assertThat(jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM chunk WHERE document_id = ?", Integer.class, id)).isZero();
        // U2 的读侧验收：出处不再返回已删文档的切片，邻居不受影响
        assertThat(documents.findSources(List.of(chunkId))).isEmpty();
        assertThat(documents.findSources(List.of(neighborChunkId))).hasSize(1);
        assertThat(documents.findDetail(id)).isEmpty();
        mockMvc.perform(get("/api/documents/{id}/chunks", id)).andExpect(status().isNotFound());
    }

    @Test
    void deleteIsIdempotentFromTheCallerSide() throws Exception {
        long id = insertIndexedDocument("已删.md");
        jdbcTemplate.update("UPDATE document SET deleted_at = CURRENT_TIMESTAMP WHERE id = ?", id);

        mockMvc.perform(delete("/api/documents/{id}", id)).andExpect(status().isNotFound());

        // 已删文档再删：404，不碰 Python（existsActive 先挡住），切片也无残留
        verify(indexClient, never()).deleteDocument(anyLong());
        assertThat(jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM chunk WHERE document_id = ?", Integer.class, id)).isZero();
    }

    @Test
    void indexCleanupFailureLeavesDatabaseUntouchedAndGateRequiresRecovery() throws Exception {
        long id = insertIndexedDocument("清索引失败.md");
        insertChunk(id, 0);
        willThrow(new org.springframework.web.client.RestClientException("python down"))
                .given(indexClient).deleteDocument(anyLong());

        mockMvc.perform(delete("/api/documents/{id}", id)).andExpect(status().isBadGateway());

        // 方案 A 的核心承诺：DB 完全不动
        assertThat(jdbcTemplate.queryForObject(
                "SELECT deleted_at IS NULL FROM document WHERE id = ?", Boolean.class, id)).isTrue();
        assertThat(jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM chunk WHERE document_id = ?", Integer.class, id)).isEqualTo(1);
        // 结果不明 → 闸门退回 RECOVERY_REQUIRED；恢复确认后重试可成功。
        // 再打桩必须用 willReturn(...).given(...) 桩式：given(mock.call()) 式在
        // 重新声明时会先派发旧桩（anyLong 的 willThrow 当场炸出）。
        mockMvc.perform(delete("/api/documents/{id}", id)).andExpect(status().isServiceUnavailable());
        readiness.ready();
        org.mockito.BDDMockito.willReturn(1).given(indexClient).deleteDocument(id);
        mockMvc.perform(delete("/api/documents/{id}", id)).andExpect(status().isNoContent());
    }

    private long insertIndexedDocument(String filename) {
        GeneratedKeyHolder keys = new GeneratedKeyHolder();
        jdbcTemplate.update(connection -> {
            var statement = connection.prepareStatement("""
                    INSERT INTO document (source_type, source_uri, title, content, content_hash, tags, index_status)
                    VALUES ('UPLOAD', ?, ?, '正文', ?, '[]', 'INDEXED')
                    """, java.sql.Statement.RETURN_GENERATED_KEYS);
            statement.setString(1, filename);
            statement.setString(2, filename.replace(".md", ""));
            statement.setString(3, "a".repeat(64));
            return statement;
        }, keys);
        return Objects.requireNonNull(keys.getKey(), "generated id is missing").longValue();
    }

    private long insertChunk(long documentId, int seq) {
        GeneratedKeyHolder keys = new GeneratedKeyHolder();
        jdbcTemplate.update(connection -> {
            var statement = connection.prepareStatement("""
                    INSERT INTO chunk (document_id, seq, text, char_start, char_end, heading_path, token_count)
                    VALUES (?, ?, '片段', 0, 6, '', 2)
                    """, java.sql.Statement.RETURN_GENERATED_KEYS);
            statement.setLong(1, documentId);
            statement.setInt(2, seq);
            return statement;
        }, keys);
        return Objects.requireNonNull(keys.getKey(), "generated chunk id is missing").longValue();
    }

    private void assertIsolatedDatabase() {
        assertThat(TEST_DATABASE).matches("easyrag_deletion_it_[0-9a-f]{32}");
        assertThat(jdbcTemplate.queryForObject("SELECT DATABASE()", String.class)).isEqualTo(TEST_DATABASE);
    }
}
