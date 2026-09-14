package com.easyrag.server.document;

import com.easyrag.server.rag.RagOperationGate;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClientResponseException;

import java.nio.charset.StandardCharsets;
import java.sql.Timestamp;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** 仅在随机隔离库验证原文/chunks 的真实事务和字节定位，不访问业务资料。 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE)
@ActiveProfiles("local")
@Tag("requires-mysql")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
@DirtiesContext(classMode = DirtiesContext.ClassMode.AFTER_CLASS)
class DocumentManagementRepositoryIT {

    private static final String TEST_DATABASE = "easyrag_management_it_" + UUID.randomUUID().toString().replace("-", "");
    private static final String ORIGINAL = "# 标题\r\n中😀\r\n尾";

    @Autowired
    private JdbcTemplate jdbc;

    @Autowired
    private DocumentManagementRepository repository;

    @Autowired
    private DocumentQueryRepository queries;

    @Autowired
    private DocumentIndexRepository indexing;

    @Autowired
    private DocumentManagementService service;

    @Autowired
    private RagOperationGate gate;

    @Autowired
    @Qualifier("indexingExecutor")
    private ThreadPoolTaskExecutor executor;

    @MockitoBean
    private IndexClient indexClient;

    @MockitoBean
    private ChunkClient chunkClient;

    @MockitoBean
    private EmbedClient embedClient;

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
    void clearOnlyIsolatedFixtures() {
        assertIsolatedDatabase();
        jdbc.execute("DROP TRIGGER IF EXISTS fail_management_chunk_delete");
        jdbc.update("DELETE FROM document");
        gate.tryAcquire(RagOperationGate.Operation.RECOVERY).lease().orElseThrow().confirmCompletion();
    }

    @AfterEach
    void waitForTheSingleExecutorBeforeTheNextFixture() throws Exception {
        executor.submit(() -> {}).get(5, TimeUnit.SECONDS);
    }

    @AfterAll
    void dropOnlyIsolatedSchema() {
        assertIsolatedDatabase();
        jdbc.execute("DROP DATABASE `" + TEST_DATABASE + "`");
        System.out.println("Dropped isolated MySQL schema: " + TEST_DATABASE);
    }

    @Test
    void detailUsesOriginalContentAndLiveChunkCount() {
        long id = createWithChunks("INDEXED");
        jdbc.update("UPDATE document SET chunk_count = 99 WHERE id = ?", id);

        var found = repository.findActive(id);

        assertThat(found).isPresent();
        var document = found.orElseThrow();
        assertThat(document.contentHash()).isEqualTo(DocumentIntakeService.contentHash(ORIGINAL));
        assertThat(document.detail().content()).isEqualTo(ORIGINAL);
        assertThat(document.detail().tags()).containsExactly("中文", "emoji😀");
        assertThat(document.detail().sourceUri()).isEqualTo("原始.md");
        assertThat(document.detail().indexStatus()).isEqualTo("INDEXED");
        assertThat(document.detail().indexError()).isEqualTo("previous failure");
        assertThat(document.detail().chunkCount()).isEqualTo(2);
        assertThat(document.detail().createdAt()).isNotNull();
        assertThat(document.detail().updatedAt()).isEqualTo(Timestamp.valueOf("2026-01-01 00:00:00").toLocalDateTime());
    }

    @Test
    void missingOrSoftDeletedDocumentsHaveNoActiveRecord() {
        long id = createWithChunks("INDEXED");
        jdbc.update("UPDATE document SET deleted_at = CURRENT_TIMESTAMP WHERE id = ?", id);

        assertThat(repository.findActive(id)).isEmpty();
        assertThat(queries.existsActive(id)).isFalse();
        assertThat(repository.findActive(Long.MAX_VALUE)).isEmpty();
        assertThat(queries.existsActive(Long.MAX_VALUE)).isFalse();
    }

    @Test
    void updateCommitsContentMetadataPendingStateAndChunkRemovalTogether() {
        long id = createWithChunks("INDEXED");
        var before = row(id);
        String updated = "# 新标题\n新正文😀";

        repository.replaceContentAndClearChunks(id, updated, DocumentIntakeService.contentHash(updated),
                "新标题", List.of("new"));

        var after = row(id);
        assertThat(after).containsEntry("content", updated)
                .containsEntry("content_hash", DocumentIntakeService.contentHash(updated))
                .containsEntry("title", "新标题").containsEntry("index_status", "PENDING")
                .containsEntry("index_error", null).containsEntry("chunk_count", 0);
        assertThat(repository.findActive(id).orElseThrow().detail().tags()).containsExactly("new");
        assertThat(chunks(id)).isEmpty();
        assertThat(after.get("updated_at")).isNotEqualTo(before.get("updated_at"));
        for (String retained : List.of("source_type", "source_uri", "created_at", "indexed_at")) {
            assertThat(after.get(retained)).isEqualTo(before.get(retained));
        }
    }

