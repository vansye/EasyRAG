package com.easyrag.server.document;

import com.easyrag.server.ServerApplication;
import com.easyrag.server.document.DocumentIndexingService.IndexingResult;
import com.easyrag.server.document.DocumentIndexingService.Outcome;
import com.easyrag.server.document.DocumentIndexingService.Stage;
import com.easyrag.server.document.DocumentIndexingService.StageTiming;
import com.easyrag.server.rag.RagOperationGate;
import com.easyrag.server.rag.RagOperationGate.Operation;
import com.easyrag.server.rag.RagOperationGate.State;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;
import org.springframework.boot.Banner;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.WebApplicationType;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.sql.Statement;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.function.Predicate;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertTrue;

@Tag("requires-mysql")
@Tag("requires-ollama")
@Timeout(180)
class DocumentIndexingIT {

    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final String TITLE = "真实链路联调😀";
    private static final List<String> TAGS = List.of("联调", "emoji😀", " spaced ");
    private static final String SOURCE = "# 联调资料😀\r\n\r\n## 收录\r\n"
            + "资料收录后先保存原文，再生成切片和向量。\r\n".repeat(8)
            + "\r\n## 溯源\r\n"
            + "回答必须引用原始片段，字节偏移不能拆开中文和表情😀。\r\n".repeat(8)
            + "\r\n## 同步\r\n"
            + "资料变更未确认完成时，问答入口保持暂停，避免读取旧片段。\r\n".repeat(8)
            + "组合字符 e\u0301 与原始换行均不得规范化。\r\n";

    @Test
    void indexesRealPythonChunksAndVectorsWithCommittedMysqlState() throws Exception {
        try (var run = IntegrationRun.start(90000)) {
            long documentId = run.createDocument();
            Map<String, Object> original = run.document(documentId);

            IndexingResult result = run.service.index(documentId);

            assertThat(result.outcome()).isEqualTo(Outcome.INDEXED);
            assertThat(result.recoveryRequired()).isFalse();
            assertThat(result.error()).isNull();
            assertThat(result.timings()).extracting(StageTiming::stage).containsExactly(
                    Stage.LOAD_DOCUMENT, Stage.CHUNK, Stage.SAVE_CHUNKS, Stage.EMBED, Stage.MARK_INDEXED);
            assertThat(result.timings()).allSatisfy(timing -> {
                assertThat(timing.successful()).isTrue();
                assertThat(timing.durationNanos()).isPositive();
            });
            var chunks = run.chunks(documentId);
            assertThat(chunks.size()).isBetween(3, 64);
            assertThat(run.document(documentId)).containsEntry("index_status", "INDEXED")
                    .containsEntry("index_error", null).containsEntry("chunk_count", chunks.size());
            assertThat(run.document(documentId).get("indexed_at")).isNotNull();
            for (String field : List.of("content", "content_hash", "title", "tags", "created_at", "updated_at")) {
                assertThat(run.document(documentId).get(field)).isEqualTo(original.get(field));
            }
            assertStoredChunksAndVectors(run, documentId, chunks);
            JsonNode state = run.state();
            assertThat(requestPaths(state)).containsExactly("POST /chunk", "POST /embed");
            assertThat(state.path("provider_requests").size()).isEqualTo(1);
            JsonNode providerRequest = state.path("provider_requests").get(0);
            assertThat(providerRequest.path("model").stringValue()).isEqualTo("bge-m3");
            assertThat(providerRequest.path("truncate").booleanValue()).isFalse();
            assertThat(providerRequest.path("input")).isEqualTo(JSON.valueToTree(chunks.stream()
                    .map(chunk -> chunk.text() + (chunk.headingPath().isEmpty() ? "" : "\n" + chunk.headingPath()))
                    .toList()));
            assertReady(run.gate);

            assertThat(run.service.index(documentId).outcome()).isEqualTo(Outcome.SKIPPED);
            assertThat(requestPaths(run.state())).isEqualTo(requestPaths(state));
            run.report("success", result);
        }
    }

