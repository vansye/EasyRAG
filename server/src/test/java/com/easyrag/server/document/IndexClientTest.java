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

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.ConnectException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.stream.Stream;
import java.util.zip.GZIPOutputStream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTimeoutPreemptively;
import static org.junit.jupiter.api.Assertions.assertTrue;

class IndexClientTest {

    private static final long DOCUMENT_ID = 7;
    private final BlockingQueue<RecordedRequest> requests = new LinkedBlockingQueue<>();
    private HttpServer server;
    private ExecutorService serverExecutor;
    private volatile HttpHandler responder;
    private IndexClient client;

    @BeforeEach
    void startLoopbackServer() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        serverExecutor = Executors.newCachedThreadPool();
        server.setExecutor(serverExecutor);
        server.createContext("/", exchange -> {
            try (exchange) {
                requests.add(new RecordedRequest(exchange.getRequestMethod(), exchange.getRequestURI().toString(),
                        exchange.getRequestHeaders().getFirst("Content-Type"),
                        exchange.getRequestHeaders().getFirst("Accept"), exchange.getRequestBody().readAllBytes()));
                responder.handle(exchange);
            }
        });
        server.start();
        respond(200, "application/json", responseBody(1));
        client = new IndexClient(baseUrl(), 1000, 1000);
    }

    @AfterEach
    void stopLoopbackServer() throws InterruptedException {
        server.stop(0);
        serverExecutor.shutdownNow();
        assertTrue(serverExecutor.awaitTermination(5, TimeUnit.SECONDS));
    }

    @ParameterizedTest
    @ValueSource(longs = {1, DOCUMENT_ID, Long.MAX_VALUE})
    void deletesTheExactPositiveBigintIdWithoutARequestBody(long documentId) throws InterruptedException {
        assertThat(client.deleteDocument(documentId)).isEqualTo(1);

        RecordedRequest request = requests.poll(2, TimeUnit.SECONDS);
        assertThat(request).isNotNull();
        assertThat(request.method()).isEqualTo("DELETE");
        assertThat(request.path()).isEqualTo("/index/" + documentId);
        assertThat(request.contentType()).isNull();
        assertThat(request.accept()).isEqualTo("application/json");
        assertThat(request.body()).isEmpty();
        assertThat(requests).isEmpty();
    }

    @ParameterizedTest
    @ValueSource(ints = {0, 1, 2, Integer.MAX_VALUE})
    void returnsTheActualNonNegativeRemovalCount(int removed) {
        respond(200, "application/json", responseBody(removed));

        assertThat(client.deleteDocument(DOCUMENT_ID)).isEqualTo(removed);
        assertThat(requests).hasSize(1);
    }

    @Test
    void acceptsZeroWhenDeletionIsRepeated() {
        respond(200, "application/json", responseBody(2));
        assertThat(client.deleteDocument(DOCUMENT_ID)).isEqualTo(2);
        respond(200, "application/json", responseBody(0));
        assertThat(client.deleteDocument(DOCUMENT_ID)).isZero();

        assertThat(requests).extracting(RecordedRequest::method).containsExactly("DELETE", "DELETE");
        assertThat(requests).extracting(RecordedRequest::path).containsExactly("/index/7", "/index/7");
    }

    @ParameterizedTest
    @ValueSource(longs = {0, -1, Long.MIN_VALUE})
    void rejectsNonPositiveDocumentIdsBeforeSendingHttp(long documentId) {
        assertThrows(IllegalArgumentException.class, () -> client.deleteDocument(documentId));

        assertThat(requests).isEmpty();
    }

    @ParameterizedTest
    @MethodSource("invalidSuccessfulBodies")
    void rejectsMalformedCoercedNegativeOrOverflowingRemovalCounts(String body) {
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

    @ParameterizedTest
    @ValueSource(strings = {"application/json", "application/json; charset=utf-8",
            "Application/JSON; charset=\"UTF-8\"", "application/json; profile=\"v1\""})
    void acceptsAnExactJsonMediaTypeWithLegalParameters(String contentType) {
        respond(200, contentType, " \r\n{\"removed\":0}\n".getBytes(StandardCharsets.UTF_8));

        assertThat(client.deleteDocument(DOCUMENT_ID)).isZero();
        assertThat(requests).hasSize(1);
    }

    @ParameterizedTest(name = "HTTP {0}: {1}")
    @MethodSource("failedHttpResponses")
    void preservesNon200ResponsesWithoutRetryingOrFollowingRedirects(int status, String body) {
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
                () -> client.deleteDocument(DOCUMENT_ID));

        assertThat(failure.getStatusCode().value()).isEqualTo(status);
        assertThat(failure.getResponseBodyAsByteArray()).isEqualTo(bytes);
        assertThat(failure.getResponseBodyAsString()).isEqualTo(body);
        assertThat(failure.getResponseHeaders()).isNotNull();
        assertThat(failure.getResponseHeaders().getFirst("X-Upstream-Error")).isEqualTo("preserved");
        assertThat(failure.getResponseHeaders().getFirst("Location")).isEqualTo(baseUrl() + "/must-not-follow");
        if (status == 401) {
            assertThat(failure.getResponseHeaders().getFirst("WWW-Authenticate"))
                    .isEqualTo("Basic realm=\"rag-service\"");
        }
        if (status == 407) {
            assertThat(failure.getResponseHeaders().getFirst("Proxy-Authenticate"))
                    .isEqualTo("Basic realm=\"proxy\"");
        }
        assertThat(requests).hasSize(1);
        assertThat(requests.element().method()).isEqualTo("DELETE");
        assertThat(requests.element().path()).isEqualTo("/index/7");
    }

    @ParameterizedTest
    @ValueSource(strings = {"invalid", "application/json; charset=not-a-charset"})
    void preservesHttpErrorsEvenWhenTheContentTypeAndBodyAreMalformed(String contentType) {
        byte[] body = {(byte) 0xc3, (byte) 0x28};
        respond(503, contentType, body);

        RestClientResponseException failure = assertThrows(RestClientResponseException.class,
                () -> client.deleteDocument(DOCUMENT_ID));

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
                () -> client.deleteDocument(DOCUMENT_ID));

        assertThat(failure.getStatusCode().value()).isEqualTo(503);
        assertThat(failure.getResponseBodyAsByteArray()).isEqualTo(body);
        assertThat(failure.getResponseHeaders()).isNotNull();
        assertThat(failure.getResponseHeaders().getFirst("Content-Encoding")).isEqualTo("gzip");
        assertThat(requests).hasSize(1);
    }

    @ParameterizedTest
    @MethodSource("invalidTimeouts")
    void rejectsNonPositiveTimeouts(int connectMillis, int readMillis) {
        assertThrows(IllegalArgumentException.class, () -> new IndexClient(baseUrl(), connectMillis, readMillis));

        assertThat(requests).isEmpty();
    }

    @ParameterizedTest
    @ValueSource(strings = {"", "/", "/internal", "/internal/"})
    void preservesConfiguredBasePathPrefixes(String prefix) {
        IndexClient configured = new IndexClient(baseUrl() + prefix, 1000, 1000);

        assertThat(configured.deleteDocument(DOCUMENT_ID)).isEqualTo(1);

        assertThat(requests).hasSize(1);
        assertThat(requests.element().path()).isEqualTo(prefix.startsWith("/internal")
                ? "/internal/index/7" : "/index/7");
    }

    @ParameterizedTest
    @MethodSource("stalledResponses")
    void readTimeoutAppliesBeforeAndAfterHeadersWithoutRetry(int status, boolean afterHeaders) {
        CountDownLatch release = new CountDownLatch(1);
        byte[] body = responseBody(1);
        responder = exchange -> {
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            if (afterHeaders) {
                exchange.sendResponseHeaders(status, body.length);
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
                exchange.sendResponseHeaders(status, body.length);
                exchange.getResponseBody().write(body);
            }
        };
        IndexClient impatient = new IndexClient(baseUrl(), 1000, 100);
        try {
            ResourceAccessException failure = assertTimeoutPreemptively(Duration.ofSeconds(3),
                    () -> assertThrows(ResourceAccessException.class, () -> impatient.deleteDocument(DOCUMENT_ID)));

            assertThat(failure).hasCauseInstanceOf(IOException.class);
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
        try {
            ResourceAccessException failure = assertTimeoutPreemptively(Duration.ofSeconds(5),
                    () -> assertThrows(ResourceAccessException.class, () -> client.deleteDocument(DOCUMENT_ID)));

            assertThat(failure).hasCauseInstanceOf(IOException.class);
            assertThat(delivered.get()).isBetween(2, body.length - 1);
            assertThat(requests).hasSize(1);
        } finally {
            release.countDown();
        }
    }

    @ParameterizedTest
    @ValueSource(ints = {200, 503})
    void incompleteResponseBodiesRemainTransportFailures(int status) {
        byte[] body = responseBody(1);
        responder = exchange -> {
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(status, body.length);
            exchange.getResponseBody().write(body, 0, 1);
            exchange.getResponseBody().flush();
        };

        ResourceAccessException failure = assertThrows(ResourceAccessException.class,
                () -> client.deleteDocument(DOCUMENT_ID));

        assertThat(failure).hasCauseInstanceOf(IOException.class);
        assertThat(requests).hasSize(1);
    }

    @Test
    void disconnectBeforeResponseHeadersDoesNotResubmitTheDelete() {
        responder = exchange -> exchange.close();

        ResourceAccessException failure = assertThrows(ResourceAccessException.class,
                () -> client.deleteDocument(DOCUMENT_ID));

        assertThat(failure).hasCauseInstanceOf(IOException.class);
        assertThat(requests).hasSize(1);
    }

    @Test
    void connectionRefusalRemainsATransportFailure() {
        server.stop(0);

        ResourceAccessException failure = assertThrows(ResourceAccessException.class,
                () -> client.deleteDocument(DOCUMENT_ID));

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
                .withUserConfiguration(IndexClient.class)
                .run(context -> {
                    assertThat(context).hasNotFailed().hasSingleBean(IndexClient.class);
                    assertThat(requests).isEmpty();
                    assertThat(context.getBean(IndexClient.class).deleteDocument(DOCUMENT_ID)).isEqualTo(1);
                    assertThat(requests).hasSize(1);
                    assertThat(requests.element().path()).isEqualTo("/configured/index/7");
                });
    }

    private void assertInvalidResponse() {
        RestClientException failure = assertThrows(RestClientException.class,
                () -> client.deleteDocument(DOCUMENT_ID));

        assertThat(failure).isNotInstanceOf(RestClientResponseException.class)
                .isNotInstanceOf(ResourceAccessException.class).hasMessageContaining("Invalid /index response");
        assertThat(failure.getCause()).isNotNull();
        assertThat(requests).hasSize(1);
    }

    private static Stream<String> invalidSuccessfulBodies() {
        return Stream.of("", " ", "null", "[]", "[1]", "1", "true", "\"1\"", "{}", "{\"removed\":null}",
                "{\"removed\":-1}", "{\"removed\":2147483648}", "{\"removed\":9223372036854775808}",
                "{\"removed\":1.0}", "{\"removed\":1.9}", "{\"removed\":1e0}", "{\"removed\":\"1\"}",
                "{\"removed\":\"\"}", "{\"removed\":true}", "{\"removed\":false}", "{\"removed\":[]}", "{\"removed\":{}}",
                "{\"removed\":1,\"removed\":1}", "{\"removed\":-1,\"removed\":0}", "{\"removed\":1} {}",
                "{\"removed\":1} null", "{\"removed\":1} invalid", "{\"removed\":1", "{\"removed\":1,}");
    }

    private static Stream<Arguments> failedHttpResponses() {
        String successBody = "{\"removed\":0}";
        return Stream.of(Arguments.of(201, successBody), Arguments.of(202, successBody), Arguments.of(204, ""),
                Arguments.of(206, successBody), Arguments.of(301, successBody), Arguments.of(302, successBody),
                Arguments.of(303, successBody), Arguments.of(307, successBody), Arguments.of(308, successBody),
                Arguments.of(400, "{\"detail\":\"bad request\"}"),
                Arguments.of(401, "{\"detail\":\"unauthorized\"}"), Arguments.of(403, "{\"detail\":\"forbidden\"}"),
                Arguments.of(404, "{\"detail\":\"Not Found\"}"), Arguments.of(404, ""),
                Arguments.of(407, "{\"detail\":\"proxy authentication required\"}"),
                Arguments.of(422, "{\"detail\":[{\"loc\":[\"path\",\"document_id\"],\"msg\":\"invalid id\"}]}"),
                Arguments.of(429, "{\"detail\":\"rate limited\"}"),
                Arguments.of(503, "{\"detail\":{\"error\":\"INDEX_UNAVAILABLE\",\"cause\":\"RuntimeError\"}}"),
                Arguments.of(500, "上游服务错误"));
    }

    private static Stream<Arguments> invalidTimeouts() {
        return Stream.of(Arguments.of(0, 1000), Arguments.of(-1, 1000),
                Arguments.of(1000, 0), Arguments.of(1000, -1));
    }

    private static Stream<Arguments> stalledResponses() {
        return Stream.of(Arguments.of(200, false), Arguments.of(200, true),
                Arguments.of(503, false), Arguments.of(503, true));
    }

    private static byte[] responseBody(int removed) {
        return ("{\"removed\":" + removed + "}").getBytes(StandardCharsets.UTF_8);
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
