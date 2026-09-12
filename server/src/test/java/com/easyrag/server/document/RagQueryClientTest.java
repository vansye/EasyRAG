package com.easyrag.server.document;

import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.web.client.RestClientResponseException;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.Executors;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * RagQueryClient 的 HTTP 契约测试：本地起真实 HttpServer（非 mock HTTP 栈），
 * 覆盖成功解析与畸形响应两类。照 EmbedClientTest 的服务模式。
 */
class RagQueryClientTest {

    private HttpServer server;
    private RagQueryClient client;

    private void startServer(int status, String body) throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.setExecutor(Executors.newSingleThreadExecutor());
        server.createContext("/query", exchange -> {
            byte[] payload = body.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(status, payload.length);
            try (OutputStream out = exchange.getResponseBody()) {
                out.write(payload);
            }
        });
        server.start();
        client = new RagQueryClient("http://127.0.0.1:" + server.getAddress().getPort(), 1000, 2000);
    }

    @AfterEach
    void stopServer() {
        if (server != null) {
            server.stop(0);
        }
    }

    @Test
    @DisplayName("完整响应被解析：answer/status/chunk_ids/trace")
    void parsesCompleteResponse() throws IOException {
        startServer(200, """
                {"answer": "ACID 是四个特性 [1]。", "status": "ANSWERED",
                 "chunk_ids": [101, 102],
                 "trace": {"rounds": 1, "decision": "SUFFICIENT", "retrieved": []}}
                """);

        RagQueryClient.QueryResponse response = client.ask("什么是 ACID？");

        assertThat(response.answer()).isEqualTo("ACID 是四个特性 [1]。");
        assertThat(response.status()).isEqualTo("ANSWERED");
        assertThat(response.chunkIds()).containsExactly(101L, 102L);
        assertThat(response.trace().path("decision").asString()).isEqualTo("SUFFICIENT");
    }

    @Test
    @DisplayName("拒答也是完整响应（status=REFUSED、chunk_ids 空数组）")
    void refusalIsParseable() throws IOException {
        startServer(200, """
                {"answer": "知识库中没有找到。", "status": "REFUSED", "chunk_ids": [], "trace": {"rounds": 1}}
                """);

        assertThat(client.ask("库外问题").status()).isEqualTo("REFUSED");
    }

    @Test
    @DisplayName("Python 503：抛 RestClientResponseException，携带状态码")
    void upstreamFailurePropagates() throws IOException {
        startServer(503, "{\"detail\": {\"error\": \"LLM_UNAVAILABLE\"}}");

        assertThatThrownBy(() -> client.ask("问题"))
                .isInstanceOf(RestClientResponseException.class)
                .hasMessageContaining("503");
    }

    @Test
    @DisplayName("畸形响应：缺 status / answer 为空 / 缺 trace 都视为调用失败")
    void malformedResponsesAreRejected() throws IOException {
        startServer(200, "{\"answer\": \"有答案\"}");
        assertThatThrownBy(() -> client.ask("问题")).isInstanceOf(IllegalArgumentException.class);
        stopServer();

        startServer(200, "{\"answer\": \"  \", \"status\": \"ANSWERED\", \"chunk_ids\": [], \"trace\": null}");
        assertThatThrownBy(() -> client.ask("问题")).isInstanceOf(IllegalArgumentException.class);
        stopServer();

        startServer(200, "{\"answer\": \"a\", \"status\": \"ANSWERED\", \"chunk_ids\": []}");
        assertThatThrownBy(() -> client.ask("问题")).isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    @DisplayName("非法入参：空与超 2000 码点的问题在发请求前被拒")
    void rejectsInvalidQuestionsLocally() throws IOException {
        startServer(200, "{\"answer\": \"a\", \"status\": \"ANSWERED\", \"chunk_ids\": [], \"trace\": {}}");
        assertThatThrownBy(() -> client.ask(" ")).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> client.ask("字".repeat(2001))).isInstanceOf(IllegalArgumentException.class);
        assertThat(client.ask("字".repeat(2000)).status()).isEqualTo("ANSWERED");
    }
}