    @Test
    void clearsOnlyFailedDocumentAfterRealPythonReportsCompletedEmbeddingFailure() throws Exception {
        try (var run = IntegrationRun.start(90000)) {
            long failedId = run.createDocument();
            long neighborId = run.createDocument();
            assertThat(run.service.index(failedId).outcome()).isEqualTo(Outcome.INDEXED);
            assertThat(run.service.index(neighborId).outcome()).isEqualTo(Outcome.INDEXED);
            JsonNode neighborVectors = run.index(neighborId);
            var oldChunks = run.chunks(failedId);
            Object indexedAt = run.document(failedId).get("indexed_at");
            assertThat(run.jdbc.update("UPDATE document SET index_status = 'PENDING' WHERE id = ?", failedId))
                    .isEqualTo(1);
            run.control("fail");

            IndexingResult result = run.service.index(failedId);

            assertThat(result.outcome()).isEqualTo(Outcome.FAILED);
            assertThat(result.recoveryRequired()).isFalse();
            assertThat(result.error()).contains("stage=EMBED", "error=EMBEDDING_UNAVAILABLE",
                    "cause=HTTPStatusError", "cleanup=OK").doesNotContain("injected-provider-private-detail");
            assertThat(result.timings()).extracting(StageTiming::stage).containsExactly(
                    Stage.LOAD_DOCUMENT, Stage.CHUNK, Stage.SAVE_CHUNKS, Stage.EMBED,
                    Stage.DELETE_INDEX, Stage.MARK_FAILED);
            assertThat(run.document(failedId)).containsEntry("index_status", "FAILED")
                    .containsEntry("index_error", result.error()).containsEntry("indexed_at", indexedAt)
                    .containsEntry("chunk_count", oldChunks.size());
            assertThat(run.chunks(failedId)).extracting(ChunkRepository.StoredChunk::chunkId)
                    .doesNotContainAnyElementsOf(oldChunks.stream().map(ChunkRepository.StoredChunk::chunkId).toList());
            assertThat(run.index(failedId).path("chunks").size()).isZero();
            assertThat(run.index(neighborId)).isEqualTo(neighborVectors);
            assertThat(run.document(neighborId)).containsEntry("index_status", "INDEXED");
            JsonNode state = run.state();
            assertThat(requestPaths(state)).containsExactly("POST /chunk", "POST /embed",
                    "POST /chunk", "POST /embed", "POST /chunk", "POST /embed", "DELETE /index/" + failedId);
            assertThat(state.path("requests").get(5).path("status").intValue()).isEqualTo(503);
            assertThat(state.path("provider_requests").size()).isEqualTo(3);
            assertThat(state.path("model_metrics").size()).isEqualTo(2);
            assertReady(run.gate);
            run.report("completed-embedding-failure", result);
        }
    }

    @Test
    void remainsPausedWhenRealPythonWritesAfterJavaTimeoutAndSuccessfulDelete() throws Exception {
        try (var run = IntegrationRun.start(3000)) {
            long documentId = run.createDocument();
            long nextDocumentId = run.createDocument();
            Map<String, Object> nextBefore = run.document(nextDocumentId);
            run.control("hold");
            var execution = run.executor.submit(() -> run.service.index(documentId));
            run.awaitState(state -> state.path("held_calls").intValue() == 1, Duration.ofSeconds(15));
            assertThat(run.gate.state()).isEqualTo(State.MUTATING);
            assertThat(run.document(documentId)).containsEntry("index_status", "INDEXING");
            assertThat(run.chunks(documentId)).isNotEmpty();
            assertThat(run.gate.tryAcquire(Operation.QUERY).lease()).isEmpty();
            assertThat(run.service.index(nextDocumentId).outcome()).isEqualTo(Outcome.BUSY);

            IndexingResult result = execution.get(15, TimeUnit.SECONDS);

            assertThat(result.outcome()).isEqualTo(Outcome.FAILED);
            assertThat(result.recoveryRequired()).isTrue();
            assertThat(result.error()).contains("stage=EMBED", "cleanup=OK");
            assertThat(run.document(documentId)).containsEntry("index_status", "FAILED")
                    .containsEntry("index_error", result.error());
            assertThat(run.index(documentId).path("chunks").size()).isZero();
            assertThat(run.state().path("held_calls").intValue()).isEqualTo(1);
            assertThat(run.gate.state()).isEqualTo(State.RECOVERY_REQUIRED);

            run.control("release");
            run.awaitState(state -> {
                JsonNode status = state.path("requests").get(1).path("status");
                return status.isNumber() && status.intValue() == 200;
            }, Duration.ofSeconds(75));

            assertStoredChunksAndVectors(run, documentId, run.chunks(documentId));
            assertThat(run.document(documentId)).containsEntry("index_status", "FAILED")
                    .containsEntry("index_error", result.error());
            assertThat(run.gate.state()).isEqualTo(State.RECOVERY_REQUIRED);
            assertThat(run.gate.tryAcquire(Operation.QUERY).lease()).isEmpty();
            IndexingResult blocked = run.service.index(nextDocumentId);
            assertThat(blocked.outcome()).isEqualTo(Outcome.BUSY);
            assertThat(blocked.recoveryRequired()).isTrue();
            assertThat(blocked.timings()).isEmpty();
            assertThat(run.document(nextDocumentId)).isEqualTo(nextBefore);
            assertThat(run.chunks(nextDocumentId)).isEmpty();
            assertThat(requestPaths(run.state())).containsExactly("POST /chunk", "POST /embed",
                    "DELETE /index/" + documentId);
            run.report("late-python-write-remains-paused", result);
        }
    }

