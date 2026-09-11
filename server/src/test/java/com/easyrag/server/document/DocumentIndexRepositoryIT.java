package com.easyrag.server.document;

import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.NullSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

import java.nio.charset.StandardCharsets;
import java.sql.Statement;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.junit.jupiter.api.Assertions.assertAll;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE)
@ActiveProfiles("local")
@Tag("requires-mysql")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
@DirtiesContext(classMode = DirtiesContext.ClassMode.AFTER_CLASS)
class DocumentIndexRepositoryIT {

    private static final String TEST_DATABASE = "easyrag_index_it_" + UUID.randomUUID().toString().replace("-", "");
    private static final String SOURCE = "中😀\r\n尾";
    private static final String TITLE = "索引状态测试";
    private static final List<String> TAGS = List.of("中文", "emoji😀", " spaced ");

    @Autowired
    private JdbcTemplate jdbcTemplate;

    @Autowired
    private DocumentIndexRepository repository;

    @Autowired
    private PlatformTransactionManager transactionManager;

    @DynamicPropertySource
    static void isolatedDatabase(DynamicPropertyRegistry registry) {
        String url = "jdbc:mysql://${MYSQL_HOST:localhost}:${MYSQL_PORT:3306}/" + TEST_DATABASE
                + "?createDatabaseIfNotExist=true&useUnicode=true&characterEncoding=UTF-8"
                + "&serverTimezone=Asia/Shanghai&useSSL=false&allowPublicKeyRetrieval=true";
        registry.add("spring.datasource.url", () -> url);
        registry.add("spring.datasource.hikari.transaction-isolation", () -> "TRANSACTION_REPEATABLE_READ");
        registry.add("spring.flyway.url", () -> url);
        registry.add("spring.flyway.user", () -> "${spring.datasource.username}");
        registry.add("spring.flyway.password", () -> "${spring.datasource.password}");
        registry.add("spring.flyway.enabled", () -> true);
    }

    @BeforeEach
    void clearOnlyIsolatedTestData() {
        assertIsolatedDatabase();
        jdbcTemplate.execute("DROP TRIGGER IF EXISTS fail_index_status_update");
        jdbcTemplate.execute("DROP TRIGGER IF EXISTS fail_index_chunk_insert");
        jdbcTemplate.update("DELETE FROM document");
    }

    @AfterAll
    void dropOnlyIsolatedTestDatabase() {
        assertIsolatedDatabase();
        jdbcTemplate.execute("DROP DATABASE `" + TEST_DATABASE + "`");
        System.out.println("Dropped isolated MySQL schema: " + TEST_DATABASE);
    }

    @Test
    void findsPendingSnapshotWithoutChangingStoredState() {
        long documentId = createDocument("PENDING");
        Map<String, Object> before = documentState(documentId);

        var found = repository.findPending(documentId);

        assertThat(found).contains(snapshot(documentId));
        assertThatThrownBy(() -> found.orElseThrow().tags().clear()).isInstanceOf(UnsupportedOperationException.class);
        assertThat(documentState(documentId)).isEqualTo(before);
        assertThat(storedChunks(documentId)).isEmpty();
    }

    @Test
    void snapshotCopiesMutableTags() {
        List<String> tags = new ArrayList<>(TAGS);
        var document = new DocumentIndexRepository.PendingDocument(1, SOURCE, TITLE, tags);

        tags.clear();

        assertThat(document.tags()).isEqualTo(TAGS);
        assertThatThrownBy(() -> document.tags().clear()).isInstanceOf(UnsupportedOperationException.class);
    }

    @ParameterizedTest
    @NullSource
    @ValueSource(strings = {"null", "[]"})
    void mapsAbsentTagsToEmptyList(String storedTags) {
        long documentId = createDocument("PENDING");
        jdbcTemplate.update("UPDATE document SET tags = ? WHERE id = ?", storedTags, documentId);

        var found = repository.findPending(documentId);

        assertThat(found).isPresent();
        assertThat(found.orElseThrow().tags()).isEmpty();
    }

    @ParameterizedTest
    @ValueSource(strings = {"{}", "\"tag\"", "42", "[1]", "[true]", "[null]", "[\"valid\", []]"})
    void rejectsNonStringArrayTagsWithoutChangingTheDocument(String storedTags) {
        long documentId = createDocument("PENDING");
        jdbcTemplate.update("UPDATE document SET tags = ? WHERE id = ?", storedTags, documentId);
        Map<String, Object> before = documentState(documentId);

        assertThatThrownBy(() -> repository.findPending(documentId)).isInstanceOf(IllegalStateException.class);

        assertThat(documentState(documentId)).isEqualTo(before);
    }

