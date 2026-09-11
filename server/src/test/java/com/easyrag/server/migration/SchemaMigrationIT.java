package com.easyrag.server.migration;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Flyway 迁移的验证：V1__init.sql 真的在 MySQL 上建出了 document 与 chunk。
 *
 * <p>为什么要单独测：/health 只跑 {@code SELECT 1}，连得上库就报 UP——
 * 它证明不了表存在。库自动建了但迁移没执行，是一个 /health 看不出来的失败。</p>
 *
 * <p>本测试需要本机 MySQL 与 {@code config/application-local.yaml} 中的凭据。
 * 它由 Failsafe 在 {@code mvn verify} 时执行（类名以 IT 结尾），并带
 * {@code requires-mysql} 标签——Surefire 在 pom 里配了 {@code excludedGroups}，
 * 所以无库环境的 {@code mvn test} 不会碰它。</p>
 */
@SpringBootTest
@ActiveProfiles("local")
@org.junit.jupiter.api.Tag("requires-mysql")
class SchemaMigrationIT {

    @Autowired
    private JdbcTemplate jdbcTemplate;

    @Test
    @DisplayName("迁移已执行：flyway_schema_history 记录 V1 且标记成功")
    void migrationV1IsRecordedAsSuccessful() {
        List<Map<String, Object>> rows = jdbcTemplate.queryForList(
                "SELECT version, description, success FROM flyway_schema_history ORDER BY installed_rank");

        assertThat(rows).isNotEmpty();
        assertThat(rows).anySatisfy(row -> {
            assertThat(row.get("version")).isEqualTo("1");
            assertThat(row.get("success")).isEqualTo(true);
        });
    }

    @Test
    @DisplayName("document 表存在，且含索引状态机与软删所需的列")
    void documentTableHasExpectedColumns() {
        List<String> columns = columnNamesOf("document");

        assertThat(columns).contains(
                "id", "source_type", "source_uri", "title", "content", "content_hash",
                "tags", "index_status", "index_error", "chunk_count",
                "created_at", "updated_at", "indexed_at", "deleted_at");
    }

    @Test
    @DisplayName("chunk 表存在，且含溯源所需的偏移量与标题路径")
    void chunkTableHasTraceabilityColumns() {
        List<String> columns = columnNamesOf("chunk");

        // char_start/char_end 是溯源落点，heading_path 是层级信号：
        // 少任何一个，U4「答案旁有可点开的出处」就无法实现。
        assertThat(columns).contains(
                "id", "document_id", "seq", "text",
                "char_start", "char_end", "heading_path", "token_count", "created_at");
    }

    @Test
    @DisplayName("index_status 是四态枚举，与状态机定义一致")
    void indexStatusIsFourStateEnum() {
        String columnType = jdbcTemplate.queryForObject(
                "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
                        + "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'document' "
                        + "AND COLUMN_NAME = 'index_status'",
                String.class);

        assertThat(columnType)
                .contains("PENDING").contains("INDEXING").contains("INDEXED").contains("FAILED");
    }

    @Test
    @DisplayName("V2：updated_at 不带 ON UPDATE，否则状态机每次写库都会改它")
    void updatedAtIsNotAutoUpdated() {
        // information_schema 用 EXTRA 记录 "on update CURRENT_TIMESTAMP"
        String extra = jdbcTemplate.queryForObject(
                "SELECT EXTRA FROM information_schema.COLUMNS "
                        + "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'document' "
                        + "AND COLUMN_NAME = 'updated_at'",
                String.class);

        // 带 ON UPDATE 会让本列变成"行最后被写时间"，而语义要的是"内容更新时间"：
        // 索引状态机每轮至少写三次这一行，A-2 的重启重置更会把一批文档的时间
        // 集体推到开机时刻——用户什么都没改，列表却重排了。
        assertThat(extra).doesNotContain("on update");
    }

    @Test
    @DisplayName("V2：(document_id, seq) 是唯一约束，重索引半途失败时当场炸而非静默重复")
    void documentSeqIsUnique() {
        Integer nonUnique = jdbcTemplate.queryForObject(
                "SELECT NON_UNIQUE FROM information_schema.STATISTICS "
                        + "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chunk' "
                        + "AND COLUMN_NAME = 'document_id' AND SEQ_IN_INDEX = 1 "
                        + "AND INDEX_NAME = 'uk_document_seq'",
                Integer.class);

        assertThat(nonUnique).isZero();   // 0 = UNIQUE
    }

    @Test
    @DisplayName("迁移历史里 V1 与 V2 都成功")
    void bothMigrationsRecorded() {
        List<Map<String, Object>> rows = jdbcTemplate.queryForList(
                "SELECT version, success FROM flyway_schema_history "
                        + "WHERE version IS NOT NULL ORDER BY installed_rank");

        assertThat(rows).hasSizeGreaterThanOrEqualTo(2);
        assertThat(rows).allSatisfy(row -> assertThat(row.get("success")).isEqualTo(true));
        assertThat(rows).anySatisfy(row -> assertThat(row.get("version")).isEqualTo("2"));
    }

    private List<String> columnNamesOf(String table) {
        return jdbcTemplate.queryForList(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                        + "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ?",
                String.class, table);
    }
}