    @Test
    void cleansOwnedResourcesEvenWhenGracefulShutdownReturnsAnError() throws Exception {
        var run = IntegrationRun.start(90000);
        try (run) {
            run.createDocument();
            run.control("fail-shutdown");
        }
        assertThat(run.pythonProcesses).isNotEmpty().noneMatch(ProcessHandle::isAlive);
        assertThat(run.executor.isTerminated()).isTrue();
        assertThat(run.context.isActive()).isFalse();
        assertThat(run.runDirectory).doesNotExist();
    }

    private static void assertStoredChunksAndVectors(IntegrationRun run, long documentId,
                                                    List<ChunkRepository.StoredChunk> chunks) throws Exception {
        byte[] source = SOURCE.getBytes(StandardCharsets.UTF_8);
        int expectedStart = 0;
        JsonNode vectors = run.index(documentId).path("chunks");
        assertThat(vectors.size()).isEqualTo(chunks.size());
        for (int sequence = 0; sequence < chunks.size(); sequence++) {
            var chunk = chunks.get(sequence);
            assertThat(chunk.seq()).isEqualTo(sequence);
            assertThat(chunk.documentId()).isEqualTo(documentId);
            assertThat(chunk.byteStart()).isEqualTo(expectedStart);
            assertThat(chunk.byteEnd()).isBetween(expectedStart + 1, source.length);
            assertThat(chunk.text()).isEqualTo(new String(Arrays.copyOfRange(source, chunk.byteStart(),
                    chunk.byteEnd()), StandardCharsets.UTF_8));
            assertThat(chunk.tokenCount()).isBetween(1, 512);
            JsonNode vector = null;
            for (JsonNode candidate : vectors) {
                if (candidate.path("id").stringValue().equals(Long.toString(chunk.chunkId()))) {
                    vector = candidate;
                    break;
                }
            }
            assertThat(vector).isNotNull();
            assertThat(vector.path("document").stringValue()).isEqualTo(chunk.text()
                    + (chunk.headingPath().isEmpty() ? "" : "\n" + chunk.headingPath()));
            assertThat(vector.path("metadata").path("document_id").longValue()).isEqualTo(documentId);
            assertThat(vector.path("metadata").path("seq").intValue()).isEqualTo(sequence);
            assertThat(vector.path("metadata").path("heading_path").stringValue()).isEqualTo(chunk.headingPath());
            assertThat(vector.path("metadata").path("tags")).isEqualTo(JSON.valueToTree(TAGS));
            assertThat(vector.path("embedding").size()).isEqualTo(1024);
            double magnitude = 0;
            for (JsonNode coordinate : vector.path("embedding")) {
                assertThat(Double.isFinite(coordinate.doubleValue())).isTrue();
                magnitude += Math.abs(coordinate.doubleValue());
            }
            assertThat(magnitude).isPositive();
            expectedStart = chunk.byteEnd();
        }
        assertThat(expectedStart).isEqualTo(source.length);
    }