    @ParameterizedTest
    @ValueSource(strings = {"INDEXING", "INDEXED", "FAILED", "DELETED", "MISSING"})
    void doesNotLoadIneligibleDocuments(String state) {
        long documentId = documentInState(state, "PENDING");
        List<Map<String, Object>> before = allDocumentStates();

        assertThat(repository.findPending(documentId)).isEmpty();

        assertThat(allDocumentStates()).isEqualTo(before);
    }

    @Test
    void commitsCompleteChunksAndIndexingTogetherWithoutChangingSourceFields() {
        long documentId = createDocumentWithChunk("PENDING");
        List<Map<String, Object>> oldChunks = storedChunks(documentId);
        Map<String, Object> expected = new LinkedHashMap<>(documentState(documentId));
        expected.put("index_status", "INDEXING");
        expected.put("index_error", null);
        expected.put("chunk_count", 3);
        long otherId = createDocumentWithChunk("INDEXED");
        Map<String, Object> otherDocument = documentState(otherId);
        List<Map<String, Object>> otherChunks = storedChunks(otherId);

        List<ChunkRepository.StoredChunk> saved = repository.saveChunksAndMarkIndexing(snapshot(documentId), batch());

        assertThat(saved).hasSize(3);
        assertThat(saved).extracting(ChunkRepository.StoredChunk::seq).containsExactly(0, 1, 2);
        assertThat(saved).extracting(ChunkRepository.StoredChunk::documentId).containsOnly(documentId);
        assertThat(saved).extracting(ChunkRepository.StoredChunk::chunkId).doesNotHaveDuplicates()
                .doesNotContain(((Number) oldChunks.get(0).get("id")).longValue());
        assertThat(storedChunks(documentId)).hasSize(3);
        for (int sequence = 0; sequence < saved.size(); sequence++) {
            assertThat(storedChunks(documentId).get(sequence)).containsEntry("id", saved.get(sequence).chunkId())
                    .containsEntry("text", batch().chunks().get(sequence).text());
        }
        assertThat(documentState(documentId)).isEqualTo(expected);
        assertThat(documentState(otherId)).isEqualTo(otherDocument);
        assertThat(storedChunks(otherId)).isEqualTo(otherChunks);
    }

    @ParameterizedTest
    @ValueSource(strings = {"INDEXING", "INDEXED", "FAILED", "DELETED", "MISSING"})
    void rejectsChunkReplacementUnlessDocumentIsPending(String state) {
        long documentId = documentInState(state, "PENDING");
        List<Map<String, Object>> documents = allDocumentStates();
        List<Map<String, Object>> chunks = storedChunks(documentId);

        assertThatThrownBy(() -> repository.saveChunksAndMarkIndexing(snapshot(documentId), batch()))
                .isInstanceOf(IllegalStateException.class);

        assertThat(allDocumentStates()).isEqualTo(documents);
        assertThat(storedChunks(documentId)).isEqualTo(chunks);
    }

    @Test
    void changedContentRollsBackStatusAndRetainsOldChunks() {
        long documentId = createDocumentWithChunk("PENDING");
        jdbcTemplate.update("UPDATE document SET content = 'changed' WHERE id = ?", documentId);
        Map<String, Object> before = documentState(documentId);
        List<Map<String, Object>> chunks = storedChunks(documentId);

        assertThatThrownBy(() -> repository.saveChunksAndMarkIndexing(snapshot(documentId), batch()))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("content changed");

        assertThat(documentState(documentId)).isEqualTo(before);
        assertThat(storedChunks(documentId)).isEqualTo(chunks);
    }

    @Test
    void invalidBatchRollsBackStatusAndRetainsOldChunks() {
        long documentId = createDocumentWithChunk("PENDING");
        Map<String, Object> before = documentState(documentId);
        List<Map<String, Object>> chunks = storedChunks(documentId);
        ChunkBatch invalid = new ChunkBatch(List.of(new ChunkBatch.Chunk(0, "wrong", 0, 3, "", 1)));

        assertThatThrownBy(() -> repository.saveChunksAndMarkIndexing(snapshot(documentId), invalid))
                .isInstanceOf(IllegalArgumentException.class);

        assertThat(documentState(documentId)).isEqualTo(before);
        assertThat(storedChunks(documentId)).isEqualTo(chunks);
    }

