package com.easyrag.server.document;

import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.RepeatedTest;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.dao.DataAccessException;
import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.jdbc.core.ConnectionCallback;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

import javax.sql.DataSource;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.Connection;
import java.sql.Statement;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import java.util.concurrent.BrokenBarrierException;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicReference;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertAll;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE)
@Import(ChunkRepositoryIT.ConcurrentReplaceConfiguration.class)
@ActiveProfiles("local")
@Tag("requires-mysql")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
@DirtiesContext(classMode = DirtiesContext.ClassMode.AFTER_CLASS)
class ChunkRepositoryIT {

    private static final String TEST_DATABASE = "easyrag_chunk_it_" + UUID.randomUUID().toString().replace("-", "");
    private static final String SOURCE = "中😀\r\n尾";

    @Autowired
    private GatedJdbcTemplate jdbcTemplate;

    @Autowired
    private ChunkRepository repository;

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
        jdbcTemplate.execute("DROP TRIGGER IF EXISTS fail_chunk_delete");
        jdbcTemplate.execute("DROP TRIGGER IF EXISTS fail_chunk_insert");
        jdbcTemplate.execute("DROP TRIGGER IF EXISTS fail_document_update");
        jdbcTemplate.update("DELETE FROM document");
    }

    @AfterAll
    void dropOnlyIsolatedTestDatabase() {
        assertIsolatedDatabase();
        jdbcTemplate.execute("DROP DATABASE `" + TEST_DATABASE + "`");
        System.out.println("Dropped isolated MySQL schema: " + TEST_DATABASE);
    }

    @Test
    void replacementMapsByteOffsetsReturnsIdsAndPreservesOtherDocumentState() {
        long documentId = createDocument(SOURCE);
        seedWholeChunk(documentId, SOURCE);
        List<Map<String, Object>> previous = storedChunks(documentId);
        Map<String, Object> expectedDocument = new LinkedHashMap<>(documentState(documentId));
        expectedDocument.put("chunk_count", 3);
        long otherId = createDocument("other");
        seedWholeChunk(otherId, "other");
        List<Map<String, Object>> otherChunks = storedChunks(otherId);
        Map<String, Object> otherDocument = documentState(otherId);

        List<ChunkRepository.StoredChunk> saved = repository.replace(documentId, SOURCE, splitBatch());

        assertThat(saved).hasSize(3);
        List<Map<String, Object>> actual = storedChunks(documentId);
        assertThat(actual).hasSize(3);
        for (int sequence = 0; sequence < actual.size(); sequence++) {
            ChunkBatch.Chunk expected = splitBatch().chunks().get(sequence);
            ChunkRepository.StoredChunk chunk = saved.get(sequence);
            assertThat(chunk.chunkId()).isPositive();
            assertThat(chunk).isEqualTo(new ChunkRepository.StoredChunk(chunk.chunkId(), documentId,
                    expected.seq(), expected.text(), expected.byteStart(), expected.byteEnd(),
                    expected.headingPath(), expected.tokenCount()));
            assertThat(actual.get(sequence)).containsAllEntriesOf(Map.of(
                    "id", chunk.chunkId(), "document_id", documentId, "seq", expected.seq(),
                    "text", expected.text(), "char_start", expected.byteStart(), "char_end", expected.byteEnd(),
                    "heading_path", expected.headingPath(), "token_count", expected.tokenCount()));
        }
        assertThat(saved).extracting(ChunkRepository.StoredChunk::chunkId).doesNotHaveDuplicates()
                .doesNotContain(((Number) previous.get(0).get("id")).longValue());
        assertThrows(UnsupportedOperationException.class, saved::clear);
        assertThat(documentState(documentId)).isEqualTo(expectedDocument);
        assertThat(storedChunks(otherId)).isEqualTo(otherChunks);
        assertThat(documentState(otherId)).isEqualTo(otherDocument);
    }

    @Test
    void shorterReplacementRemovesOldRemainderAndUpdatesCount() {
        long documentId = createDocument(SOURCE);
        List<ChunkRepository.StoredChunk> previous = repository.replace(documentId, SOURCE, splitBatch());

        List<ChunkRepository.StoredChunk> saved = repository.replace(documentId, SOURCE, wholeBatch(SOURCE));

        assertThat(storedChunks(documentId)).hasSize(1);
        assertThat(documentState(documentId)).containsEntry("chunk_count", 1);
        assertThat(saved).hasSize(1);
        assertThat(previous).extracting(ChunkRepository.StoredChunk::chunkId).doesNotContain(saved.get(0).chunkId());
    }

    @Test
    void mysqlPreservesBomCombiningCharactersAndCrlf() {
        String content = "\uFEFFe\u0301👩\u200D💻\r\n";
        long documentId = createDocument(content);

        repository.replace(documentId, content, wholeBatch(content));

        assertThat(storedChunks(documentId)).hasSize(1);
        assertThat(storedChunks(documentId).get(0)).containsEntry("text", content)
                .containsEntry("char_start", 0)
                .containsEntry("char_end", content.getBytes(StandardCharsets.UTF_8).length);
        assertThat(documentState(documentId)).containsEntry("content", content);
    }

    @Test
    void mysqlAcceptsExactTextByteAndHeadingCodePointLimits() {
        String content = "中".repeat(21845);
        String heading = "😀".repeat(512);
        long documentId = createDocument(content);
        ChunkBatch batch = new ChunkBatch(List.of(new ChunkBatch.Chunk(0, content, 0, 65535, heading, 0)));

        repository.replace(documentId, content, batch);

        assertThat(storedChunks(documentId)).hasSize(1);
        assertThat(storedChunks(documentId).get(0)).containsEntry("text", content)
                .containsEntry("heading_path", heading).containsEntry("char_end", 65535);
        assertThat(jdbcTemplate.queryForObject("SELECT LENGTH(text) FROM chunk WHERE document_id = ?",
                Integer.class, documentId)).isEqualTo(65535);
        assertThat(jdbcTemplate.queryForObject("SELECT CHAR_LENGTH(heading_path) FROM chunk WHERE document_id = ?",
                Integer.class, documentId)).isEqualTo(512);
    }

    @ParameterizedTest
    @MethodSource("contentVersions")
    void rawContentVersionIsCheckedWithoutNormalizationOrSqlCollation(String chunkedContent, String currentContent) {
        long documentId = createDocument(currentContent);
        seedWholeChunk(documentId, currentContent);
        List<Map<String, Object>> previous = storedChunks(documentId);
        Map<String, Object> previousDocument = documentState(documentId);

        assertThrows(IllegalStateException.class,
                () -> repository.replace(documentId, chunkedContent, wholeBatch(chunkedContent)));

        assertThat(storedChunks(documentId)).isEqualTo(previous);
        assertThat(documentState(documentId)).isEqualTo(previousDocument);
    }

    @Test
    void missingDocumentCannotReceiveChunks() {
        assertThrows(EmptyResultDataAccessException.class,
                () -> repository.replace(Long.MAX_VALUE, SOURCE, splitBatch()));
        assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM chunk", Integer.class)).isZero();
    }

    @Test
    void softDeletedDocumentCannotReceiveChunks() {
        long documentId = createDocument(SOURCE);
        seedWholeChunk(documentId, SOURCE);
        jdbcTemplate.update("UPDATE document SET deleted_at = CURRENT_TIMESTAMP WHERE id = ?", documentId);
        List<Map<String, Object>> previous = storedChunks(documentId);
        Map<String, Object> previousDocument = documentState(documentId);

        assertThrows(EmptyResultDataAccessException.class, () -> repository.replace(documentId, SOURCE, splitBatch()));

        assertThat(storedChunks(documentId)).isEqualTo(previous);
        assertThat(documentState(documentId)).isEqualTo(previousDocument);
    }

    @ParameterizedTest
    @ValueSource(longs = {0, -1})
    void documentIdMustBePositive(long documentId) {
        assertThrows(IllegalArgumentException.class, () -> repository.replace(documentId, SOURCE, splitBatch()));
    }

    @Test
    void invalidLaterChunkLeavesOldRowsAndCountUntouched() {
        long documentId = createDocument(SOURCE);
        seedWholeChunk(documentId, SOURCE);
        List<Map<String, Object>> previous = storedChunks(documentId);
        Map<String, Object> previousDocument = documentState(documentId);
        ChunkBatch invalid = new ChunkBatch(List.of(new ChunkBatch.Chunk(0, "中", 0, 3, "", 1),
                new ChunkBatch.Chunk(1, "wrong", 3, 12, "", 1)));

        assertThrows(IllegalArgumentException.class, () -> repository.replace(documentId, SOURCE, invalid));

        assertThat(storedChunks(documentId)).isEqualTo(previous);
        assertThat(documentState(documentId)).isEqualTo(previousDocument);
    }

    @Test
    void failureOnSecondInsertRollsBackDeletionAndFirstInsert() {
        long documentId = createDocument(SOURCE);
        seedWholeChunk(documentId, SOURCE);
        List<Map<String, Object>> previous = storedChunks(documentId);
        Map<String, Object> previousDocument = documentState(documentId);
        jdbcTemplate.execute("""
                CREATE TRIGGER fail_chunk_insert BEFORE INSERT ON chunk FOR EACH ROW
                BEGIN
                    IF NEW.seq = 1 THEN
                        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'test second insert failure';
                    END IF;
                END
                """);

        assertThrows(DataAccessException.class, () -> repository.replace(documentId, SOURCE, splitBatch()));

        assertThat(storedChunks(documentId)).isEqualTo(previous);
        assertThat(documentState(documentId)).isEqualTo(previousDocument);
    }

    @Test
    void failureUpdatingCountRollsBackAllNewChunks() {
        long documentId = createDocument(SOURCE);
        seedWholeChunk(documentId, SOURCE);
        List<Map<String, Object>> previous = storedChunks(documentId);
        Map<String, Object> previousDocument = documentState(documentId);
        jdbcTemplate.execute("""
                CREATE TRIGGER fail_document_update BEFORE UPDATE ON document FOR EACH ROW
                SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'test count update failure'
                """);

        assertThrows(DataAccessException.class, () -> repository.replace(documentId, SOURCE, splitBatch()));

        assertThat(storedChunks(documentId)).isEqualTo(previous);
        assertThat(documentState(documentId)).isEqualTo(previousDocument);
    }

    @RepeatedTest(3)
    void concurrentFirstReplacementsOfDifferentDocumentsBothCommit() throws Exception {
        long firstId = createDocument(SOURCE);
        long secondId = createDocument("other");
        assertThat(jdbcTemplate.execute((ConnectionCallback<Integer>) Connection::getTransactionIsolation))
                .isEqualTo(Connection.TRANSACTION_REPEATABLE_READ);
        var executor = Executors.newFixedThreadPool(2);
        jdbcTemplate.afterChunkDelete = new CyclicBarrier(2);
        try {
            Future<List<ChunkRepository.StoredChunk>> firstPending = executor.submit(
                    () -> repository.replace(firstId, SOURCE, splitBatch()));
            Future<List<ChunkRepository.StoredChunk>> secondPending = executor.submit(
                    () -> repository.replace(secondId, "other", wholeBatch("other")));

            assertAll(
                    () -> assertThat(firstPending.get(15, TimeUnit.SECONDS)).hasSize(3),
                    () -> assertThat(secondPending.get(15, TimeUnit.SECONDS)).hasSize(1));
            assertThat(storedChunks(firstId)).extracting(chunk -> chunk.get("text"))
                    .containsExactly("中", "😀\r\n", "尾");
            assertThat(storedChunks(secondId)).extracting(chunk -> chunk.get("text")).containsExactly("other");
            assertThat(documentState(firstId)).containsEntry("chunk_count", 3);
            assertThat(documentState(secondId)).containsEntry("chunk_count", 1);
        } finally {
            jdbcTemplate.afterChunkDelete = null;
            executor.shutdownNow();
            assertTrue(executor.awaitTermination(5, TimeUnit.SECONDS));
        }
    }

    @ParameterizedTest
    @ValueSource(ints = {TransactionDefinition.ISOLATION_DEFAULT, TransactionDefinition.ISOLATION_READ_UNCOMMITTED,
            TransactionDefinition.ISOLATION_REPEATABLE_READ, TransactionDefinition.ISOLATION_SERIALIZABLE})
    void incompatibleOuterTransactionIsRejectedBeforeDeletingOldChunks(int isolationLevel) {
        long documentId = createDocument(SOURCE);
        seedWholeChunk(documentId, SOURCE);
        List<Map<String, Object>> previous = storedChunks(documentId);
        Map<String, Object> previousDocument = documentState(documentId);
        jdbcTemplate.execute("""
                CREATE TRIGGER fail_chunk_delete BEFORE DELETE ON chunk FOR EACH ROW
                SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'incompatible transaction reached chunk deletion'
                """);
        TransactionTemplate transaction = new TransactionTemplate(transactionManager);
        transaction.setIsolationLevel(isolationLevel);

        IllegalStateException failure = assertThrows(IllegalStateException.class,
                () -> transaction.executeWithoutResult(status -> {
                    assertThat(jdbcTemplate.execute((ConnectionCallback<Integer>) Connection::getTransactionIsolation))
                            .isEqualTo(isolationLevel == TransactionDefinition.ISOLATION_DEFAULT
                                    ? Connection.TRANSACTION_REPEATABLE_READ : isolationLevel);
                    repository.replace(documentId, SOURCE, splitBatch());
                }));

        assertThat(failure).hasMessage("chunk replacement requires READ_COMMITTED transaction isolation");
        assertThat(storedChunks(documentId)).isEqualTo(previous);
        assertThat(documentState(documentId)).isEqualTo(previousDocument);
    }

    @Test
    void replacementJoinsReadCommittedOuterTransactionAndRollsBackWithIt() {
        long documentId = createDocument(SOURCE);
        seedWholeChunk(documentId, SOURCE);
        List<Map<String, Object>> previous = storedChunks(documentId);
        Map<String, Object> previousDocument = documentState(documentId);
        TransactionTemplate transaction = new TransactionTemplate(transactionManager);
        transaction.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);
        IllegalStateException outerFailure = new IllegalStateException("test outer transaction failure");

        IllegalStateException failure = assertThrows(IllegalStateException.class,
                () -> transaction.executeWithoutResult(status -> {
                    assertThat(repository.replace(documentId, SOURCE, splitBatch())).hasSize(3);
                    assertThat(storedChunks(documentId)).hasSize(3);
                    assertThat(documentState(documentId)).containsEntry("chunk_count", 3);
                    jdbcTemplate.update("UPDATE document SET index_status = 'FAILED' WHERE id = ?", documentId);
                    throw outerFailure;
                }));

        assertThat(failure).isSameAs(outerFailure);
        assertThat(storedChunks(documentId)).isEqualTo(previous);
        assertThat(documentState(documentId)).isEqualTo(previousDocument);
    }

    @Test
    void replacementWaitsForDocumentLockAndRejectsNewlyCommittedContent() throws Exception {
        long documentId = createDocument(SOURCE);
        seedWholeChunk(documentId, SOURCE);
        List<Map<String, Object>> previous = storedChunks(documentId);
        var executor = Executors.newSingleThreadExecutor();
        CountDownLatch started = new CountDownLatch(1);
        AtomicReference<Future<List<ChunkRepository.StoredChunk>>> pending = new AtomicReference<>();
        try {
            new TransactionTemplate(transactionManager).executeWithoutResult(status -> {
                jdbcTemplate.queryForObject("SELECT id FROM document WHERE id = ? FOR UPDATE", Long.class, documentId);
                pending.set(executor.submit(() -> {
                    started.countDown();
                    return repository.replace(documentId, SOURCE, splitBatch());
                }));
                try {
                    assertTrue(started.await(5, TimeUnit.SECONDS));
                    assertThrows(TimeoutException.class, () -> pending.get().get(500, TimeUnit.MILLISECONDS));
                } catch (InterruptedException failure) {
                    Thread.currentThread().interrupt();
                    throw new AssertionError(failure);
                }
                jdbcTemplate.update("UPDATE document SET content = ? WHERE id = ?", "new content", documentId);
            });

            ExecutionException failure = assertThrows(ExecutionException.class,
                    () -> pending.get().get(5, TimeUnit.SECONDS));
            assertThat(failure.getCause()).isInstanceOf(IllegalStateException.class);
            assertThat(storedChunks(documentId)).isEqualTo(previous);
            assertThat(documentState(documentId)).containsEntry("content", "new content").containsEntry("chunk_count", 1);
        } finally {
            executor.shutdownNow();
            assertTrue(executor.awaitTermination(5, TimeUnit.SECONDS));
        }
    }

    private void assertIsolatedDatabase() {
        assertThat(TEST_DATABASE).matches("easyrag_chunk_it_[0-9a-f]{32}");
        assertThat(jdbcTemplate.queryForObject("SELECT DATABASE()", String.class)).isEqualTo(TEST_DATABASE);
    }

    private static Stream<Arguments> contentVersions() {
        return Stream.of(Arguments.of("line\r\nnext", "line\nnext"), Arguments.of(" text ", "text"),
                Arguments.of("A", "a"), Arguments.of("é", "e\u0301"));
    }

    private long createDocument(String content) {
        GeneratedKeyHolder keys = new GeneratedKeyHolder();
        jdbcTemplate.update(connection -> {
            var statement = connection.prepareStatement("""
                    INSERT INTO document (source_type, source_uri, title, content, content_hash,
                                          index_status, updated_at, indexed_at)
                    VALUES ('UPLOAD', 'chunk-it.md', '切片测试', ?, ?, 'INDEXED',
                            '2026-01-01 00:00:00', '2026-01-02 00:00:00')
                    """, Statement.RETURN_GENERATED_KEYS);
            statement.setString(1, content);
            statement.setString(2, normalizedHash(content));
            return statement;
        }, keys);
        return Objects.requireNonNull(keys.getKey()).longValue();
    }

    private void seedWholeChunk(long documentId, String content) {
        jdbcTemplate.update("""
                INSERT INTO chunk (document_id, seq, text, char_start, char_end, heading_path, token_count)
                VALUES (?, 0, ?, 0, ?, '', 1)
                """, documentId, content, content.getBytes(StandardCharsets.UTF_8).length);
        jdbcTemplate.update("UPDATE document SET chunk_count = 1 WHERE id = ?", documentId);
    }

    private List<Map<String, Object>> storedChunks(long documentId) {
        return jdbcTemplate.queryForList("""
                SELECT id, document_id, seq, text, char_start, char_end, heading_path, token_count
                FROM chunk WHERE document_id = ? ORDER BY seq
                """, documentId);
    }

    private Map<String, Object> documentState(long documentId) {
        return jdbcTemplate.queryForMap("""
                SELECT content, content_hash, chunk_count, index_status, index_error, updated_at, indexed_at, deleted_at
                FROM document WHERE id = ?
                """, documentId);
    }

    private ChunkBatch splitBatch() {
        return new ChunkBatch(List.of(new ChunkBatch.Chunk(0, "中", 0, 3, "章节", 2),
                new ChunkBatch.Chunk(1, "😀\r\n", 3, 9, "章节", 3),
                new ChunkBatch.Chunk(2, "尾", 9, 12, "章节", 2)));
    }

    private ChunkBatch wholeBatch(String content) {
        return new ChunkBatch(List.of(new ChunkBatch.Chunk(0, content, 0,
                content.getBytes(StandardCharsets.UTF_8).length, "", 1)));
    }

    private String normalizedHash(String content) {
        String normalized = content.replace("\r\n", "\n").replace('\r', '\n').strip();
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                    .digest(normalized.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException failure) {
            throw new AssertionError(failure);
        }
    }

    @TestConfiguration(proxyBeanMethods = false)
    static class ConcurrentReplaceConfiguration {

        @Bean
        GatedJdbcTemplate jdbcTemplate(DataSource dataSource) {
            return new GatedJdbcTemplate(dataSource);
        }
    }

    private static class GatedJdbcTemplate extends JdbcTemplate {

        private volatile CyclicBarrier afterChunkDelete;

        GatedJdbcTemplate(DataSource dataSource) {
            super(dataSource);
        }

        @Override
        public int update(String sql, Object... arguments) {
            int updated = super.update(sql, arguments);
            CyclicBarrier barrier = afterChunkDelete;
            if (barrier != null && sql.equals("DELETE FROM chunk WHERE document_id = ?")) {
                try {
                    barrier.await(10, TimeUnit.SECONDS);
                } catch (InterruptedException failure) {
                    Thread.currentThread().interrupt();
                    throw new AssertionError(failure);
                } catch (BrokenBarrierException | TimeoutException failure) {
                    throw new AssertionError(failure);
                }
            }
            return updated;
        }
    }
}
