package com.easyrag.server.document;

import com.easyrag.server.rag.RagOperationGate;
import com.easyrag.server.rag.RagOperationGate.Operation;
import com.easyrag.server.rag.RagOperationGate.State;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.web.client.RestClientResponseException;
import tools.jackson.core.JacksonException;
import tools.jackson.core.StreamReadFeature;
import tools.jackson.databind.DeserializationFeature;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.function.Supplier;

@Service
public class DocumentIndexingService {

    private static final Logger LOGGER = LoggerFactory.getLogger(DocumentIndexingService.class);
    private static final JsonMapper JSON = JsonMapper.builder()
            .enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
            .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
            .build();
    private final RagOperationGate gate;
    private final DocumentIndexRepository documents;
    private final ChunkClient chunkClient;
    private final EmbedClient embedClient;
    private final IndexClient indexClient;

    public DocumentIndexingService(RagOperationGate gate, DocumentIndexRepository documents,
                                   ChunkClient chunkClient, EmbedClient embedClient, IndexClient indexClient) {
        this.gate = gate;
        this.documents = documents;
        this.chunkClient = chunkClient;
        this.embedClient = embedClient;
        this.indexClient = indexClient;
    }

    public IndexingResult index(long documentId) {
        validateInvocation(documentId);
        ExecutionTrace trace = new ExecutionTrace();
        var admission = gate.tryAcquire(Operation.MUTATION);
        if (admission.lease().isEmpty()) {
            return trace.finish(documentId, Outcome.BUSY,
                    admission.state() == State.RECOVERY_REQUIRED || admission.state() == State.RECOVERING, null);
        }
        return index(documentId, admission.lease().orElseThrow(), trace);
    }

    /** 资料更新/手动重索引已获取许可，后台沿用它，不在提交与执行之间开放查询。 */
    IndexingResult index(long documentId, RagOperationGate.Lease lease) {
        validateInvocation(documentId);
        return index(documentId, lease, new ExecutionTrace());
    }

    private IndexingResult index(long documentId, RagOperationGate.Lease lease, ExecutionTrace trace) {
        try (lease) {
            Optional<DocumentIndexRepository.PendingDocument> pending;
            try {
                pending = trace.measure(Stage.LOAD_DOCUMENT, () -> documents.findPending(documentId));
            } catch (RuntimeException failure) {
                return trace.finish(documentId, Outcome.FAILED, true,
                        "stage=LOAD_DOCUMENT; failure=" + limited(describe(failure).summary(), 400));
            }
            if (pending.isEmpty()) {
                lease.confirmCompletion();
                return trace.finish(documentId, Outcome.SKIPPED, false, null);
            }
            var document = pending.orElseThrow();
            Stage stage = Stage.CHUNK;
            try {
                ChunkBatch batch = trace.measure(Stage.CHUNK,
                        () -> chunkClient.chunk(documentId, document.content(), document.title()));
                stage = Stage.SAVE_CHUNKS;
                var chunks = trace.measure(Stage.SAVE_CHUNKS,
                        () -> documents.saveChunksAndMarkIndexing(document, batch));
                stage = Stage.EMBED;
                trace.measure(Stage.EMBED, () -> embedClient.embed(documentId, chunks, document.tags()));
                stage = Stage.MARK_INDEXED;
                trace.measure(Stage.MARK_INDEXED, () -> {
                    documents.markIndexed(documentId);
                    return null;
                });
                lease.confirmCompletion();
                return trace.finish(documentId, Outcome.INDEXED, false, null);
            } catch (RuntimeException failure) {
                return finishFailure(documentId, stage, failure, lease, trace);
            }
        }
    }

    private static void validateInvocation(long documentId) {
        if (documentId <= 0) {
            throw new IllegalArgumentException("document_id must be positive");
        }
        if (TransactionSynchronizationManager.isActualTransactionActive()) {
            throw new IllegalStateException("document indexing cannot run inside a database transaction");
        }
    }

    private IndexingResult finishFailure(long documentId, Stage stage, RuntimeException failure,
                                         RagOperationGate.Lease lease, ExecutionTrace trace) {
        FailureInfo original = describe(failure);
        boolean confirmed = stage == Stage.CHUNK || stage == Stage.SAVE_CHUNKS
                || stage == Stage.EMBED && original.completedEmbed();
        String cleanup = "OK";
        try {
            trace.measure(Stage.DELETE_INDEX, () -> indexClient.deleteDocument(documentId));
        } catch (RuntimeException cleanupFailure) {
            confirmed = false;
            cleanup = describe(cleanupFailure).summary();
        }
        String error = "stage=" + stage + "; failure=" + limited(original.summary(), 400)
                + "; cleanup=" + limited(cleanup, 300);
        String storedError = error;
        try {
            trace.measure(Stage.MARK_FAILED, () -> {
                documents.markFailed(documentId, storedError);
                return null;
            });
        } catch (RuntimeException persistenceFailure) {
            confirmed = false;
            error += "; persistence=" + limited(describe(persistenceFailure).summary(), 200);
        }
        if (confirmed) {
            lease.confirmCompletion();
        }
        return trace.finish(documentId, Outcome.FAILED, !confirmed, error);
    }

