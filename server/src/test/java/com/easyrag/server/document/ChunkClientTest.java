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
import tools.jackson.databind.node.ObjectNode;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.math.BigDecimal;
import java.net.ConnectException;
import java.net.InetSocketAddress;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.stream.Stream;
import java.util.stream.StreamSupport;
import java.util.zip.GZIPOutputStream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTimeoutPreemptively;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ChunkClientTest {

    private static final JsonMapper JSON = JsonMapper.builder().build();
    private final BlockingQueue<RecordedRequest> requests = new LinkedBlockingQueue<>();
    private HttpServer server;
    private ExecutorService serverExecutor;
    private volatile HttpHandler responder;
    private ChunkClient client;

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
        respond(200, "application/json", responseBody("abc"));
        client = new ChunkClient(baseUrl(), 1000, 1000);
    }

    @AfterEach
    void stopLoopbackServer() throws InterruptedException {
        server.stop(0);
        serverExecutor.shutdownNow();
        assertTrue(serverExecutor.awaitTermination(5, TimeUnit.SECONDS));
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("unicodeDocuments")
    void sendsOriginalUnicodeAndReadsSharedByteOffsetContract(String name, JsonNode document) throws Exception {
        String source = document.get("text").asString();
        String title = "文档标题 😀\r\n";
        JsonNode chunks = document.get("chunks").deepCopy();
        for (JsonNode chunk : chunks) {
            ((ObjectNode) chunk).put("token_count", 1);
        }
        ObjectNode response = JSON.createObjectNode().set("chunks", chunks);
        respond(200, "application/json", JSON.writeValueAsBytes(response));

        ChunkBatch batch = client.chunk(Long.MAX_VALUE, source, title);

        assertThat(batch).isNotNull();
        batch.validateAgainst(source);
        JsonNode actual = JSON.valueToTree(batch);
        assertThat(actual).isEqualTo(response);
        RecordedRequest request = requests.poll(2, TimeUnit.SECONDS);
        assertThat(request).isNotNull();
        assertThat(request.method()).isEqualTo("POST");
        assertThat(request.path()).isEqualTo("/chunk");
        assertThat(request.contentType()).isEqualTo("application/json");
        assertThat(request.accept()).isEqualTo("application/json");
        JsonNode payload = JSON.readTree(request.body());
        assertThat(payload.size()).isEqualTo(3);
        assertThat(payload.get("document_id").isIntegralNumber()).isTrue();
        assertThat(payload.get("document_id").asLong()).isEqualTo(Long.MAX_VALUE);
        assertThat(payload.get("text").asString()).isEqualTo(source);
        assertThat(payload.get("title").asString()).isEqualTo(title);
        assertThat(requests).isEmpty();
    }

    @ParameterizedTest(name = "{0}={1}")
    @MethodSource("invalidFieldTypes")
    void rejectsJsonTypesBeforeTheyCanBeCoerced(String field, Object value, String source) {
        Map<String, Object> chunk = responseChunk(source);
        chunk.put(field, value);
        respond(200, "application/json", JSON.writeValueAsBytes(Map.of("chunks", List.of(chunk))));

        assertInvalidResponse(source);
    }

    @ParameterizedTest
    @ValueSource(strings = {"seq", "text", "byte_start", "byte_end", "heading_path", "token_count"})
    void rejectsMissingResultFields(String field) {
        Map<String, Object> chunk = responseChunk("abc");
        chunk.remove(field);
        respond(200, "application/json", JSON.writeValueAsBytes(Map.of("chunks", List.of(chunk))));

        assertInvalidResponse("abc");
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("malformedResponses")
    void rejectsMalformedOrAmbiguousResponseBodies(String name, String body) {
        respond(200, "application/json", body.getBytes(StandardCharsets.UTF_8));

        assertInvalidResponse("abc");
    }

    @ParameterizedTest(name = "{0}={1}")
    @MethodSource("invalidResults")
    void validatesParsedChunksAgainstTheExactSentContent(String field, Object value) {
        Map<String, Object> chunk = responseChunk("abc");
        chunk.put(field, value);
        respond(200, "application/json", JSON.writeValueAsBytes(Map.of("chunks", List.of(chunk))));

        assertInvalidResponse("abc");
    }

    @Test
    void doesNotRepairInvalidUtf8ResponseBytesIntoMatchingText() {
        byte[] prefix = "{\"chunks\":[{\"seq\":0,\"text\":\"".getBytes(StandardCharsets.UTF_8);
        byte[] suffix = "\",\"byte_start\":0,\"byte_end\":4,\"heading_path\":\"\",\"token_count\":1}]}"
                .getBytes(StandardCharsets.UTF_8);
        byte[] body = ByteBuffer.allocate(prefix.length + 2 + suffix.length)
                .put(prefix).put((byte) 0xC3).put((byte) 0x28).put(suffix).array();
        respond(200, "application/json", body);

        assertInvalidResponse("\uFFFD(");
    }

    @ParameterizedTest(name = "HTTP {0}: {1}")
    @MethodSource("failedHttpResponses")
    void preservesFailedHttpResponsesWithoutRetryingOrFollowingRedirects(int status, String body) {
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
                () -> client.chunk(1, "abc", "title"));

        assertThat(failure.getStatusCode().value()).isEqualTo(status);
        assertThat(failure.getResponseBodyAsByteArray()).isEqualTo(bytes);
        assertThat(failure.getResponseBodyAsString()).isEqualTo(body);
        assertThat(failure.getResponseHeaders()).isNotNull();
        assertThat(failure.getResponseHeaders().getFirst("X-Upstream-Error")).isEqualTo("preserved");
        assertThat(requests).hasSize(1);
    }

    @Test
    void preservesEncodedHttpErrorBodyRatherThanTransparentlyDecompressingIt() throws IOException {
        ByteArrayOutputStream encoded = new ByteArrayOutputStream();
        try (GZIPOutputStream gzip = new GZIPOutputStream(encoded)) {
            gzip.write("{\"detail\":{\"error\":\"TOKENIZER_UNAVAILABLE\"}}".getBytes(StandardCharsets.UTF_8));
        }
        byte[] body = encoded.toByteArray();
        respond(503, "application/json", body);
        HttpHandler responseHandler = responder;
        responder = exchange -> {
            exchange.getResponseHeaders().set("Content-Encoding", "gzip");
            responseHandler.handle(exchange);
        };

        RestClientResponseException failure = assertThrows(RestClientResponseException.class,
                () -> client.chunk(1, "abc", ""));

        assertThat(failure.getStatusCode().value()).isEqualTo(503);
        assertThat(failure.getResponseBodyAsByteArray()).isEqualTo(body);
        assertThat(failure.getResponseHeaders()).isNotNull();
        assertThat(failure.getResponseHeaders().getFirst("Content-Encoding")).isEqualTo("gzip");
        assertThat(requests).hasSize(1);
    }

    @ParameterizedTest
    @ValueSource(strings = {"invalid", "application/json; charset=not-a-charset"})
    void failedStatusIsPreservedEvenWithMalformedContentType(String contentType) {
        byte[] body = "{\"detail\":{\"error\":\"TOKENIZER_UNAVAILABLE\"}}".getBytes(StandardCharsets.UTF_8);
        respond(503, contentType, body);

        RestClientResponseException failure = assertThrows(RestClientResponseException.class,
                () -> client.chunk(1, "abc", ""));

        assertThat(failure.getStatusCode().value()).isEqualTo(503);
        assertThat(failure.getResponseBodyAsByteArray()).isEqualTo(body);
        assertThat(failure.getResponseHeaders()).isNotNull();
        assertThat(failure.getResponseHeaders().getFirst("Content-Type")).isEqualTo(contentType);
        assertThat(requests).hasSize(1);
    }

    @ParameterizedTest
    @NullSource
    @ValueSource(strings = {"text/plain", "text/html", "invalid", "application/json; charset=not-a-charset",
            "application/*", "*/*"})
    void rejectsSuccessfulResponsesWithoutJsonContentType(String contentType) {
        respond(200, contentType, responseBody("abc"));

        assertInvalidResponse("abc");
    }

    @ParameterizedTest
    @ValueSource(strings = {"", "/", "/internal", "/internal/"})
    void acceptsConfiguredBasePathsAndEmptyTitles(String prefix) throws Exception {
        ChunkClient configured = new ChunkClient(baseUrl() + prefix, 1000, 1000);
        respond(200, "application/json; charset=utf-8", responseBody("abc"));

        assertThat(configured.chunk(1, "abc", "").chunks()).hasSize(1);

        RecordedRequest request = requests.poll(2, TimeUnit.SECONDS);
        assertThat(request).isNotNull();
        assertThat(request.path()).isEqualTo(prefix.startsWith("/internal") ? "/internal/chunk" : "/chunk");
        assertThat(JSON.readTree(request.body()).get("title").asString()).isEmpty();
    }

    @Test
    void acceptsMaximumIntegerTokenCountWithoutRecomputingIt() {
        Map<String, Object> chunk = responseChunk("abc");
        chunk.put("token_count", Integer.MAX_VALUE);
        respond(200, "application/json", JSON.writeValueAsBytes(Map.of("chunks", List.of(chunk))));

        assertThat(client.chunk(1, "abc", "").chunks().get(0).tokenCount()).isEqualTo(Integer.MAX_VALUE);
    }

    @ParameterizedTest
    @MethodSource("invalidRequests")
    void rejectsInvalidRequestsBeforeSendingHttp(long documentId, String text, String title) {
        assertThrows(IllegalArgumentException.class, () -> client.chunk(documentId, text, title));

        assertThat(requests).isEmpty();
    }

    @ParameterizedTest
    @MethodSource("invalidTimeouts")
    void rejectsTimeoutsThatWouldBeInvalidOrUnbounded(int connectMillis, int readMillis) {
        assertThrows(IllegalArgumentException.class, () -> new ChunkClient(baseUrl(), connectMillis, readMillis));

        assertThat(requests).isEmpty();
    }

    @ParameterizedTest
    @ValueSource(booleans = {false, true})
    void readTimeoutAppliesToBothHeadersAndBody(boolean afterHeaders) {
        CountDownLatch release = new CountDownLatch(1);
        byte[] body = responseBody("abc");
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
        ChunkClient impatient = new ChunkClient(baseUrl(), 1000, 100);
        try {
            RestClientException failure = assertTimeoutPreemptively(Duration.ofSeconds(3),
                    () -> assertThrows(RestClientException.class, () -> impatient.chunk(1, "abc", "")));

            assertThat(failure).isInstanceOf(ResourceAccessException.class).hasCauseInstanceOf(IOException.class);
            assertThat(requests).hasSize(1);
        } finally {
            release.countDown();
        }
    }

    @Test
    void readBudgetIncludesTheWholeBodyEvenWhenSegmentsKeepArriving() {
        byte[] body = responseBody("abc");
        int segmentSize = (body.length + 11) / 12;
        AtomicInteger delivered = new AtomicInteger();
        CountDownLatch release = new CountDownLatch(1);
        responder = exchange -> {
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, body.length);
            for (int offset = 0; offset < body.length; offset += segmentSize) {
                int length = Math.min(segmentSize, body.length - offset);
                exchange.getResponseBody().write(body, offset, length);
                exchange.getResponseBody().flush();
                delivered.addAndGet(length);
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
        ChunkClient bounded = new ChunkClient(baseUrl(), 1000, 1000);
        try {
            ResourceAccessException failure = assertTimeoutPreemptively(Duration.ofSeconds(5),
                    () -> assertThrows(ResourceAccessException.class, () -> bounded.chunk(1, "abc", "")));

            assertThat(failure).hasCauseInstanceOf(IOException.class);
            assertThat(delivered.get()).isBetween(segmentSize * 2, body.length - 1);
            assertThat(requests).hasSize(1);
        } finally {
            release.countDown();
        }
    }

    @Test
    void connectionRefusalRemainsATransportFailure() {
        server.stop(0);

        ResourceAccessException failure = assertThrows(ResourceAccessException.class,
                () -> client.chunk(1, "abc", ""));

        assertThat(failure).hasCauseInstanceOf(ConnectException.class);
        assertThat(requests).isEmpty();
    }

    @Test
    void springWiresTheConfiguredClientWithoutContactingPythonAtStartup() throws IOException {
        var properties = new YamlPropertySourceLoader().load("application", new ClassPathResource("application.yml"));
        new ApplicationContextRunner()
                .withInitializer(context -> properties.forEach(property ->
                        context.getEnvironment().getPropertySources().addLast(property)))
                .withPropertyValues("rag.base-url=" + baseUrl() + "/configured")
                .withUserConfiguration(ChunkClient.class)
                .run(context -> {
                    assertThat(context).hasNotFailed().hasSingleBean(ChunkClient.class);
                    assertThat(requests).isEmpty();
                    assertThat(context.getBean(ChunkClient.class).chunk(1, "abc", "").chunks()).hasSize(1);
                    assertThat(requests).hasSize(1);
                    assertThat(requests.element().path()).isEqualTo("/configured/chunk");
                });
    }

    private void assertInvalidResponse(String source) {
        RestClientException failure = assertThrows(RestClientException.class, () -> client.chunk(1, source, ""));
        assertThat(failure).hasMessageContaining("Invalid /chunk response");
        assertThat(requests).hasSize(1);
    }

    private static Stream<Arguments> invalidFieldTypes() {
        return Stream.of(
                Arguments.of("seq", new BigDecimal("0.9"), "abc"),
                Arguments.of("byte_start", new BigDecimal("-0.9"), "abc"),
                Arguments.of("byte_end", new BigDecimal("3.9"), "abc"),
                Arguments.of("token_count", new BigDecimal("1.9"), "abc"),
                Arguments.of("seq", new BigDecimal("0.0"), "abc"),
                Arguments.of("seq", "0", "abc"),
                Arguments.of("byte_start", "0", "abc"),
                Arguments.of("byte_end", "3", "abc"),
                Arguments.of("token_count", "1", "abc"),
                Arguments.of("text", 123, "123"),
                Arguments.of("text", new BigDecimal("12.5"), "12.5"),
                Arguments.of("text", false, "false"),
                Arguments.of("heading_path", 123, "abc"),
                Arguments.of("heading_path", new BigDecimal("12.5"), "abc"),
                Arguments.of("heading_path", false, "abc"),
                Arguments.of("seq", true, "abc"),
                Arguments.of("token_count", 2147483648L, "abc"));
    }

    private static Stream<Arguments> malformedResponses() {
        String valid = new String(responseBody("abc"), StandardCharsets.UTF_8);
        return Stream.of(Arguments.of("empty body", ""), Arguments.of("whitespace body", " \r\n"),
                Arguments.of("null", "null"), Arguments.of("truncated JSON", "{\"chunks\":["),
                Arguments.of("missing chunks", "{}"), Arguments.of("wrong envelope", "[]"),
                Arguments.of("empty chunks", "{\"chunks\":[]}"),
                Arguments.of("null chunk", "{\"chunks\":[null]}"),
                Arguments.of("object chunks", "{\"chunks\":{}}"),
                Arguments.of("trailing JSON", valid + " {}"),
                Arguments.of("duplicate seq", valid.replace("\"seq\":0", "\"seq\":7,\"seq\":0")),
                Arguments.of("duplicate chunks", "{\"chunks\":[]," + valid.substring(1)));
    }

    private static Stream<Arguments> invalidResults() {
        return Stream.of(Arguments.of("seq", 1), Arguments.of("byte_start", 1),
                Arguments.of("byte_end", 2), Arguments.of("text", "abd"), Arguments.of("token_count", -1),
                Arguments.of("heading_path", null));
    }

    private static Stream<Arguments> failedHttpResponses() {
        String valid = new String(responseBody("abc"), StandardCharsets.UTF_8);
        return Stream.of(Arguments.of(201, valid), Arguments.of(202, valid), Arguments.of(204, ""),
                Arguments.of(302, valid), Arguments.of(307, valid),
                Arguments.of(401, "{\"detail\":\"unauthorized\"}"),
                Arguments.of(407, "{\"detail\":\"proxy authentication required\"}"),
                Arguments.of(422, "{\"detail\":[{\"loc\":[\"body\",\"text\"],\"msg\":\"invalid text\"}]}"),
                Arguments.of(422, "{\"detail\":{\"error\":\"CHUNK_TOKEN_LIMIT_EXCEEDED\"}}"),
                Arguments.of(503, "{\"detail\":{\"error\":\"TOKENIZER_UNAVAILABLE\",\"cause\":\"Exception\"}}"),
                Arguments.of(500, "上游服务错误"));
    }

    private static Stream<Arguments> invalidRequests() {
        return Stream.of(Arguments.of(0L, "abc", ""), Arguments.of(-1L, "abc", ""),
                Arguments.of(1L, null, ""), Arguments.of(1L, "", ""),
                Arguments.of(1L, " \r\n\u00A0\u0085", ""), Arguments.of(1L, "abc", null),
                Arguments.of(1L, "\uD800", ""), Arguments.of(1L, "abc", "\uDC00"));
    }

    private static Stream<Arguments> invalidTimeouts() {
        return Stream.of(Arguments.of(0, 1000), Arguments.of(-1, 1000),
                Arguments.of(1000, 0), Arguments.of(1000, -1));
    }

    private static Map<String, Object> responseChunk(String text) {
        Map<String, Object> chunk = new LinkedHashMap<>();
        chunk.put("seq", 0);
        chunk.put("text", text);
        chunk.put("byte_start", 0);
        chunk.put("byte_end", text.getBytes(StandardCharsets.UTF_8).length);
        chunk.put("heading_path", "");
        chunk.put("token_count", 1);
        return chunk;
    }

    private static byte[] responseBody(String text) {
        return JSON.writeValueAsBytes(Map.of("chunks", List.of(responseChunk(text))));
    }

    private static Stream<Arguments> unicodeDocuments() throws IOException {
        try (var input = Objects.requireNonNull(ChunkClientTest.class.getResourceAsStream("/contracts/utf8-offsets.json"))) {
            JsonNode fixtures = JSON.readTree(input).get("cases");
            return StreamSupport.stream(fixtures.spliterator(), false)
                    .map(document -> Arguments.of(document.get("name").asString(), document));
        }
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