    @Test
    void failedChunkInsertRollsBackStatusDeletionAndCount() {
        long documentId = createDocumentWithChunk("PENDING");
        Map<String, Object> before = documentState(documentId);
        List<Map<String, Object>> chunks = storedChunks(documentId);
        jdbcTemplate.execute("""
                CREATE TRIGGER fail_index_chunk_insert BEFORE INSERT ON chunk FOR EACH ROW
                SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'forced chunk insert failure'
                """);

        assertThatThrownBy(() -> repository.saveChunksAndMarkIndexing(snapshot(documentId), batch()))
                .isInstanceOf(DataAccessException.class);

        assertThat(documentState(documentId)).isEqualTo(before);
        assertThat(storedChunks(documentId)).isEqualTo(chunks);
    }

    @Test
    void failedIndexingUpdateRetainsOldStateAndChunks() {
        long documentId = createDocumentWithChunk("PENDING");
        Map<String, Object> before = documentState(documentId);
        List<Map<String, Object>> chunks = storedChunks(documentId);
        failStatusUpdate("INDEXING");

        assertThatThrownBy(() -> repository.saveChunksAndMarkIndexing(snapshot(documentId), batch()))
                .isInstanceOf(DataAccessException.class);

        assertThat(documentState(documentId)).isEqualTo(before);
        assertThat(storedChunks(documentId)).isEqualTo(chunks);
    }

    @Test
    void marksIndexedWithDatabaseTimeWithoutChangingSourceOrChunks() {
        long documentId = createDocumentWithChunk("INDEXING");
        Map<String, Object> expected = new LinkedHashMap<>(documentState(documentId));
        List<Map<String, Object>> chunks = storedChunks(documentId);
        LocalDateTime before = databaseTime();

        repository.markIndexed(documentId);

        LocalDateTime after = databaseTime();
        Map<String, Object> actual = documentState(documentId);
        expected.put("index_status", "INDEXED");
        expected.put("index_error", null);
        expected.put("indexed_at", actual.get("indexed_at"));
        assertThat(actual).isEqualTo(expected);
        assertThat(jdbcTemplate.queryForObject("SELECT indexed_at FROM document WHERE id = ?",
                LocalDateTime.class, documentId)).isBetween(before, after);
        assertThat(storedChunks(documentId)).isEqualTo(chunks);
        assertThatThrownBy(() -> repository.markIndexed(documentId)).isInstanceOf(IllegalStateException.class);
    }

    @ParameterizedTest
    @ValueSource(strings = {"PENDING", "INDEXING"})
    void marksFailedAndPreservesChunksAndHistoricalSuccessTime(String state) {
        long documentId = createDocumentWithChunk(state);
        Map<String, Object> expected = new LinkedHashMap<>(documentState(documentId));
        List<Map<String, Object>> chunks = storedChunks(documentId);
        String error = "EMBED_FAILED: model unavailable; cleanup=failed: connection refused 😀";
        expected.put("index_status", "FAILED");
        expected.put("index_error", error);

        repository.markFailed(documentId, error);

        assertThat(documentState(documentId)).isEqualTo(expected);
        assertThat(storedChunks(documentId)).isEqualTo(chunks);
        assertThatThrownBy(() -> repository.markFailed(documentId, "late failure"))
                .isInstanceOf(IllegalStateException.class);
        assertThat(documentState(documentId)).isEqualTo(expected);
    }

    @ParameterizedTest
    @ValueSource(strings = {"PENDING", "INDEXED", "FAILED", "DELETED", "MISSING"})
    void rejectsIndexedCompletionFromWrongState(String state) {
        long documentId = documentInState(state, "INDEXING");
        List<Map<String, Object>> before = allDocumentStates();

        assertThatThrownBy(() -> repository.markIndexed(documentId)).isInstanceOf(IllegalStateException.class);

        assertThat(allDocumentStates()).isEqualTo(before);
    }

    @ParameterizedTest
    @ValueSource(strings = {"INDEXED", "FAILED", "DELETED", "MISSING"})
    void rejectsFailedCompletionFromWrongState(String state) {
        long documentId = documentInState(state, "INDEXING");
        List<Map<String, Object>> before = allDocumentStates();

        assertThatThrownBy(() -> repository.markFailed(documentId, "failure"))
                .isInstanceOf(IllegalStateException.class);

        assertThat(allDocumentStates()).isEqualTo(before);
    }

    @ParameterizedTest
    @ValueSource(strings = {"INDEXED", "FAILED"})
    void terminalSqlFailureIsNotReportedAsSuccess(String terminalState) {
        long documentId = createDocumentWithChunk("INDEXING");
        Map<String, Object> before = documentState(documentId);
        failStatusUpdate(terminalState);

        assertThatThrownBy(() -> {
            if (terminalState.equals("INDEXED")) {
                repository.markIndexed(documentId);
            } else {
                repository.markFailed(documentId, "failure");
            }
        }).isInstanceOf(DataAccessException.class);

        assertThat(documentState(documentId)).isEqualTo(before);
    }

