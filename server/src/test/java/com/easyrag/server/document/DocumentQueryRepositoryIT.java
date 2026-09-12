package com.easyrag.server.document;

import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;

import java.util.List;
import java.util.Objects;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * DocumentQueryRepository 的 SQL 行为测试：真实 MySQL、随机隔离库，
 * 不触碰业务库。需要本地凭据（requires-mysql），CI 不跑。
 *
 * 重点守住：chunk_count 是实时 COUNT（A1-3——列是缓存，COUNT 是事实）、
 * 状态过滤、分页、软删除排除。
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE)
@ActiveProfiles("local")
@Tag("requires-mysql")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
@DirtiesContext(classMode = DirtiesContext.ClassMode.AFTER_CLASS)
class DocumentQueryRepositoryIT {

    private static final String TEST_DATABASE = "easyrag_query_it_" + UUID.randomUUID().toString().replace("-", "");

    @Autowired
    private JdbcTemplate jdbcTemplate;

    @Autowired
    private DocumentQueryRepository repository;

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
    void clearOnlyIsolatedTestData() {
        assertIsolatedDatabase();
        jdbcTemplate.update("DELETE FROM document");
    }

    @AfterAll
    void dropOnlyIsolatedTestDatabase() {
        assertIsolatedDatabase();
        jdbcTemplate.execute("DROP DATABASE `" + TEST_DATABASE + "`");
        System.out.println("Dropped isolated MySQL schema: " + TEST_DATABASE);
    }

    @Test
    void insertPendingStoresRowReadableThroughFindPage() {
        long id = repository.insertPending(new DocumentQueryRepository.NewDocument(
                "UPLOAD", "note.md", "KV Cache", "# KV Cache\n正文",
                "a".repeat(64), List.of("redis", "缓存")));
        assertThat(id).isPositive();

        var page = repository.findPage(null, 0, 20);
        assertThat(page.total()).isEqualTo(1);
        DocumentQueryRepository.DocumentSummary summary = page.items().get(0);
        assertThat(summary.id()).isEqualTo(id);
        assertThat(summary.title()).isEqualTo("KV Cache");
        assertThat(summary.sourceType()).isEqualTo("UPLOAD");
        assertThat(summary.indexStatus()).isEqualTo("PENDING");
        assertThat(summary.tags()).containsExactly("redis", "缓存");
        assertThat(summary.updatedAt()).isNotNull();
    }

    @Test
    void findPageCountsChunksLiveRatherThanTrustingTheColumn() {
        long withChunks = insertDocument("含切片.md", "PENDING");
        long without = insertDocument("无切片.md", "PENDING");
        jdbcTemplate.update("""
                INSERT INTO chunk (document_id, seq, text, char_start, char_end, heading_path, token_count)
                VALUES (?, 0, '片段一', 0, 9, '', 3), (?, 1, '片段二', 9, 18, '', 3), (?, 2, '片段三', 18, 27, '', 3)
                """, withChunks, withChunks, withChunks);
        // 列只在一处维护，这里刻意不更新它：实时 COUNT 必须与列脱钩（A1-3）
        assertThat(jdbcTemplate.queryForObject(
                "SELECT chunk_count FROM document WHERE id = ?", Integer.class, withChunks)).isZero();

        var page = repository.findPage(null, 0, 20);

        assertThat(page.total()).isEqualTo(2);
        var summary = page.items().stream()
                .filter(item -> item.id() == withChunks).findFirst().orElseThrow();
        var other = page.items().stream()
                .filter(item -> item.id() == without).findFirst().orElseThrow();
        assertThat(summary.chunkCount()).isEqualTo(3);
        assertThat(other.chunkCount()).isZero();
    }

    @Test
    void findPageFiltersByStatusAndExcludesDeleted() {
        long pending = insertDocument("待索引.md", "PENDING");
        long indexed = insertDocument("已索引.md", "INDEXED");
        jdbcTemplate.update("UPDATE document SET deleted_at = CURRENT_TIMESTAMP WHERE id = ?", indexed);

        var page = repository.findPage("PENDING", 0, 20);

        assertThat(page.total()).isEqualTo(1);
        assertThat(page.items()).hasSize(1);
        assertThat(page.items().get(0).id()).isEqualTo(pending);
        assertThat(page.items().get(0).indexStatus()).isEqualTo("PENDING");
    }

    @Test
    void findPagePaginatesWithStableOrdering() {
        for (int i = 1; i <= 3; i++) {
            long id = insertDocument("第" + i + "篇.md", "PENDING");
            // updated_at 秒级精度，显式区分以保证排序确定
            jdbcTemplate.update("UPDATE document SET updated_at = ? WHERE id = ?",
                    java.sql.Timestamp.valueOf("2026-01-01 00:00:0" + i), id);
        }

        var firstPage = repository.findPage(null, 0, 2);
        var secondPage = repository.findPage(null, 1, 2);

        assertThat(firstPage.total()).isEqualTo(3);
        assertThat(firstPage.items()).extracting(DocumentQueryRepository.DocumentSummary::title)
                .containsExactly("第3篇", "第2篇");
        assertThat(secondPage.items()).extracting(DocumentQueryRepository.DocumentSummary::title)
                .containsExactly("第1篇");
    }

    private long insertDocument(String filename, String status) {
        GeneratedKeyHolder keys = new GeneratedKeyHolder();
        jdbcTemplate.update(connection -> {
            var statement = connection.prepareStatement("""
                    INSERT INTO document (source_type, source_uri, title, content, content_hash, tags, index_status)
                    VALUES ('UPLOAD', ?, ?, '正文', ?, '[]', ?)
                    """, java.sql.Statement.RETURN_GENERATED_KEYS);
            statement.setString(1, filename);
            statement.setString(2, filename.replace(".md", ""));
            statement.setString(3, "a".repeat(64));
            statement.setString(4, status);
            return statement;
        }, keys);
        return Objects.requireNonNull(keys.getKey(), "generated id is missing").longValue();
    }

    private void assertIsolatedDatabase() {
        assertThat(TEST_DATABASE).matches("easyrag_query_it_[0-9a-f]{32}");
        assertThat(jdbcTemplate.queryForObject("SELECT DATABASE()", String.class)).isEqualTo(TEST_DATABASE);
    }
}