    @Test
    void touchingUnchangedContentPreservesExactBytesChunksAndIndexFields() {
        long id = createWithChunks("INDEXED");
        var before = row(id);
        var oldChunks = chunks(id);

        repository.touchUpdatedAt(id);

        var after = row(id);
        assertThat(after.get("updated_at")).isNotEqualTo(before.get("updated_at"));
        before.remove("updated_at");
        after.remove("updated_at");
        assertThat(after).isEqualTo(before);
        assertThat(chunks(id)).isEqualTo(oldChunks);
    }

    @Test
    void failedDocumentCanBeRebuiltUsingTheExistingIndexingRepository() {
        long id = createWithChunks("FAILED");
        var before = row(id);
        var oldIds = chunks(id).stream().map(chunk -> chunk.get("id")).toList();

        repository.clearChunksAndMarkPending(id);

        assertThat(row(id)).containsEntry("index_status", "PENDING").containsEntry("index_error", null)
                .containsEntry("chunk_count", 0).containsEntry("updated_at", before.get("updated_at"));
        assertThat(chunks(id)).isEmpty();
        var pending = indexing.findPending(id).orElseThrow();
        var batch = new ChunkBatch(List.of(new ChunkBatch.Chunk(0, ORIGINAL, 0,
                ORIGINAL.getBytes(StandardCharsets.UTF_8).length, "标题", 8)));
        var rebuilt = indexing.saveChunksAndMarkIndexing(pending, batch);
        indexing.markIndexed(id);

        assertThat(rebuilt).hasSize(1);
        assertThat(oldIds).doesNotContain(rebuilt.get(0).chunkId());
        assertThat(repository.findActive(id).orElseThrow().detail().indexStatus()).isEqualTo("INDEXED");
        assertThat(row(id)).containsEntry("content", ORIGINAL).containsEntry("chunk_count", 1);
    }

    @Test
    void deletingOneDocumentPreservesItsOriginalAndLeavesNeighborsUntouched() {
        long id = createWithChunks("INDEXED");
        long neighbor = createWithChunks("INDEXED");
        var neighborRow = row(neighbor);
        var neighborChunks = chunks(neighbor);

        repository.softDeleteAndClearChunks(id);

        assertThat(row(id).get("deleted_at")).isNotNull();
        assertThat(row(id)).containsEntry("content", ORIGINAL).containsEntry("chunk_count", 0);
        assertThat(chunks(id)).isEmpty();
        assertThat(repository.findActive(id)).isEmpty();
        assertThat(queries.findChunks(id)).isEmpty();
        assertThat(queries.findPage(null, 0, 20).items())
                .extracting(DocumentQueryRepository.DocumentSummary::id).containsExactly(neighbor);
        assertThat(row(neighbor)).isEqualTo(neighborRow);
        assertThat(chunks(neighbor)).isEqualTo(neighborChunks);
    }

    @ParameterizedTest
    @ValueSource(strings = {"UPDATE", "REINDEX", "DELETE"})
    void chunkDeleteFailureRollsBackEveryPartOfTheDocumentMutation(String operation) {
        long id = createWithChunks("INDEXED");
        var before = row(id);
        var beforeChunks = chunks(id);
        jdbc.execute("""
                CREATE TRIGGER fail_management_chunk_delete BEFORE DELETE ON chunk FOR EACH ROW
                SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'forced chunk delete failure'
                """);

        assertThatThrownBy(() -> {
            switch (operation) {
                case "UPDATE" -> repository.replaceContentAndClearChunks(id, "新正文", "b".repeat(64), "新标题", List.of());
                case "REINDEX" -> repository.clearChunksAndMarkPending(id);
                default -> repository.softDeleteAndClearChunks(id);
            }
        }).isInstanceOf(DataAccessException.class);

        assertThat(row(id)).isEqualTo(before);
        assertThat(chunks(id)).isEqualTo(beforeChunks);
    }