    @ParameterizedTest
    @MethodSource("invalidErrors")
    void rejectsUnstorableFailureReasonWithoutMutatingState(String error) {
        long documentId = createDocument("PENDING");
        Map<String, Object> before = documentState(documentId);

        assertThatThrownBy(() -> repository.markFailed(documentId, error))
                .isInstanceOf(IllegalArgumentException.class);

        assertThat(documentState(documentId)).isEqualTo(before);
    }

    @ParameterizedTest
    @ValueSource(strings = {"中", "😀"})
    void storesExactly1024UnicodeCodePointsWithoutTruncation(String character) {
        long documentId = createDocument("PENDING");
        String error = character.repeat(1024);

        repository.markFailed(documentId, error);

        assertThat(documentState(documentId)).containsEntry("index_status", "FAILED").containsEntry("index_error", error);
    }

    @ParameterizedTest
    @ValueSource(longs = {0, -1, Long.MIN_VALUE})
    void rejectsNonPositiveDocumentIds(long documentId) {
        assertAll(
                () -> assertThrows(IllegalArgumentException.class, () -> repository.findPending(documentId)),
                () -> assertThrows(IllegalArgumentException.class,
                        () -> repository.saveChunksAndMarkIndexing(snapshot(documentId), batch())),
                () -> assertThrows(IllegalArgumentException.class, () -> repository.markIndexed(documentId)),
                () -> assertThrows(IllegalArgumentException.class, () -> repository.markFailed(documentId, "failure")));
    }

    @Test
    void joinsReadCommittedTransactionAndDoesNotCommitAheadOfItsCaller() {
        long documentId = createDocumentWithChunk("PENDING");
        Map<String, Object> before = documentState(documentId);
        List<Map<String, Object>> chunks = storedChunks(documentId);
        TransactionTemplate transaction = new TransactionTemplate(transactionManager);
        transaction.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);

        assertThatThrownBy(() -> transaction.executeWithoutResult(status -> {
            assertThat(repository.saveChunksAndMarkIndexing(snapshot(documentId), batch())).hasSize(3);
            repository.markIndexed(documentId);
            assertThat(documentState(documentId)).containsEntry("index_status", "INDEXED");
            throw new IllegalStateException("outer rollback");
        })).isInstanceOf(IllegalStateException.class).hasMessage("outer rollback");