    private static FailureInfo describe(RuntimeException failure) {
        String basic = limited(failure.getClass().getSimpleName(), 64);
        if (!(failure instanceof RestClientResponseException response)) {
            if (failure.getCause() != null) {
                basic += "/" + limited(failure.getCause().getClass().getSimpleName(), 64);
            }
            return new FailureInfo(basic, false);
        }
        int status = response.getStatusCode().value();
        basic += "(http=" + status + ")";
        try {
            HttpHeaders headers = response.getResponseHeaders();
            if (headers == null || headers.getContentType() == null
                    || !MediaType.APPLICATION_JSON.equalsTypeAndSubtype(headers.getContentType())) {
                return new FailureInfo(basic, false);
            }
            List<String> encodings = headers.get(HttpHeaders.CONTENT_ENCODING);
            if (encodings != null && encodings.stream().anyMatch(encoding -> !encoding.trim().equalsIgnoreCase("identity"))) {
                return new FailureInfo(basic, false);
            }
            JsonNode root = JSON.readTree(response.getResponseBodyAsByteArray());
            JsonNode detail = root.path("detail");
            String code = identifier(detail.get("error"));
            if (!detail.isObject() || code == null) {
                return new FailureInfo(basic, false);
            }
            String cause = identifier(detail.get("cause"));
            JsonNode cleanupNode = detail.get("cleanup_error");
            String cleanup = identifier(cleanupNode);
            String summary = basic + "; error=" + limited(code, 64);
            if (cause != null) {
                summary += "; cause=" + limited(cause, 64);
            }
            if (cleanupNode != null && cleanupNode.isNull()) {
                summary += "; python_cleanup_error=none";
            } else if (cleanup != null) {
                summary += "; python_cleanup_error=" + limited(cleanup, 64);
            }
            boolean completed = root.isObject() && root.size() == 1 && switch (code) {
                case "CHUNK_ID_CONFLICT" -> status == 409 && detail.size() == 1;
                case "INDEX_UNAVAILABLE", "EMBEDDING_UNAVAILABLE" -> status == 503
                        && detail.size() == 2 && cause != null;
                case "INDEX_WRITE_FAILED" -> status == 503 && detail.size() == 3 && cause != null
                        && cleanupNode != null && (cleanupNode.isNull() || cleanup != null);
                default -> false;
            };
            return new FailureInfo(summary, completed);
        } catch (JacksonException | IllegalArgumentException invalidResponse) {
            return new FailureInfo(basic, false);
        }
    }

    private static String identifier(JsonNode value) {
        if (value == null || !value.isString() || !value.stringValue().matches("[A-Za-z_][A-Za-z0-9_]*")) {
            return null;
        }
        return value.stringValue();
    }

    private static String limited(String value, int maximumCodePoints) {
        if (value.codePointCount(0, value.length()) <= maximumCodePoints) {
            return value;
        }
        return value.substring(0, value.offsetByCodePoints(0, maximumCodePoints - 1)) + "…";
    }

    public enum Outcome {
        INDEXED, SKIPPED, BUSY, FAILED
    }

    public enum Stage {
        LOAD_DOCUMENT, CHUNK, SAVE_CHUNKS, EMBED, MARK_INDEXED, DELETE_INDEX, MARK_FAILED
    }

    public record StageTiming(Stage stage, long durationNanos, boolean successful) {}

    public record IndexingResult(long documentId, Outcome outcome, boolean recoveryRequired, String error,
                                 List<StageTiming> timings, long durationNanos) {
        public IndexingResult {
            timings = List.copyOf(timings);
        }
    }

    private record FailureInfo(String summary, boolean completedEmbed) {}

    private static final class ExecutionTrace {
        private final long startedAt = System.nanoTime();
        private final List<StageTiming> timings = new ArrayList<>();

        private <Value> Value measure(Stage stage, Supplier<Value> action) {
            long started = System.nanoTime();
            boolean successful = false;
            try {
                Value value = action.get();
                successful = true;
                return value;
            } finally {
                timings.add(new StageTiming(stage, System.nanoTime() - started, successful));
            }
        }

        private IndexingResult finish(long documentId, Outcome outcome, boolean recoveryRequired, String error) {
            var result = new IndexingResult(documentId, outcome, recoveryRequired, error, timings,
                    System.nanoTime() - startedAt);
            var event = outcome == Outcome.FAILED ? LOGGER.atWarn() : LOGGER.atInfo();
            event.addKeyValue("document_id", documentId).addKeyValue("outcome", outcome)
                    .addKeyValue("recovery_required", recoveryRequired).addKeyValue("error", error)
                    .addKeyValue("duration_ns", result.durationNanos()).addKeyValue("stages", result.timings())
                    .log("document_indexing");
            return result;
        }
    }
}
