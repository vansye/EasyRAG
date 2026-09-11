package com.easyrag.server.document;

import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.NullSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.boot.env.YamlPropertySourceLoader;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.core.io.ClassPathResource;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.ConnectException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.stream.IntStream;
import java.util.stream.Stream;
import java.util.zip.GZIPOutputStream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTimeoutPreemptively;
import static org.junit.jupiter.api.Assertions.assertTrue;

class EmbedClientTest {

    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final long DOCUMENT_ID = 7;
    private final BlockingQueue<RecordedRequest> requests = new LinkedBlockingQueue<>();
    private HttpServer server;
    private ExecutorService serverExecutor;
    private volatile HttpHandler responder;
    private EmbedClient client;

    @BeforeEach
    void startLoopbackServer() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        serverExecutor = Executors.newCachedThreadPool();
        server.setExecutor(serverExecutor);
        server.createContext("/", exchange -> {
            try (exchange) {
                requests.add(new RecordedRequest(exchange.getRequestMethod(), exchange.getRequestURI().getPath(),
                        exchange.getRequestHeaders().getFirst("Content-Type"),
                        exchange.getRequestHeaders().getFirst("Accept"), exchange.getRequestBody().readAllBytes()));
                responder.handle(exchange);
            }
        });
        server.start();
        respond(200, "application/json", responseBody(1));
        client = new EmbedClient(baseUrl(), 1000, 1000);
    }

    @AfterEach
    void stopLoopbackServer() throws InterruptedException {
        server.stop(0);
        serverExecutor.shutdownNow();
        assertTrue(serverExecutor.awaitTermination(5, TimeUnit.SECONDS));
    }

    @Test
    void sendsStoredIdsAndOriginalUnicodeWithoutAddingMetadataToText() throws Exception {
        String text = "\uFEFF# 原文 😀\r\ne\u0301 和 👩🏽‍💻\n ";
        String heading = "资料 > Java 😀\r\n";
        List<String> tags = List.of("Java", "研发 😀", "");
        var chunk = storedChunk(Long.MAX_VALUE, Long.MAX_VALUE, 0, text, heading);

        assertThat(client.embed(Long.MAX_VALUE, List.of(chunk), tags)).isEqualTo(1);

        RecordedRequest request = requests.poll(2, TimeUnit.SECONDS);
        assertThat(request).isNotNull();
        assertThat(request.method()).isEqualTo("POST");
        assertThat(request.path()).isEqualTo("/embed");
        assertThat(request.contentType()).isEqualTo("application/json");
        assertThat(request.accept()).isEqualTo("application/json");
        assertThat(JSON.readTree(request.body())).isEqualTo(JSON.valueToTree(Map.of(
                "document_id", Long.MAX_VALUE, "chunks", List.of(Map.of(
                        "chunk_id", Long.MAX_VALUE, "text", text, "heading_path", heading, "tags", tags)))));
        assertThat(requests).isEmpty();
    }

    @Test
    void sortsAWholeDocumentBySequenceWithoutMutatingTheCallersList() throws Exception {
        var first = storedChunk(102, DOCUMENT_ID, 0, "第一块\r\n", "");
        var second = storedChunk(101, DOCUMENT_ID, 1, "第二块 😀", "章节");
        List<ChunkRepository.StoredChunk> chunks = new ArrayList<>(List.of(second, first));
        respond(200, "application/json", responseBody(2));

        assertThat(client.embed(DOCUMENT_ID, chunks, List.of())).isEqualTo(2);

        assertThat(chunks).containsExactly(second, first);
        JsonNode sent = JSON.readTree(requests.element().body()).get("chunks");
        assertThat(sent.size()).isEqualTo(2);
        assertThat(sent.get(0).get("chunk_id").asLong()).isEqualTo(102);
        assertThat(sent.get(1).get("chunk_id").asLong()).isEqualTo(101);
        assertThat(sent.get(0).get("heading_path").asString()).isEmpty();
        assertThat(sent.get(0).get("tags").isArray()).isTrue();
        assertThat(sent.get(0).get("tags").size()).isZero();
        assertThat(requests).hasSize(1);
    }

    @ParameterizedTest
    @ValueSource(ints = {64, 65, 129})
    void submitsTheEntireDocumentInOneRequestRatherThanHttpBatches(int count) throws Exception {
        List<ChunkRepository.StoredChunk> chunks = IntStream.range(0, count)
                .mapToObj(sequence -> storedChunk(sequence + 101L, DOCUMENT_ID, sequence, "正文 " + sequence, ""))
                .toList();
        respond(200, "application/json", responseBody(count));

        assertThat(client.embed(DOCUMENT_ID, chunks, List.of("整篇"))).isEqualTo(count);

        assertThat(requests).hasSize(1);
        JsonNode sent = JSON.readTree(requests.element().body()).get("chunks");
        assertThat(sent.size()).isEqualTo(count);
        for (int sequence = 0; sequence < count; sequence++) {
            assertThat(sent.get(sequence).get("chunk_id").asLong()).isEqualTo(sequence + 101L);
            assertThat(sent.get(sequence).get("tags")).isEqualTo(JSON.valueToTree(List.of("整篇")));
        }
    }

    @ParameterizedTest
    @MethodSource("invalidSuccessfulBodies")
    void rejectsMalformedOrCoercedSuccessAndAnyIndexedCountMismatch(String body) {
        respond(200, "application/json", body.getBytes(StandardCharsets.UTF_8));

        assertInvalidResponse();
    }

    @Test
    void rejectsMalformedUtf8InTheJsonResponse() {
        byte[] body = {'{', '"', (byte) 0xc3, (byte) 0x28, '"', ':', '1', '}'};
        respond(200, "application/json", body);

        assertInvalidResponse();
    }

    @ParameterizedTest
    @NullSource
    @ValueSource(strings = {"text/plain", "text/html", "invalid", "application/json; charset=not-a-charset",
            "application/*", "*/*", "application/problem+json"})
    void rejectsSuccessfulResponsesWithoutAnExactJsonMediaType(String contentType) {
        respond(200, contentType, responseBody(1));

        assertInvalidResponse();
    }

    @ParameterizedTest(name = "HTTP {0}: {1}")
    @MethodSource("failedHttpResponses")
    void preservesHttpFailuresWithoutRetryingFollowingRedirectsOrCleaningTheIndex(int status, String body) {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        respond(status, "application/json", bytes);
        HttpHandler responseHandler = responder;
        responder = exchange -> {
            exchange.getResponseHeaders().set("Location", baseUrl() + "/must-not-follow");
            exchange.getResponseHeaders().set("X-Upstream-Error", "preserved");
            if (status == 401) {
                exchange.getResponseHeaders().set("WWW-Authenticate", "Basic realm=\"rag-service\"");
            }
            if (status == 407) {
                exchange.getResponseHeaders().set("Proxy-Authenticate", "Basic realm=\"proxy\"");
            }
            responseHandler.handle(exchange);
        };

        RestClientResponseException failure = assertThrows(RestClientResponseException.class,
                () -> client.embed(DOCUMENT_ID, validChunks(), List.of()));

        assertThat(failure.getStatusCode().value()).isEqualTo(status);
        assertThat(failure.getResponseBodyAsByteArray()).isEqualTo(bytes);
        assertThat(failure.getResponseBodyAsString()).isEqualTo(body);
        assertThat(failure.getResponseHeaders()).isNotNull();
        assertThat(failure.getResponseHeaders().getFirst("X-Upstream-Error")).isEqualTo("preserved");
        assertThat(requests).hasSize(1);
        assertThat(requests.element().method()).isEqualTo("POST");
        assertThat(requests.element().path()).isEqualTo("/embed");
    }

    @ParameterizedTest
    @ValueSource(strings = {"invalid", "application/json; charset=not-a-charset"})
    void preservesNon200StatusAndRawBytesEvenWhenContentTypeOrBodyIsInvalid(String contentType) {
        byte[] body = {(byte) 0xc3, (byte) 0x28};
        respond(503, contentType, body);

        RestClientResponseException failure = assertThrows(RestClientResponseException.class,
                () -> client.embed(DOCUMENT_ID, validChunks(), List.of()));

        assertThat(failure.getStatusCode().value()).isEqualTo(503);
        assertThat(failure.getResponseBodyAsByteArray()).isEqualTo(body);
        assertThat(failure.getResponseHeaders()).isNotNull();
        assertThat(failure.getResponseHeaders().getFirst("Content-Type")).isEqualTo(contentType);
        assertThat(requests).hasSize(1);
    }

    @Test
    void preservesEncodedHttpErrorBodyRatherThanTransparentlyDecompressingIt() throws IOException {
        ByteArrayOutputStream encoded = new ByteArrayOutputStream();
        try (GZIPOutputStream gzip = new GZIPOutputStream(encoded)) {
            gzip.write("{\"detail\":{\"error\":\"INDEX_UNAVAILABLE\"}}".getBytes(StandardCharsets.UTF_8));
        }
        byte[] body = encoded.toByteArray();
        respond(503, "application/json", body);
        HttpHandler responseHandler = responder;
        responder = exchange -> {
            exchange.getResponseHeaders().set("Content-Encoding", "gzip");
            responseHandler.handle(exchange);
        };

        RestClientResponseException failure = assertThrows(RestClientResponseException.class,
                () -> client.embed(DOCUMENT_ID, validChunks(), List.of()));

        assertThat(failure.getStatusCode().value()).isEqualTo(503);
        assertThat(failure.getResponseBodyAsByteArray()).isEqualTo(body);
        assertThat(failure.getResponseHeaders()).isNotNull();
        assertThat(failure.getResponseHeaders().getFirst("Content-Encoding")).isEqualTo("gzip");
        assertThat(requests).hasSize(1);
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("invalidRequests")
    void rejectsInvalidDocumentCollectionsAndTagsBeforeSendingHttp(String name, long documentId,
            List<ChunkRepository.StoredChunk> chunks, List<String> tags) {
        assertThrows(IllegalArgumentException.class, () -> client.embed(documentId, chunks, tags));

        assertThat(requests).isEmpty();
    }

    @ParameterizedTest
    @MethodSource("invalidStoredChunks")
    void rejectsInvalidStoredChunkFieldsBeforeSendingHttp(ChunkRepository.StoredChunk chunk) {
        assertThrows(IllegalArgumentException.class, () -> client.embed(DOCUMENT_ID, List.of(chunk), List.of()));

        assertThat(requests).isEmpty();
    }

    @ParameterizedTest
    @MethodSource("invalidTimeouts")
    void rejectsNonPositiveTimeouts(int connectMillis, int readMillis) {
        assertThrows(IllegalArgumentException.class, () -> new EmbedClient(baseUrl(), connectMillis, readMillis));

        assertThat(requests).isEmpty();
    }

    @ParameterizedTest
    @ValueSource(strings = {"", "/", "/internal", "/internal/"})
    void supportsBasePathPrefixesAndJsonCharsetParameters(String prefix) {
        EmbedClient configured = new EmbedClient(baseUrl() + prefix, 1000, 1000);
        respond(200, "application/json; charset=utf-8", responseBody(1));

        assertThat(configured.embed(DOCUMENT_ID, validChunks(), List.of())).isEqualTo(1);

        assertThat(requests).hasSize(1);
        assertThat(requests.element().path()).isEqualTo(prefix.startsWith("/internal") ? "/internal/embed" : "/embed");
    }

    @ParameterizedTest
    @ValueSource(booleans = {false, true})
    void readTimeoutAppliesToHeadersAndBodyWithoutRetryOrCleanup(boolean afterHeaders) {
        CountDownLatch release = new CountDownLatch(1);
        byte[] body = responseBody(1);
        responder = exchange -> {
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            if (afterHeaders) {
                exchange.sendResponseHeaders(200, body.length);
                exchange.getResponseBody().write(body, 0, 1);
                exchange.getResponseBody().flush();
            }
            try {
                release.await(5, TimeUnit.SECONDS);
            } catch (InterruptedException failure) {
                Thread.currentThread().interrupt();
                throw new IOException(failure);
            }
            if (afterHeaders) {
                exchange.getResponseBody().write(body, 1, body.length - 1);
            } else {
                exchange.sendResponseHeaders(200, body.length);
                exchange.getResponseBody().write(body);
            }
        };
        EmbedClient impatient = new EmbedClient(baseUrl(), 1000, 100);
        try {
            RestClientException failure = assertTimeoutPreemptively(Duration.ofSeconds(3),
                    () -> assertThrows(RestClientException.class,
                            () -> impatient.embed(DOCUMENT_ID, validChunks(), List.of())));

            assertThat(failure).isInstanceOf(ResourceAccessException.class).hasCauseInstanceOf(IOException.class);
            assertThat(requests).hasSize(1);
        } finally {
            release.countDown();
        }
    }

    @Test
    void readBudgetIncludesTheWholeBodyEvenWhenBytesKeepArriving() {
        byte[] body = responseBody(1);
        AtomicInteger delivered = new AtomicInteger();
        CountDownLatch release = new CountDownLatch(1);
        responder = exchange -> {
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, body.length);
            for (byte value : body) {
                exchange.getResponseBody().write(value);
                exchange.getResponseBody().flush();
                delivered.incrementAndGet();
                try {
                    if (release.await(150, TimeUnit.MILLISECONDS)) {
                        return;
                    }
                } catch (InterruptedException failure) {
                    Thread.currentThread().interrupt();
                    throw new IOException(failure);
                }
            }
        };
        EmbedClient bounded = new EmbedClient(baseUrl(), 1000, 1000);
        try {
            ResourceAccessException failure = assertTimeoutPreemptively(Duration.ofSeconds(5),
                    () -> assertThrows(ResourceAccessException.class,
                            () -> bounded.embed(DOCUMENT_ID, validChunks(), List.of())));

            assertThat(failure).hasCauseInstanceOf(IOException.class);
            assertThat(delivered.get()).isBetween(2, body.length - 1);
            assertThat(requests).hasSize(1);
        } finally {
            release.countDown();
        }
    }

    @Test
    void connectionRefusalRemainsATransportFailure() {
        server.stop(0);

        ResourceAccessException failure = assertThrows(ResourceAccessException.class,
                () -> client.embed(DOCUMENT_ID, validChunks(), List.of()));

        assertThat(failure).hasCauseInstanceOf(ConnectException.class);
        assertThat(requests).isEmpty();
    }

    @Test
    void springWiresTheExistingRagConfigurationWithoutContactingPythonAtStartup() throws IOException {
        var properties = new YamlPropertySourceLoader().load("application", new ClassPathResource("application.yml"));
        new ApplicationContextRunner()
                .withInitializer(context -> properties.forEach(property ->
                        context.getEnvironment().getPropertySources().addLast(property)))
                .withPropertyValues("rag.base-url=" + baseUrl() + "/configured")
                .withUserConfiguration(EmbedClient.class)
                .run(context -> {
                    assertThat(context).hasNotFailed().hasSingleBean(EmbedClient.class);
                    assertThat(requests).isEmpty();
                    assertThat(context.getBean(EmbedClient.class).embed(DOCUMENT_ID, validChunks(), List.of()))
                            .isEqualTo(1);
                    assertThat(requests).hasSize(1);
                    assertThat(requests.element().path()).isEqualTo("/configured/embed");
                });
    }

    private void assertInvalidResponse() {
        RestClientException failure = assertThrows(RestClientException.class,
                () -> client.embed(DOCUMENT_ID, validChunks(), List.of()));

        assertThat(failure).hasMessageContaining("Invalid /embed response");
        assertThat(failure.getCause()).isNotNull();
        assertThat(requests).hasSize(1);
    }

    private static Stream<String> invalidSuccessfulBodies() {
        return Stream.of("", " ", "null", "[]", "[1]", "1", "true", "\"1\"", "{}", "{\"indexed\":null}",
                "{\"indexed\":0}", "{\"indexed\":-1}", "{\"indexed\":2}", "{\"indexed\":2147483648}",
                "{\"indexed\":9223372036854775808}", "{\"indexed\":1.0}", "{\"indexed\":1.9}", "{\"indexed\":1e0}",
                "{\"indexed\":\"1\"}", "{\"indexed\":true}", "{\"indexed\":false}", "{\"indexed\":[]}",
                "{\"indexed\":{}}", "{\"indexed\":1,\"indexed\":1}", "{\"indexed\":0,\"indexed\":1}",
                "{\"indexed\":1} {}", "{\"indexed\":1} null", "{\"indexed\":1} invalid", "{\"indexed\":1", "{\"indexed\":1,}");
    }

    private static Stream<Arguments> failedHttpResponses() {
        String successBody = "{\"indexed\":1}";
        return Stream.of(Arguments.of(201, successBody), Arguments.of(202, successBody), Arguments.of(204, ""),
                Arguments.of(302, successBody), Arguments.of(303, successBody), Arguments.of(307, successBody),
                Arguments.of(308, successBody),
                Arguments.of(401, "{\"detail\":\"unauthorized\"}"),
                Arguments.of(407, "{\"detail\":\"proxy authentication required\"}"),
                Arguments.of(422, "{\"detail\":[{\"loc\":[\"body\",\"chunks\"],\"msg\":\"invalid chunks\"}]}"),
                Arguments.of(409, "{\"detail\":{\"error\":\"CHUNK_ID_CONFLICT\"}}"),
                Arguments.of(503, "{\"detail\":{\"error\":\"EMBEDDING_UNAVAILABLE\",\"cause\":\"ReadTimeout\"}}"),
                Arguments.of(503, "{\"detail\":{\"error\":\"INDEX_UNAVAILABLE\",\"cause\":\"RuntimeError\"}}"),
                Arguments.of(503, "{\"detail\":{\"error\":\"INDEX_WRITE_FAILED\",\"cause\":\"RuntimeError\",\"cleanup_error\":null}}"),
                Arguments.of(503, "{\"detail\":{\"error\":\"INDEX_WRITE_FAILED\",\"cause\":\"RuntimeError\",\"cleanup_error\":\"ValueError\"}}"),
                Arguments.of(404, ""), Arguments.of(500, "上游服务错误"));
    }

    private static Stream<Arguments> invalidRequests() {
        var valid = validChunks().get(0);
        return Stream.of(
                Arguments.of("zero document id", 0L, List.of(valid), List.of()),
                Arguments.of("negative document id", -1L, List.of(valid), List.of()),
                Arguments.of("null chunks", DOCUMENT_ID, null, List.of()),
                Arguments.of("empty chunks", DOCUMENT_ID, List.of(), List.of()),
                Arguments.of("null chunk", DOCUMENT_ID, Arrays.asList(valid, null), List.of()),
                Arguments.of("null tags", DOCUMENT_ID, List.of(valid), null),
                Arguments.of("null tag", DOCUMENT_ID, List.of(valid), Arrays.asList("Java", null)),
                Arguments.of("unpaired high surrogate tag", DOCUMENT_ID, List.of(valid), List.of("\uD800")),
                Arguments.of("unpaired low surrogate tag", DOCUMENT_ID, List.of(valid), List.of("\uDC00")),
                Arguments.of("duplicate chunk id", DOCUMENT_ID,
                        List.of(valid, storedChunk(valid.chunkId(), DOCUMENT_ID, 1, "def", "")), List.of()),
                Arguments.of("duplicate sequence", DOCUMENT_ID,
                        List.of(valid, storedChunk(102, DOCUMENT_ID, 0, "def", "")), List.of()),
                Arguments.of("sequence gap", DOCUMENT_ID,
                        List.of(valid, storedChunk(102, DOCUMENT_ID, 2, "def", "")), List.of()));
    }

    private static Stream<ChunkRepository.StoredChunk> invalidStoredChunks() {
        return Stream.of(storedChunk(0, DOCUMENT_ID, 0, "abc", ""),
                storedChunk(-1, DOCUMENT_ID, 0, "abc", ""),
                storedChunk(101, DOCUMENT_ID + 1, 0, "abc", ""),
                storedChunk(101, DOCUMENT_ID, -1, "abc", ""),
                storedChunk(101, DOCUMENT_ID, 1, "abc", ""),
                storedChunk(101, DOCUMENT_ID, 0, null, ""),
                storedChunk(101, DOCUMENT_ID, 0, "", ""),
                storedChunk(101, DOCUMENT_ID, 0, " \r\n\u00A0\u0085\u2003", ""),
                storedChunk(101, DOCUMENT_ID, 0, "\uD800", ""),
                storedChunk(101, DOCUMENT_ID, 0, "\uDC00", ""),
                storedChunk(101, DOCUMENT_ID, 0, "abc", null),
                storedChunk(101, DOCUMENT_ID, 0, "abc", "\uD800"),
                storedChunk(101, DOCUMENT_ID, 0, "abc", "\uDC00"));
    }

    private static Stream<Arguments> invalidTimeouts() {
        return Stream.of(Arguments.of(0, 1000), Arguments.of(-1, 1000),
                Arguments.of(1000, 0), Arguments.of(1000, -1));
    }

    private static List<ChunkRepository.StoredChunk> validChunks() {
        return List.of(storedChunk(101, DOCUMENT_ID, 0, "abc", ""));
    }

    private static ChunkRepository.StoredChunk storedChunk(long chunkId, long documentId, int sequence,
                                                           String text, String heading) {
        return new ChunkRepository.StoredChunk(chunkId, documentId, sequence, text, 0,
                text == null ? 0 : text.getBytes(StandardCharsets.UTF_8).length, heading, 1);
    }

    private static byte[] responseBody(int indexed) {
        return JSON.writeValueAsBytes(Map.of("indexed", indexed));
    }

    private String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    private void respond(int status, String contentType, byte[] body) {
        responder = exchange -> {
            if (contentType != null) {
                exchange.getResponseHeaders().set("Content-Type", contentType);
            }
            exchange.sendResponseHeaders(status, body.length == 0 ? -1 : body.length);
            if (body.length > 0) {
                exchange.getResponseBody().write(body);
            }
        };
    }

    private record RecordedRequest(String method, String path, String contentType, String accept, byte[] body) {}
}
