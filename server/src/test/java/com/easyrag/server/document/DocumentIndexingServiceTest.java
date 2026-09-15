package com.easyrag.server.document;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.easyrag.server.document.DocumentIndexingService.IndexingResult;
import com.easyrag.server.document.DocumentIndexingService.Outcome;
import com.easyrag.server.document.DocumentIndexingService.Stage;
import com.easyrag.server.document.DocumentIndexingService.StageTiming;
import com.easyrag.server.rag.RagOperationGate;
import com.easyrag.server.rag.RagOperationGate.Operation;
import com.easyrag.server.rag.RagOperationGate.State;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.EnumSource;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import tools.jackson.databind.json.JsonMapper;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Supplier;
import java.util.stream.Collectors;
import java.util.stream.IntStream;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class DocumentIndexingServiceTest {

    private static final long DOCUMENT_ID = 42;
    private static final String SOURCE = "正文😀\r\nsource-secret";
    private static final String TITLE = "title-secret";
    private static final List<String> TAGS = List.of("RAG", "测试");
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final DocumentIndexRepository.PendingDocument DOCUMENT =
            new DocumentIndexRepository.PendingDocument(DOCUMENT_ID, SOURCE, TITLE, TAGS);
    private static final ChunkBatch BATCH = new ChunkBatch(List.of(new ChunkBatch.Chunk(
            0, SOURCE, 0, SOURCE.getBytes(StandardCharsets.UTF_8).length, "章节", 8)));
    private static final List<ChunkRepository.StoredChunk> STORED = List.of(new ChunkRepository.StoredChunk(
            101, DOCUMENT_ID, 0, SOURCE, 0, SOURCE.getBytes(StandardCharsets.UTF_8).length, "章节", 8));

    private RagOperationGate gate;
    private DocumentIndexRepository documents;
    private DocumentIndexingService service;
    private HttpServer server;
    private ExecutorService httpWorkers;
    private ListAppender<ILoggingEvent> logEvents;
    private final Logger logger = (Logger) LoggerFactory.getLogger(DocumentIndexingService.class);
    private final List<Stage> calls = new CopyOnWriteArrayList<>();
    private final List<Request> requests = new CopyOnWriteArrayList<>();
    private final List<State> observedStates = new CopyOnWriteArrayList<>();
    private final List<Boolean> observedTransactions = new CopyOnWriteArrayList<>();
    private final CountDownLatch phaseEntered = new CountDownLatch(1);
    private final CountDownLatch workerProgress = new CountDownLatch(1);
    private final CountDownLatch releasePhase = new CountDownLatch(1);
    private final CountDownLatch lateMutationWritten = new CountDownLatch(1);
    private final AtomicBoolean simulateLateWrite = new AtomicBoolean();
    private volatile Stage blockedStage;
    private volatile Reply chunkReply;
    private volatile Reply embedReply;
    private volatile Reply deleteReply;

    @BeforeEach
    void setUp() throws IOException {
        gate = readyGate();
        documents = mock(DocumentIndexRepository.class);
        when(documents.findPending(DOCUMENT_ID)).thenAnswer(invocation -> {
            reached(Stage.LOAD_DOCUMENT);
            return Optional.of(DOCUMENT);
        });
        when(documents.saveChunksAndMarkIndexing(any(), any())).thenAnswer(invocation -> {
            reached(Stage.SAVE_CHUNKS);
            return STORED;
        });
        doAnswer(invocation -> {
            reached(Stage.MARK_INDEXED);
            return null;
        }).when(documents).markIndexed(DOCUMENT_ID);
        doAnswer(invocation -> {
            reached(Stage.MARK_FAILED);
            return null;
        }).when(documents).markFailed(eq(DOCUMENT_ID), anyString());
        chunkReply = Reply.json(200, chunkResponse(BATCH));
        embedReply = Reply.json(200, "{\"indexed\":1}");
        deleteReply = Reply.json(200, "{\"removed\":0}");
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        httpWorkers = Executors.newCachedThreadPool();
        server.setExecutor(httpWorkers);
        server.createContext("/chunk", exchange -> serve(exchange, Stage.CHUNK, () -> chunkReply));
        server.createContext("/embed", exchange -> serve(exchange, Stage.EMBED, () -> embedReply));
        server.createContext("/index/", exchange -> serve(exchange, Stage.DELETE_INDEX, () -> deleteReply));
        server.start();
        service = serviceWithEmbedTimeout(5000);
        logEvents = new ListAppender<>();
        logEvents.start();
        logger.addAppender(logEvents);
    }

    @AfterEach
    void tearDown() throws InterruptedException {
        releasePhase.countDown();
        server.stop(0);
        httpWorkers.shutdownNow();
        assertTrue(httpWorkers.awaitTermination(5, TimeUnit.SECONDS));
        logger.detachAppender(logEvents);
        logEvents.stop();
    }

    @Test
    void indexesOneWholeDocumentAndConfirmsOnlyAfterFinalStateReturns() {
        IndexingResult result = service.index(DOCUMENT_ID);

        assertThat(result.outcome()).isEqualTo(Outcome.INDEXED);
        assertThat(result.documentId()).isEqualTo(DOCUMENT_ID);
        assertThat(result.recoveryRequired()).isFalse();
        assertThat(result.error()).isNull();
        assertThat(gate.state()).isEqualTo(State.READY);
        assertThat(calls).containsExactly(Stage.LOAD_DOCUMENT, Stage.CHUNK, Stage.SAVE_CHUNKS,
                Stage.EMBED, Stage.MARK_INDEXED);
        assertThat(observedStates).containsOnly(State.MUTATING);
        assertThat(observedTransactions).containsOnly(false);
        verify(documents).saveChunksAndMarkIndexing(DOCUMENT, BATCH);
        verify(documents).markIndexed(DOCUMENT_ID);
        verify(documents, never()).markFailed(anyLong(), anyString());
        assertThat(requests).extracting(Request::method).containsExactly("POST", "POST");
        assertThat(JSON.readTree(requests.get(0).body())).isEqualTo(JSON.readTree(JSON.writeValueAsBytes(Map.of(
                "document_id", DOCUMENT_ID, "text", SOURCE, "title", TITLE))));
        var embedded = JSON.readTree(requests.get(1).body());
        assertThat(embedded.path("document_id").longValue()).isEqualTo(DOCUMENT_ID);
        assertThat(embedded.path("chunks").size()).isEqualTo(1);
        assertThat(embedded.path("chunks").get(0).path("chunk_id").longValue()).isEqualTo(101);
        assertThat(embedded.path("chunks").get(0).path("tags")).isEqualTo(JSON.valueToTree(TAGS));
    }

    @Test
    void continuesTheMutationLeaseReceivedFromDocumentUpdateUntilIndexingCommits() {
        var lease = gate.tryAcquire(Operation.MUTATION).lease().orElseThrow();
        assertThat(gate.tryAcquire(Operation.QUERY).lease()).isEmpty();

        var result = service.index(DOCUMENT_ID, lease);

        assertThat(result).isNotNull();
        assertThat(result.outcome()).isEqualTo(Outcome.INDEXED);
        assertThat(observedStates).containsOnly(State.MUTATING);
        assertThat(calls).containsExactly(Stage.LOAD_DOCUMENT, Stage.CHUNK, Stage.SAVE_CHUNKS,
                Stage.EMBED, Stage.MARK_INDEXED);
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @Test
    void handedOffMutationUsesTheSameFailedIndexCleanupAndRecoveryRules() {
        var lease = gate.tryAcquire(Operation.MUTATION).lease().orElseThrow();
        embedReply = Reply.json(503,
                "{\"detail\":{\"error\":\"EMBEDDING_UNAVAILABLE\",\"cause\":\"RuntimeError\"}}");

        var result = service.index(DOCUMENT_ID, lease);

        assertThat(result).isNotNull();
        assertThat(result.outcome()).isEqualTo(Outcome.FAILED);
        assertThat(result.recoveryRequired()).isFalse();
        assertThat(result.error()).contains("stage=EMBED", "cleanup=OK");
        verify(documents).markFailed(eq(DOCUMENT_ID), anyString());
        assertThat(calls).endsWith(Stage.DELETE_INDEX, Stage.MARK_FAILED);
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @Test
    void sendsMoreThan64ChunksInOneEmbedRequest() {
        String text = "x".repeat(65);
        var document = new DocumentIndexRepository.PendingDocument(DOCUMENT_ID, text, TITLE, TAGS);
        var batch = new ChunkBatch(IntStream.range(0, 65).mapToObj(sequence ->
                new ChunkBatch.Chunk(sequence, "x", sequence, sequence + 1, "", 1)).toList());
        var chunks = IntStream.range(0, 65).mapToObj(sequence -> new ChunkRepository.StoredChunk(
                100 + sequence, DOCUMENT_ID, sequence, "x", sequence, sequence + 1, "", 1)).toList();
        when(documents.findPending(DOCUMENT_ID)).thenReturn(Optional.of(document));
        when(documents.saveChunksAndMarkIndexing(document, batch)).thenReturn(chunks);
        chunkReply = Reply.json(200, chunkResponse(batch));
        embedReply = Reply.json(200, "{\"indexed\":65}");

        IndexingResult result = service.index(DOCUMENT_ID);

        assertThat(result.outcome()).isEqualTo(Outcome.INDEXED);
        assertThat(requests).hasSize(2);
        assertThat(JSON.readTree(requests.get(1).body()).path("chunks").size()).isEqualTo(65);
        verify(documents).markIndexed(DOCUMENT_ID);
    }

    @Test
    void skipsIneligibleDocumentsWithoutCallingPythonOrChangingState() {
        when(documents.findPending(DOCUMENT_ID)).thenReturn(Optional.empty());

        IndexingResult result = service.index(DOCUMENT_ID);

        assertThat(result.outcome()).isEqualTo(Outcome.SKIPPED);
        assertThat(result.recoveryRequired()).isFalse();
        assertThat(result.timings()).extracting(StageTiming::stage).containsExactly(Stage.LOAD_DOCUMENT);
        assertThat(requests).isEmpty();
        verify(documents, never()).saveChunksAndMarkIndexing(any(), any());
        verify(documents, never()).markIndexed(anyLong());
        verify(documents, never()).markFailed(anyLong(), anyString());
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @ParameterizedTest
    @EnumSource(value = State.class, names = {"QUERYING", "MUTATING", "RECOVERING", "RECOVERY_REQUIRED"})
    void busyAdmissionHasNoDatabaseOrHttpEffects(State state) {
        RagOperationGate.Lease held = null;
        if (state == State.RECOVERY_REQUIRED) {
            gate = new RagOperationGate();
            service = serviceWithEmbedTimeout(5000);
        } else {
            Operation operation = switch (state) {
                case QUERYING -> Operation.QUERY;
                case MUTATING -> Operation.MUTATION;
                default -> Operation.RECOVERY;
            };
            held = gate.tryAcquire(operation).lease().orElseThrow();
        }
        try {
            IndexingResult result = service.index(DOCUMENT_ID);

            assertThat(result.outcome()).isEqualTo(Outcome.BUSY);
            assertThat(result.recoveryRequired()).isEqualTo(state == State.RECOVERY_REQUIRED || state == State.RECOVERING);
            assertThat(result.timings()).isEmpty();
            assertThat(gate.state()).isEqualTo(state);
            assertThat(requests).isEmpty();
            verifyNoInteractions(documents);
        } finally {
            if (held != null) {
                held.close();
            }
        }
    }

    @ParameterizedTest
    @ValueSource(longs = {0, -1, Long.MIN_VALUE})
    void invalidIdsAreRejectedBeforeAdmissionOrIo(long documentId) {
        assertThatThrownBy(() -> service.index(documentId)).isInstanceOf(IllegalArgumentException.class);

        verifyNoInteractions(documents);
        assertThat(requests).isEmpty();
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @Test
    void activeDatabaseTransactionIsRejectedBeforeAdmissionOrIo() {
        TransactionSynchronizationManager.setActualTransactionActive(true);
        try {
            assertThatThrownBy(() -> service.index(DOCUMENT_ID)).isInstanceOf(IllegalStateException.class);
        } finally {
            TransactionSynchronizationManager.setActualTransactionActive(false);
        }

        verifyNoInteractions(documents);
        assertThat(requests).isEmpty();
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @Test
    void unreadableEligibilityPausesWithoutDeletingAnUnverifiedDocument() {
        when(documents.findPending(DOCUMENT_ID)).thenThrow(new DataAccessResourceFailureException("private SQL"));

        IndexingResult result = service.index(DOCUMENT_ID);

        assertFailed(result, true);
        assertThat(result.error()).contains("LOAD_DOCUMENT", "DataAccessResourceFailureException").doesNotContain("private SQL");
        assertThat(requests).isEmpty();
        verify(documents, never()).markFailed(anyLong(), anyString());
    }

    @ParameterizedTest
    @EnumSource(value = Stage.class, names = {"CHUNK", "SAVE_CHUNKS"})
    void preEmbedFailureCleansOnceAndResumesAfterFailedStateReturns(Stage stage) {
        if (stage == Stage.CHUNK) {
            chunkReply = Reply.json(503, "{\"detail\":{\"error\":\"CHUNKING_UNAVAILABLE\",\"cause\":\"ValueError\"}}");
        } else {
            when(documents.saveChunksAndMarkIndexing(any(), any())).thenThrow(new IllegalStateException("private SQL"));
        }

        IndexingResult result = service.index(DOCUMENT_ID);

        assertFailed(result, false);
        assertThat(result.error()).contains("stage=" + stage, "cleanup=OK");
        assertThat(requests).extracting(Request::path).containsExactly("/chunk", "/index/42");
        assertThat(requests.get(1).method()).isEqualTo("DELETE");
        verify(documents).markFailed(eq(DOCUMENT_ID), eq(result.error()));
        verify(documents, never()).markIndexed(anyLong());
    }

    @ParameterizedTest
    @MethodSource("completedEmbedErrors")
    void declaredCompletedEmbedFailureResumesOnlyAfterIndependentCleanup(Reply reply, String errorCode) {
        embedReply = reply;

        IndexingResult result = service.index(DOCUMENT_ID);

        assertFailed(result, false);
        assertThat(result.error()).contains("stage=EMBED", errorCode, "cleanup=OK");
        assertThat(requests).extracting(Request::path).containsExactly("/chunk", "/embed", "/index/42");
        verify(documents).markFailed(eq(DOCUMENT_ID), eq(result.error()));
        verify(documents, never()).markIndexed(anyLong());
    }

    @ParameterizedTest
    @MethodSource("unknownEmbedErrors")
    void unconfirmedEmbedResponseNeverResumesEvenIfDeleteSucceeds(Reply reply) {
        embedReply = reply;

        IndexingResult result = service.index(DOCUMENT_ID);

        assertFailed(result, true);
        assertThat(result.error()).contains("stage=EMBED", "cleanup=OK");
        assertThat(requests).extracting(Request::path).containsExactly("/chunk", "/embed", "/index/42");
        verify(documents).markFailed(eq(DOCUMENT_ID), eq(result.error()));
        verify(documents, never()).markIndexed(anyLong());
    }

    @Test
    void failedIndependentCleanupPreservesBothPythonAndCleanupCauses() {
        embedReply = Reply.json(503, "{\"detail\":{\"error\":\"INDEX_WRITE_FAILED\",\"cause\":\"ValueError\",\"cleanup_error\":\"RuntimeError\"}}");
        deleteReply = Reply.json(503, "{\"detail\":{\"error\":\"INDEX_UNAVAILABLE\",\"cause\":\"LookupError\"}}");

        IndexingResult result = service.index(DOCUMENT_ID);

        assertFailed(result, true);
        assertThat(result.error()).contains("INDEX_WRITE_FAILED", "cause=ValueError", "python_cleanup_error=RuntimeError",
                "cleanup=", "INDEX_UNAVAILABLE", "cause=LookupError");
        assertThat(requests).hasSize(3);
        verify(documents, times(1)).markFailed(eq(DOCUMENT_ID), eq(result.error()));
    }

    @Test
    void failedCleanupAfterPureChunkFailureAlsoRequiresRecovery() {
        chunkReply = Reply.json(500, "{}");
        deleteReply = Reply.json(200, "{\"removed\":-1}");

        IndexingResult result = service.index(DOCUMENT_ID);

        assertFailed(result, true);
        assertThat(result.error()).contains("stage=CHUNK", "cleanup=");
        assertThat(requests).extracting(Request::path).containsExactly("/chunk", "/index/42");
        verify(documents).markFailed(eq(DOCUMENT_ID), eq(result.error()));
    }

    @Test
    void failedIndexedCommitPausesEvenWhenCleanupAndFailedRecordingReturn() {
        doThrow(new DataAccessResourceFailureException("commit result unknown")).when(documents).markIndexed(DOCUMENT_ID);

        IndexingResult result = service.index(DOCUMENT_ID);

        assertFailed(result, true);
        assertThat(result.error()).contains("stage=MARK_INDEXED", "DataAccessResourceFailureException", "cleanup=OK");
        assertThat(requests).extracting(Request::path).containsExactly("/chunk", "/embed", "/index/42");
        verify(documents).markFailed(eq(DOCUMENT_ID), eq(result.error()));
    }

    @Test
    void failedFailureRecordingDoesNotHideTheInitialErrorOrReopenTheGate() {
        embedReply = Reply.json(503, "{\"detail\":{\"error\":\"EMBEDDING_UNAVAILABLE\",\"cause\":\"ReadTimeout\"}}");
        doThrow(new DataAccessResourceFailureException("private database detail"))
                .when(documents).markFailed(eq(DOCUMENT_ID), anyString());

        IndexingResult result = service.index(DOCUMENT_ID);

        assertFailed(result, true);
        assertThat(result.error()).contains("stage=EMBED", "EMBEDDING_UNAVAILABLE", "cause=ReadTimeout",
                "cleanup=OK", "persistence=DataAccessResourceFailureException").doesNotContain("private database detail");
        assertThat(requests).hasSize(3);
        verify(documents, times(1)).markFailed(eq(DOCUMENT_ID), anyString());
    }

    @Test
    void timingsAreImmutableOrderedAndExcludeImplicitRetries() {
        IndexingResult result = service.index(DOCUMENT_ID);

        assertThat(result.outcome()).isEqualTo(Outcome.INDEXED);
        assertThat(result.timings()).extracting(StageTiming::stage).containsExactly(
                Stage.LOAD_DOCUMENT, Stage.CHUNK, Stage.SAVE_CHUNKS, Stage.EMBED, Stage.MARK_INDEXED);
        assertThat(result.timings()).allSatisfy(timing -> {
            assertThat(timing.durationNanos()).isNotNegative();
            assertThat(timing.successful()).isTrue();
        });
        assertThat(result.durationNanos()).isGreaterThanOrEqualTo(result.timings().stream().mapToLong(StageTiming::durationNanos).sum());
        assertThatThrownBy(() -> result.timings().clear()).isInstanceOf(UnsupportedOperationException.class);
    }

    @Test
    void resultCopiesTheTimingList() {
        List<StageTiming> timings = new ArrayList<>(List.of(new StageTiming(Stage.CHUNK, 1, true)));
        IndexingResult result = new IndexingResult(DOCUMENT_ID, Outcome.FAILED, true, "failure", timings, 1);

        timings.clear();

        assertThat(result.timings()).hasSize(1);
        assertThatThrownBy(() -> result.timings().clear()).isInstanceOf(UnsupportedOperationException.class);
    }

    @Test
    void diagnosticsDoNotPersistOrLogResponseBodiesExceptionMessagesOrSourceText() {
        String secret = "Bearer api-secret-" + "😀".repeat(1200);
        embedReply = Reply.json(503, "{\"detail\":{\"error\":\"EMBEDDING_UNAVAILABLE\",\"cause\":"
                + JSON.writeValueAsString(secret) + "}}");
        doThrow(new DataAccessResourceFailureException("password=private-password"))
                .when(documents).markFailed(eq(DOCUMENT_ID), anyString());

        IndexingResult result = service.index(DOCUMENT_ID);

        assertFailed(result, true);
        assertThat(result.error().codePointCount(0, result.error().length())).isLessThanOrEqualTo(1024);
        assertThat(result.error()).doesNotContain("api-secret", "private-password", SOURCE, TITLE);
        assertThat(logEvents.list).hasSize(1);
        ILoggingEvent event = logEvents.list.get(0);
        assertThat(event.getThrowableProxy()).isNull();
        Map<String, String> fields = event.getKeyValuePairs().stream().collect(Collectors.toMap(
                pair -> pair.key, pair -> String.valueOf(pair.value)));
        assertThat(fields).containsEntry("document_id", "42").containsEntry("outcome", "FAILED")
                .containsEntry("recovery_required", "true").containsKeys("duration_ns", "stages", "error");
        assertThat(fields.toString()).doesNotContain("api-secret", "private-password", SOURCE, TITLE);
        verify(documents).markFailed(eq(DOCUMENT_ID), org.mockito.ArgumentMatchers.argThat(error ->
                error.codePointCount(0, error.length()) <= 1024 && !error.contains("api-secret")));
    }

    @ParameterizedTest
    @EnumSource(Stage.class)
    void holdsAdmissionUntilInFlightStageAndFinalStateHaveReturned(Stage stage) throws Exception {
        blockedStage = stage;
        boolean failurePath = stage == Stage.DELETE_INDEX || stage == Stage.MARK_FAILED;
        if (failurePath) {
            embedReply = Reply.json(503, "{\"detail\":{\"error\":\"EMBEDDING_UNAVAILABLE\",\"cause\":\"ReadTimeout\"}}");
        }
        ExecutorService worker = Executors.newSingleThreadExecutor();
        try {
            Future<IndexingResult> result = worker.submit(() -> {
                try {
                    return service.index(DOCUMENT_ID);
                } finally {
                    workerProgress.countDown();
                }
            });
            assertTrue(workerProgress.await(5, TimeUnit.SECONDS));
            assertThat(phaseEntered.getCount()).isZero();
            assertThat(result.isDone()).isFalse();
            assertThat(gate.state()).isEqualTo(State.MUTATING);
            assertThat(gate.tryAcquire(Operation.QUERY).lease()).isEmpty();
            int requestCount = requests.size();

            IndexingResult busy = service.index(99);

            assertThat(busy.outcome()).isEqualTo(Outcome.BUSY);
            assertThat(requests).hasSize(requestCount);
            verify(documents, never()).findPending(99);
            releasePhase.countDown();
            IndexingResult finished = result.get(5, TimeUnit.SECONDS);
            assertThat(finished.outcome()).isEqualTo(failurePath ? Outcome.FAILED : Outcome.INDEXED);
            assertThat(finished.recoveryRequired()).isFalse();
            assertThat(gate.state()).isEqualTo(State.READY);
        } finally {
            releasePhase.countDown();
            worker.shutdownNow();
            assertTrue(worker.awaitTermination(5, TimeUnit.SECONDS));
        }
    }

    @ParameterizedTest
    @EnumSource(value = Stage.class, names = {"EMBED", "DELETE_INDEX"})
    void lateMutationAfterTimeoutDoesNotReopenQueriesOrStartAnotherDocument(Stage stage) throws Exception {
        blockedStage = stage;
        simulateLateWrite.set(true);
        if (stage == Stage.DELETE_INDEX) {
            embedReply = Reply.json(503, "{\"detail\":{\"error\":\"EMBEDDING_UNAVAILABLE\",\"cause\":\"ReadTimeout\"}}");
        }
        service = serviceWithTimeouts(stage == Stage.EMBED ? 250 : 5000, stage == Stage.DELETE_INDEX ? 250 : 5000);
        ExecutorService worker = Executors.newSingleThreadExecutor();
        try {
            Future<IndexingResult> result = worker.submit(() -> {
                try {
                    return service.index(DOCUMENT_ID);
                } finally {
                    workerProgress.countDown();
                }
            });
            assertTrue(workerProgress.await(5, TimeUnit.SECONDS));
            assertThat(phaseEntered.getCount()).isZero();

            IndexingResult timedOut = result.get(5, TimeUnit.SECONDS);

            assertFailed(timedOut, true);
            assertThat(releasePhase.getCount()).isEqualTo(1);
            assertThat(lateMutationWritten.getCount()).isEqualTo(1);
            assertThat(requests).extracting(Request::path).containsExactly("/chunk", "/embed", "/index/42");
            verify(documents).markFailed(eq(DOCUMENT_ID), anyString());
            releasePhase.countDown();
            assertTrue(lateMutationWritten.await(5, TimeUnit.SECONDS));
            assertThat(gate.state()).isEqualTo(State.RECOVERY_REQUIRED);
            assertThat(gate.tryAcquire(Operation.QUERY).lease()).isEmpty();
            assertThat(service.index(99).outcome()).isEqualTo(Outcome.BUSY);
            verify(documents, never()).findPending(99);
            assertThat(requests).hasSize(3);
        } finally {
            releasePhase.countDown();
            worker.shutdownNow();
            assertTrue(worker.awaitTermination(5, TimeUnit.SECONDS));
        }
    }

    @Test
    void exceptionalExitWithoutConfirmedCompletionClosesTheGate() {
        when(documents.saveChunksAndMarkIndexing(any(), any())).thenThrow(new AssertionError("forced fatal exit"));

        assertThatThrownBy(() -> service.index(DOCUMENT_ID)).isInstanceOf(AssertionError.class);

        assertThat(gate.state()).isEqualTo(State.RECOVERY_REQUIRED);
        assertThat(requests).extracting(Request::path).containsExactly("/chunk");
    }

    @Test
    void failedStagesAreRecordedWithoutCountingHiddenRetries() {
        embedReply = Reply.json(503, "{\"detail\":{\"error\":\"EMBEDDING_UNAVAILABLE\",\"cause\":\"ReadTimeout\"}}");
        deleteReply = Reply.json(503, "{}");

        IndexingResult result = service.index(DOCUMENT_ID);

        assertFailed(result, true);
        assertThat(result.timings()).extracting(StageTiming::stage).containsExactly(Stage.LOAD_DOCUMENT,
                Stage.CHUNK, Stage.SAVE_CHUNKS, Stage.EMBED, Stage.DELETE_INDEX, Stage.MARK_FAILED);
        assertThat(result.timings()).extracting(StageTiming::successful).containsExactly(true, true, true, false, false, true);
        assertThat(requests).hasSize(3);
    }

    private void assertFailed(IndexingResult result, boolean requiresRecovery) {
        assertThat(result.outcome()).isEqualTo(Outcome.FAILED);
        assertThat(result.recoveryRequired()).isEqualTo(requiresRecovery);
        assertThat(gate.state()).isEqualTo(requiresRecovery ? State.RECOVERY_REQUIRED : State.READY);
    }

    private RagOperationGate readyGate() {
        RagOperationGate ready = new RagOperationGate();
        try (var recovery = ready.tryAcquire(Operation.RECOVERY).lease().orElseThrow()) {
            assertTrue(recovery.confirmCompletion());
        }
        return ready;
    }

    private DocumentIndexingService serviceWithEmbedTimeout(int timeoutMillis) {
        return serviceWithTimeouts(timeoutMillis, 5000);
    }

    private DocumentIndexingService serviceWithTimeouts(int embedTimeoutMillis, int deleteTimeoutMillis) {
        String baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
        return new DocumentIndexingService(gate, documents, new ChunkClient(baseUrl, 1000, 5000),
                new EmbedClient(baseUrl, 1000, embedTimeoutMillis), new IndexClient(baseUrl, 1000, deleteTimeoutMillis));
    }

    private void reached(Stage stage) {
        calls.add(stage);
        observedStates.add(gate.state());
        observedTransactions.add(TransactionSynchronizationManager.isActualTransactionActive());
        if (stage == blockedStage) {
            phaseEntered.countDown();
            workerProgress.countDown();
            try {
                assertTrue(releasePhase.await(5, TimeUnit.SECONDS));
            } catch (InterruptedException failure) {
                Thread.currentThread().interrupt();
                throw new IllegalStateException(failure);
            }
        }
    }

    private void serve(HttpExchange exchange, Stage stage, Supplier<Reply> response) throws IOException {
        try {
            requests.add(new Request(exchange.getRequestMethod(), exchange.getRequestURI().getPath(),
                    exchange.getRequestBody().readAllBytes()));
            reached(stage);
            if (stage == blockedStage && simulateLateWrite.get()) {
                lateMutationWritten.countDown();
            }
            Reply reply = response.get();
            if (reply.contentType() != null) {
                exchange.getResponseHeaders().set("Content-Type", reply.contentType());
            }
            if (reply.contentEncoding() != null) {
                exchange.getResponseHeaders().set("Content-Encoding", reply.contentEncoding());
            }
            byte[] body = reply.body().getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(reply.status(), body.length == 0 ? -1 : body.length);
            if (body.length > 0) {
                exchange.getResponseBody().write(body);
            }
        } finally {
            exchange.close();
        }
    }

    private static String chunkResponse(ChunkBatch batch) {
        return JSON.writeValueAsString(Map.of("chunks", batch.chunks().stream().map(chunk -> Map.of(
                "seq", chunk.seq(), "text", chunk.text(), "byte_start", chunk.byteStart(), "byte_end", chunk.byteEnd(),
                "heading_path", chunk.headingPath(), "token_count", chunk.tokenCount())).toList()));
    }

    private static Stream<Arguments> completedEmbedErrors() {
        return Stream.of(
                Arguments.of(Reply.json(409, "{\"detail\":{\"error\":\"CHUNK_ID_CONFLICT\"}}"), "CHUNK_ID_CONFLICT"),
                Arguments.of(Reply.json(503, "{\"detail\":{\"error\":\"INDEX_UNAVAILABLE\",\"cause\":\"RuntimeError\"}}"), "INDEX_UNAVAILABLE"),
                Arguments.of(Reply.json(503, "{\"detail\":{\"error\":\"EMBEDDING_UNAVAILABLE\",\"cause\":\"ReadTimeout\"}}"), "EMBEDDING_UNAVAILABLE"),
                Arguments.of(Reply.json(503, "{\"detail\":{\"error\":\"INDEX_WRITE_FAILED\",\"cause\":\"ValueError\",\"cleanup_error\":null}}"), "INDEX_WRITE_FAILED"),
                Arguments.of(Reply.json(503, "{\"detail\":{\"error\":\"INDEX_WRITE_FAILED\",\"cause\":\"ValueError\",\"cleanup_error\":\"RuntimeError\"}}"), "INDEX_WRITE_FAILED"));
    }

    private static Stream<Reply> unknownEmbedErrors() {
        String declared = "{\"detail\":{\"error\":\"EMBEDDING_UNAVAILABLE\",\"cause\":\"ReadTimeout\"}}";
        return Stream.of(Reply.json(504, declared), Reply.json(503, "{}"), Reply.json(503, ""),
                Reply.json(503, "{\"detail\":{\"error\":\"UNKNOWN_ERROR\",\"cause\":\"ReadTimeout\"}}"),
                Reply.json(503, "{\"detail\":{\"error\":\"EMBEDDING_UNAVAILABLE\"}}"),
                Reply.json(503, "{\"detail\":{\"error\":\"INDEX_WRITE_FAILED\",\"cause\":\"ValueError\"}}"),
                Reply.json(503, "{\"detail\":{\"error\":\"INDEX_WRITE_FAILED\",\"cause\":\"ValueError\",\"cleanup_error\":false}}"),
                Reply.json(503, declared + "{}"),
                Reply.json(503, "{\"detail\":{\"error\":\"EMBEDDING_UNAVAILABLE\",\"cause\":\"ReadTimeout\"},\"extra\":true}"),
                Reply.json(503, "{\"detail\":{\"error\":\"EMBEDDING_UNAVAILABLE\",\"cause\":\"ReadTimeout\",\"extra\":true}}"),
                Reply.json(503, "{\"detail\":{\"error\":\"UNKNOWN_ERROR\",\"error\":\"EMBEDDING_UNAVAILABLE\",\"cause\":\"ReadTimeout\"}}"),
                Reply.json(200, "{\"indexed\":2}"), Reply.json(200, "not JSON"),
                new Reply(503, "text/plain", null, declared),
                new Reply(503, "application/json", "gzip", declared),
                new Reply(503, null, null, declared));
    }

    private record Request(String method, String path, byte[] body) {}

    private record Reply(int status, String contentType, String contentEncoding, String body) {
        private static Reply json(int status, String body) {
            return new Reply(status, "application/json", null, body);
        }
    }
}