    @ParameterizedTest
    @ValueSource(strings = {"MISSING", "DELETED", "INDEXING"})
    void mutationRefusesMissingDeletedOrUnfinishedDocuments(String state) {
        long id = state.equals("MISSING") ? Long.MAX_VALUE : createWithChunks(state.equals("INDEXING") ? state : "INDEXED");
        if (state.equals("DELETED")) {
            jdbc.update("UPDATE document SET deleted_at = CURRENT_TIMESTAMP WHERE id = ?", id);
        }
        var rows = jdbc.queryForList("SELECT * FROM document ORDER BY id");
        var storedChunks = chunks(id);

        assertThatThrownBy(() -> repository.touchUpdatedAt(id)).isInstanceOf(IllegalStateException.class);
        assertThatThrownBy(() -> repository.clearChunksAndMarkPending(id)).isInstanceOf(IllegalStateException.class);
        assertThatThrownBy(() -> repository.softDeleteAndClearChunks(id)).isInstanceOf(IllegalStateException.class);
        assertThatThrownBy(() -> repository.replaceContentAndClearChunks(id, "新正文", "a".repeat(64), "标题", List.of()))
                .isInstanceOf(IllegalStateException.class);

        assertThat(jdbc.queryForList("SELECT * FROM document ORDER BY id")).isEqualTo(rows);
        assertThat(chunks(id)).isEqualTo(storedChunks);
    }

    @Test
    void titleSearchIsLiteralAndCombinesWithStatusAndPagination() {
        long first = queries.insertPending(new DocumentQueryRepository.NewDocument(
                "UPLOAD", "first.md", "SQL 100%_说明", ORIGINAL, "a".repeat(64), List.of()));
        long second = queries.insertPending(new DocumentQueryRepository.NewDocument(
                "UPLOAD", "second.md", "SQL 100%_补充", ORIGINAL, "b".repeat(64), List.of()));
        queries.insertPending(new DocumentQueryRepository.NewDocument(
                "UPLOAD", "other.md", "SQL 100 percent", ORIGINAL, "c".repeat(64), List.of()));

        var page = queries.findPage("PENDING", "%_", 0, 1);
        var next = queries.findPage("PENDING", "%_", 1, 1);

        assertThat(page).isNotNull();
        assertThat(page.total()).isEqualTo(2);
        assertThat(page.items()).extracting(DocumentQueryRepository.DocumentSummary::id).containsExactly(second);
        assertThat(next.items()).extracting(DocumentQueryRepository.DocumentSummary::id).containsExactly(first);
        assertThat(queries.findPage("FAILED", "%_", 0, 20).total()).isZero();
        jdbc.update("UPDATE document SET deleted_at = CURRENT_TIMESTAMP WHERE id = ?", second);
        assertThat(queries.findPage(null, "%_", 0, 20).items())
                .extracting(DocumentQueryRepository.DocumentSummary::id).containsExactly(first);
    }

    @Test
    void updateCommitsBeforeTheRealAsyncWorkerReadsItAndNeverOpensAQueryWindow() throws Exception {
        long id = createWithChunks("INDEXED");
        var oldIds = chunks(id).stream().map(chunk -> ((Number) chunk.get("id")).longValue()).toList();
        String updated = "# 新标题\n新内容😀";
        var entered = new CountDownLatch(1);
        var release = new CountDownLatch(1);
        when(chunkClient.chunk(id, updated, "新标题")).thenAnswer(invocation -> {
            entered.countDown();
            assertThat(release.await(5, TimeUnit.SECONDS)).isTrue();
            return wholeDocumentBatch(updated);
        });
        when(embedClient.embed(eq(id), anyList(), anyList())).thenReturn(1);

        try {
            assertThat(service.update(id, updated).reindexed()).isTrue();
            assertThat(entered.await(5, TimeUnit.SECONDS)).isTrue();
            assertThat(repository.findActive(id).orElseThrow().detail().content()).isEqualTo(updated);
            assertThat(queries.findChunks(id)).isEmpty();
            assertThat(queries.findSources(oldIds)).isEmpty();
            assertThat(gate.state()).isEqualTo(RagOperationGate.State.MUTATING);
            assertThat(gate.tryAcquire(RagOperationGate.Operation.QUERY).lease()).isEmpty();
            verify(indexClient).deleteDocument(id);
        } finally {
            release.countDown();
            executor.submit(() -> {}).get(5, TimeUnit.SECONDS);
        }

        assertThat(queries.findDetail(id).orElseThrow().indexStatus()).isEqualTo("INDEXED");
        assertThat(queries.findChunks(id)).extracting(DocumentQueryRepository.ChunkRow::text)
                .containsExactly(updated);
        assertThat(queries.findSources(oldIds)).isEmpty();
        assertThat(gate.state()).isEqualTo(RagOperationGate.State.READY);
    }