    private static List<String> requestPaths(JsonNode state) {
        List<String> requests = new ArrayList<>();
        for (JsonNode request : state.path("requests")) {
            requests.add(request.path("method").stringValue() + " " + request.path("path").stringValue());
        }
        return requests;
    }

    private static void assertReady(RagOperationGate gate) {
        assertThat(gate.state()).isEqualTo(State.READY);
        try (var query = gate.tryAcquire(Operation.QUERY).lease().orElseThrow()) {
            assertThat(gate.state()).isEqualTo(State.QUERYING);
        }
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    private static final class IntegrationRun implements AutoCloseable {

        private final String identifier = UUID.randomUUID().toString().replace("-", "");
        private final String database = "easyrag_chain_it_" + identifier;
        private final Path serverDirectory = Path.of(System.getProperty("basedir", ".")).toAbsolutePath().normalize();
        private final Path targetDirectory = serverDirectory.resolve("target").toRealPath();
        private final Path runDirectory = targetDirectory.resolve("indexing-it-" + identifier);
        private final Path pythonLog = targetDirectory.resolve("indexing-it-" + identifier + ".log");
        private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2))
                .followRedirects(HttpClient.Redirect.NEVER).version(HttpClient.Version.HTTP_1_1).build();
        private final ExecutorService executor = Executors.newSingleThreadExecutor();
        private Process python;
        private List<ProcessHandle> pythonProcesses = List.of();
        private URI baseUrl;
        private ConfigurableApplicationContext context;
        private JdbcTemplate jdbc;
        private RagOperationGate gate;
        private DocumentIndexingService service;

        private IntegrationRun() throws IOException {
            Files.createDirectory(runDirectory);
        }

        static IntegrationRun start(int readTimeoutMillis) throws Exception {
            var run = new IntegrationRun();
            try {
                run.startPython();
                run.startJava(readTimeoutMillis);
                return run;
            } catch (Exception | Error failure) {
                try {
                    run.close();
                } catch (Exception | Error cleanupFailure) {
                    failure.addSuppressed(cleanupFailure);
                }
                throw failure;
            }
        }

        private void startPython() throws Exception {
            Path ragDirectory = serverDirectory.getParent().resolve("rag-service");
            Path executable = ragDirectory.resolve(System.getProperty("os.name").startsWith("Windows")
                    ? ".venv/Scripts/python.exe" : ".venv/bin/python");
            assertThat(executable).isRegularFile();
            ProcessBuilder builder = new ProcessBuilder(executable.toString(), "-u",
                    ragDirectory.resolve("tests/indexing_integration_server.py").toString(),
                    "--run-dir", runDirectory.toString()).directory(ragDirectory.toFile());
            builder.environment().put("PYTHONIOENCODING", "utf-8");
            builder.environment().remove("PYTHONPATH");
            python = builder.redirectErrorStream(true).redirectOutput(pythonLog.toFile()).start();
            long deadline = System.nanoTime() + Duration.ofSeconds(40).toNanos();
            Path ready = runDirectory.resolve("ready.json");
            while (System.nanoTime() < deadline) {
                assertThat(python.isAlive()).as("Python fixture exited; inspect %s", pythonLog).isTrue();
                if (Files.exists(ready)) {
                    JsonNode endpoint = JSON.readTree(Files.readString(ready));
                    baseUrl = URI.create("http://127.0.0.1:" + endpoint.path("port").intValue());
                    try {
                        JsonNode status = state();
                        assertThat(status.path("run_id").stringValue()).isEqualTo(runDirectory.getFileName().toString());
                        assertThat(Path.of(status.path("chroma_dir").stringValue()).toRealPath())
                                .isEqualTo(runDirectory.resolve("chroma").toRealPath());
                        assertThat(status.path("chroma").path("status").stringValue()).isEqualTo("UP");
                        assertThat(status.path("chroma").path("vectors").intValue()).isZero();
                        pythonProcesses = Stream.concat(Stream.of(python.toHandle()), python.descendants()).toList();
                        assertThat(pythonProcesses).extracting(ProcessHandle::pid)
                                .contains(status.path("pid").longValue());
                        JsonNode health = request("GET", "/health", null);
                        assertThat(health.path("embedding").path("status").stringValue())
                                .as("Local Ollama must have bge-m3 available").isEqualTo("UP");
                        assertThat(health.path("embedding").path("dim").intValue()).isEqualTo(1024);
                        return;
                    } catch (IOException notListeningYet) {
                        Thread.sleep(100);
                    }
                } else {
                    Thread.sleep(100);
                }
            }
            throw new IllegalStateException("Python fixture did not become ready; inspect " + pythonLog);
        }