        assertThat(documentState(documentId)).isEqualTo(before);
        assertThat(storedChunks(documentId)).isEqualTo(chunks);
    }

    @Test
    void inheritedRepeatableReadRollsBackIndexingAndChunks() {
        long documentId = createDocumentWithChunk("PENDING");
        Map<String, Object> before = documentState(documentId);
        List<Map<String, Object>> chunks = storedChunks(documentId);
        TransactionTemplate transaction = new TransactionTemplate(transactionManager);
        transaction.setIsolationLevel(TransactionDefinition.ISOLATION_REPEATABLE_READ);

        assertThatThrownBy(() -> transaction.executeWithoutResult(status ->
                repository.saveChunksAndMarkIndexing(snapshot(documentId), batch())))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("requires READ_COMMITTED");

        assertThat(documentState(documentId)).isEqualTo(before);
        assertThat(storedChunks(documentId)).isEqualTo(chunks);
    }

    @Test
    void concurrentPendingAttemptsCommitOnlyOneReplacement() throws Exception {
        long documentId = createDocumentWithChunk("PENDING");
        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch start = new CountDownLatch(1);
        ExecutorService executor = Executors.newFixedThreadPool(2);
        List<Future<Object>> attempts = new ArrayList<>();
        try {
            for (int attempt = 0; attempt < 2; attempt++) {
                attempts.add(executor.submit(() -> {
                    ready.countDown();
                    assertTrue(start.await(5, TimeUnit.SECONDS));
                    try {
                        return repository.saveChunksAndMarkIndexing(snapshot(documentId), batch());
                    } catch (IllegalStateException failure) {
                        return failure;
                    }
                }));
            }
            assertTrue(ready.await(5, TimeUnit.SECONDS));
            start.countDown();
            int committed = 0;
            int rejected = 0;
            for (Future<Object> attempt : attempts) {
                Object outcome = attempt.get(5, TimeUnit.SECONDS);
                if (outcome instanceof List<?> saved) {
                    assertThat(saved).hasSize(3);
                    committed++;
                } else {
                    assertThat(outcome).isInstanceOf(IllegalStateException.class);
                    rejected++;
                }
            }
            assertThat(committed).isEqualTo(1);
            assertThat(rejected).isEqualTo(1);
            assertThat(documentState(documentId)).containsEntry("index_status", "INDEXING").containsEntry("chunk_count", 3);
            assertThat(storedChunks(documentId)).hasSize(3);
        } finally {
            start.countDown();
            executor.shutdownNow();
            assertTrue(executor.awaitTermination(5, TimeUnit.SECONDS));
        }
    }

    private void assertIsolatedDatabase() {
        assertThat(TEST_DATABASE).matches("easyrag_index_it_[0-9a-f]{32}");
        assertThat(jdbcTemplate.queryForObject("SELECT DATABASE()", String.class)).isEqualTo(TEST_DATABASE);
    }

    private long createDocument(String state) {
        GeneratedKeyHolder keys = new GeneratedKeyHolder();
        jdbcTemplate.update(connection -> {
            var statement = connection.prepareStatement("""
                    INSERT INTO document (source_type, source_uri, title, content, content_hash, tags,
                                          index_status, index_error, updated_at, indexed_at)
                    VALUES ('UPLOAD', 'index-it.md', ?, ?, ?, '["中文","emoji😀"," spaced "]',
                            ?, 'previous error', '2026-01-01 00:00:00', '2026-01-02 00:00:00')
                    """, Statement.RETURN_GENERATED_KEYS);
            statement.setString(1, TITLE);
            statement.setString(2, SOURCE);
            statement.setString(3, "0".repeat(64));
            statement.setString(4, state);
            return statement;
        }, keys);
        return Objects.requireNonNull(keys.getKey()).longValue();
    }

    private long createDocumentWithChunk(String state) {
        long documentId = createDocument(state);
        String oldContent = "旧切片";
        jdbcTemplate.update("""
                INSERT INTO chunk (document_id, seq, text, char_start, char_end, heading_path, token_count)
                VALUES (?, 0, ?, 0, ?, '', 1)
                """, documentId, oldContent, oldContent.getBytes(StandardCharsets.UTF_8).length);
        jdbcTemplate.update("UPDATE document SET chunk_count = 1 WHERE id = ?", documentId);
        return documentId;
    }

    private long documentInState(String state, String eligibleState) {
        if (state.equals("MISSING")) {
            return Long.MAX_VALUE;
        }
        long documentId = createDocumentWithChunk(state.equals("DELETED") ? eligibleState : state);
        if (state.equals("DELETED")) {
            jdbcTemplate.update("UPDATE document SET deleted_at = CURRENT_TIMESTAMP WHERE id = ?", documentId);
        }
        return documentId;
    }

    private DocumentIndexRepository.PendingDocument snapshot(long documentId) {
        return new DocumentIndexRepository.PendingDocument(documentId, SOURCE, TITLE, TAGS);
    }

    private ChunkBatch batch() {
        return new ChunkBatch(List.of(new ChunkBatch.Chunk(0, "中", 0, 3, "章节", 2),
                new ChunkBatch.Chunk(1, "😀\r\n", 3, 9, "章节", 3),
                new ChunkBatch.Chunk(2, "尾", 9, 12, "章节", 2)));
    }

    private Map<String, Object> documentState(long documentId) {
        return jdbcTemplate.queryForMap("SELECT * FROM document WHERE id = ?", documentId);
    }

    private List<Map<String, Object>> allDocumentStates() {
        return jdbcTemplate.queryForList("SELECT * FROM document ORDER BY id");
    }

    private List<Map<String, Object>> storedChunks(long documentId) {
        return jdbcTemplate.queryForList("SELECT * FROM chunk WHERE document_id = ? ORDER BY seq", documentId);
    }

    private LocalDateTime databaseTime() {
        return jdbcTemplate.queryForObject("SELECT CURRENT_TIMESTAMP()", LocalDateTime.class);
    }

    private void failStatusUpdate(String state) {
        jdbcTemplate.execute("""
                CREATE TRIGGER fail_index_status_update BEFORE UPDATE ON document FOR EACH ROW
                BEGIN
                    IF NEW.index_status = '%s' THEN
                        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'forced status update failure';
                    END IF;
                END
                """.formatted(state));
    }

    private static Stream<String> invalidErrors() {
        return Stream.of(null, "", " \r\n", "\uD800", "x".repeat(1025), "😀".repeat(1025));
    }
}