    @Test
    void failedAsyncIndexCanBeRetriedAndThenSoftDeletedThroughTheRealWorkflow() throws Exception {
        long id = createWithChunks("FAILED");
        when(chunkClient.chunk(id, ORIGINAL, "标题")).thenReturn(wholeDocumentBatch(ORIGINAL));
        var headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        when(embedClient.embed(eq(id), anyList(), anyList())).thenThrow(new RestClientResponseException(
                "model failed", 503, "Unavailable", headers,
                "{\"detail\":{\"error\":\"EMBEDDING_UNAVAILABLE\",\"cause\":\"RuntimeError\"}}"
                        .getBytes(StandardCharsets.UTF_8), StandardCharsets.UTF_8));

        service.reindex(id);
        executor.submit(() -> {}).get(5, TimeUnit.SECONDS);

        assertThat(queries.findDetail(id).orElseThrow().indexStatus()).isEqualTo("FAILED");
        assertThat(queries.findDetail(id).orElseThrow().indexError()).contains("EMBEDDING_UNAVAILABLE", "cleanup=OK");
        assertThat(gate.state()).isEqualTo(RagOperationGate.State.READY);
        org.mockito.Mockito.doReturn(1).when(embedClient).embed(eq(id), anyList(), anyList());

        service.reindex(id);
        executor.submit(() -> {}).get(5, TimeUnit.SECONDS);

        assertThat(queries.findDetail(id).orElseThrow().indexStatus()).isEqualTo("INDEXED");
        assertThat(queries.findDetail(id).orElseThrow().indexError()).isNull();
        service.delete(id);
        assertThat(row(id).get("deleted_at")).isNotNull();
        assertThat(chunks(id)).isEmpty();
        assertThat(queries.findDetail(id)).isEmpty();
        assertThatThrownBy(() -> service.reindex(id)).isInstanceOf(DocumentManagementService.NotFound.class);
        assertThat(gate.state()).isEqualTo(RagOperationGate.State.READY);
    }

    @Test
    void uncertainAsyncEmbedKeepsTheSavedContentButBlocksQueriesAndRetries() throws Exception {
        long id = createWithChunks("INDEXED");
        String updated = "# 新标题\n新内容";
        when(chunkClient.chunk(id, updated, "新标题")).thenReturn(wholeDocumentBatch(updated));
        when(embedClient.embed(eq(id), anyList(), anyList())).thenThrow(new ResourceAccessException("timeout"));

        service.update(id, updated);
        executor.submit(() -> {}).get(5, TimeUnit.SECONDS);

        assertThat(queries.findDetail(id).orElseThrow().content()).isEqualTo(updated);
        assertThat(queries.findDetail(id).orElseThrow().indexStatus()).isEqualTo("FAILED");
        assertThat(gate.state()).isEqualTo(RagOperationGate.State.RECOVERY_REQUIRED);
        assertThat(gate.tryAcquire(RagOperationGate.Operation.QUERY).lease()).isEmpty();
        assertThatThrownBy(() -> service.reindex(id)).isInstanceOf(DocumentManagementService.Busy.class);
    }

    private static ChunkBatch wholeDocumentBatch(String content) {
        return new ChunkBatch(List.of(new ChunkBatch.Chunk(0, content, 0,
                content.getBytes(StandardCharsets.UTF_8).length, "标题", 8)));
    }

    private long createWithChunks(String status) {
        long id = queries.insertPending(new DocumentQueryRepository.NewDocument("UPLOAD", "原始.md", "标题",
                ORIGINAL, DocumentIntakeService.contentHash(ORIGINAL), List.of("中文", "emoji😀")));
        jdbc.update("""
                UPDATE document SET index_status = ?, index_error = 'previous failure', chunk_count = 2,
                                    updated_at = '2026-01-01 00:00:00', indexed_at = '2026-01-02 00:00:00'
                WHERE id = ?
                """, status, id);
        int bodyStart = "# 标题\r\n".getBytes(StandardCharsets.UTF_8).length;
        int tailStart = ORIGINAL.getBytes(StandardCharsets.UTF_8).length - 3;
        jdbc.update("""
                INSERT INTO chunk (document_id, seq, text, char_start, char_end, heading_path, token_count)
                VALUES (?, 1, '尾', ?, ?, NULL, 1), (?, 0, '中😀\r\n', ?, ?, '标题', 4)
                """, id, tailStart, tailStart + 3, id, bodyStart, tailStart);
        return id;
    }

    private Map<String, Object> row(long id) {
        return jdbc.queryForMap("SELECT * FROM document WHERE id = ?", id);
    }

    private List<Map<String, Object>> chunks(long id) {
        return jdbc.queryForList("SELECT * FROM chunk WHERE document_id = ? ORDER BY seq", id);
    }

    private void assertIsolatedDatabase() {
        assertThat(TEST_DATABASE).matches("easyrag_management_it_[0-9a-f]{32}");
        assertThat(jdbc.queryForObject("SELECT DATABASE()", String.class)).isEqualTo(TEST_DATABASE);
    }
}