        private void startJava(int readTimeoutMillis) {
            String url = "jdbc:mysql://${MYSQL_HOST:localhost}:${MYSQL_PORT:3306}/" + database
                    + "?createDatabaseIfNotExist=true&useUnicode=true&characterEncoding=UTF-8"
                    + "&serverTimezone=Asia/Shanghai&useSSL=false&allowPublicKeyRetrieval=true";
            SpringApplication application = new SpringApplication(ServerApplication.class);
            application.setWebApplicationType(WebApplicationType.NONE);
            application.setRegisterShutdownHook(false);
            application.setBannerMode(Banner.Mode.OFF);
            context = application.run("--spring.profiles.active=local", "--spring.datasource.url=" + url,
                    "--spring.datasource.hikari.transaction-isolation=TRANSACTION_REPEATABLE_READ",
                    "--spring.flyway.url=" + url, "--spring.flyway.user=${spring.datasource.username}",
                    "--spring.flyway.password=${spring.datasource.password}", "--spring.flyway.enabled=true",
                    "--rag.base-url=" + baseUrl, "--rag.connect-timeout-ms=1000",
                    "--rag.read-timeout-ms=" + readTimeoutMillis);
            jdbc = context.getBean(JdbcTemplate.class);
            gate = context.getBean(RagOperationGate.class);
            service = context.getBean(DocumentIndexingService.class);
            assertIsolatedDatabase();
            assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM document", Integer.class)).isZero();
            assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM chunk", Integer.class)).isZero();
            assertThat(gate.state()).isEqualTo(State.RECOVERY_REQUIRED);
            assertThat(service.index(1).outcome()).isEqualTo(Outcome.BUSY);
            try (var recovery = gate.tryAcquire(Operation.RECOVERY).lease().orElseThrow()) {
                assertThat(recovery.confirmCompletion()).isTrue();
            }
        }

        private long createDocument() throws Exception {
            GeneratedKeyHolder keys = new GeneratedKeyHolder();
            String hash = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                    .digest(SOURCE.getBytes(StandardCharsets.UTF_8)));
            jdbc.update(connection -> {
                var statement = connection.prepareStatement("""
                        INSERT INTO document (source_type, source_uri, title, content, content_hash, tags)
                        VALUES ('UPLOAD', 'integration-synthetic.md', ?, ?, ?, ?)
                        """, Statement.RETURN_GENERATED_KEYS);
                statement.setString(1, TITLE);
                statement.setString(2, SOURCE);
                statement.setString(3, hash);
                statement.setString(4, JSON.writeValueAsString(TAGS));
                return statement;
            }, keys);
            return Objects.requireNonNull(keys.getKey()).longValue();
        }

        private Map<String, Object> document(long documentId) {
            return jdbc.queryForMap("SELECT * FROM document WHERE id = ?", documentId);
        }

        private List<ChunkRepository.StoredChunk> chunks(long documentId) {
            return jdbc.query("SELECT * FROM chunk WHERE document_id = ? ORDER BY seq", (row, rowNumber) ->
                    new ChunkRepository.StoredChunk(row.getLong("id"), row.getLong("document_id"), row.getInt("seq"),
                            row.getString("text"), row.getInt("char_start"), row.getInt("char_end"),
                            row.getString("heading_path"), row.getInt("token_count")), documentId);
        }

        private JsonNode request(String method, String path, String body) throws Exception {
            HttpRequest request = HttpRequest.newBuilder(baseUrl.resolve(path)).timeout(Duration.ofSeconds(5))
                    .header("Content-Type", "application/json").method(method, body == null
                            ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofString(body)).build();
            var response = http.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            assertThat(response.statusCode()).as("test fixture %s %s", method, path).isEqualTo(200);
            return JSON.readTree(response.body());
        }

        private JsonNode state() throws Exception {
            return request("GET", "/__it__/state", null);
        }

        private JsonNode index(long documentId) throws Exception {
            return request("GET", "/__it__/index/" + documentId, null);
        }

        private void control(String mode) throws Exception {
            request("POST", "/__it__/control", JSON.writeValueAsString(Map.of("mode", mode)));
        }

        private void awaitState(Predicate<JsonNode> condition, Duration timeout) throws Exception {
            long deadline = System.nanoTime() + timeout.toNanos();
            do {
                if (condition.test(state())) {
                    return;
                }
                Thread.sleep(50);
            } while (System.nanoTime() < deadline);
            throw new AssertionError("Python fixture did not reach the expected barrier; inspect " + pythonLog);
        }

        private void report(String scenario, IndexingResult result) throws Exception {
            System.out.println("INDEXING_IT scenario=" + scenario + " duration_ns=" + result.durationNanos()
                    + " outcome=" + result.outcome() + " recovery_required=" + result.recoveryRequired()
                    + " chunks=" + chunks(result.documentId()).size()
                    + " stages=" + JSON.writeValueAsString(result.timings()) + " model_metrics="
                    + JSON.writeValueAsString(state().path("model_metrics")));
        }

        private void assertIsolatedDatabase() {
            assertThat(database).matches("easyrag_chain_it_[0-9a-f]{32}");
            assertThat(jdbc.queryForObject("SELECT DATABASE()", String.class)).isEqualTo(database);
        }

        @Override
        public void close() throws Exception {
            try {
                if (python != null) {
                    pythonProcesses = Stream.concat(pythonProcesses.stream(), Stream.concat(
                            Stream.of(python.toHandle()), python.descendants())).distinct().toList();
                    try {
                        if (python.isAlive() && baseUrl != null) {
                            try {
                                request("POST", "/__it__/shutdown", "{}");
                            } catch (IOException | AssertionError unavailable) {
                                System.out.println("Python fixture shutdown requires process termination");
                            }
                        }
                    } finally {
                        if (!python.waitFor(5, TimeUnit.SECONDS)) {
                            pythonProcesses.stream().filter(ProcessHandle::isAlive).forEach(ProcessHandle::destroyForcibly);
                            assertTrue(python.waitFor(10, TimeUnit.SECONDS), "owned Python process must exit before cleanup");
                        }
                        for (ProcessHandle process : pythonProcesses) {
                            if (process.isAlive()) {
                                process.destroyForcibly();
                                process.onExit().get(10, TimeUnit.SECONDS);
                            }
                        }
                        assertThat(pythonProcesses).noneMatch(ProcessHandle::isAlive);
                    }
                }
                executor.shutdownNow();
                assertTrue(executor.awaitTermination(15, TimeUnit.SECONDS), "Java indexing executor must exit");
                if (jdbc != null) {
                    assertIsolatedDatabase();
                    jdbc.execute("DROP DATABASE `" + database + "`");
                    assertThat(jdbc.queryForList("SELECT SCHEMA_NAME FROM information_schema.schemata WHERE SCHEMA_NAME = ?",
                            String.class, database)).isEmpty();
                    System.out.println("Dropped isolated MySQL schema: " + database);
                }
                Path resolved = runDirectory.toRealPath();
                assertThat(resolved.getParent()).isEqualTo(targetDirectory);
                assertThat(resolved.getFileName().toString()).isEqualTo("indexing-it-" + identifier);
                try (var paths = Files.walk(resolved)) {
                    for (Path path : paths.sorted(Comparator.reverseOrder()).toList()) {
                        Files.delete(path);
                    }
                }
                System.out.println("Stopped owned Python processes and removed isolated index: " + runDirectory);
            } finally {
                executor.shutdownNow();
                if (context != null) {
                    context.close();
                }
            }
        }
    }
}
