package com.easyrag.server.runtime;

import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import tools.jackson.databind.json.JsonMapper;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

class ModelConfigControllerTest {
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final String CONFIG = """
            {"configured":true,"provider":"openai","model":"configured-model",
             "base_url":"https://models.example.test/v1","api_key_configured":true,"source":"local",
             "api_key":"private-upstream-key","internal":"private-upstream-detail"}
            """;
    private final List<String> requests = new CopyOnWriteArrayList<>();
    private HttpServer python;
    private MockMvc mvc;
    private volatile int upstreamStatus = 200;
    private volatile String upstreamBody = CONFIG;
    private volatile String requestBody;
    private volatile String requestUpgrade;

    @BeforeEach
    void startPython() throws Exception {
        python = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        python.createContext("/", exchange -> {
            try (exchange) {
                requests.add(exchange.getRequestMethod() + " " + exchange.getRequestURI().getPath());
                requestUpgrade = exchange.getRequestHeaders().getFirst("Upgrade");
                requestBody = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
                byte[] body = upstreamBody.getBytes(StandardCharsets.UTF_8);
                exchange.getResponseHeaders().set("Content-Type", "application/json");
                exchange.sendResponseHeaders(upstreamStatus, body.length);
                exchange.getResponseBody().write(body);
            }
        });
        python.start();
        mvc = MockMvcBuilders.standaloneSetup(new ModelConfigController(
                "http://127.0.0.1:" + python.getAddress().getPort())).build();
    }

    @AfterEach
    void stopPython() {
        python.stop(0);
    }

    @Test
    void readsOnlyPublicConfigurationFieldsWithoutCaching() throws Exception {
        String body = mvc.perform(get("/api/model-config"))
                .andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(jsonPath("$.model").value("configured-model"))
                .andExpect(jsonPath("$.api_key_configured").value(true))
                .andExpect(jsonPath("$.api_key").doesNotExist())
                .andReturn().getResponse().getContentAsString();
        assertThat(body).doesNotContain("private-upstream");
        assertThat(requests).containsExactly("GET /model-config");
    }

    @Test
    void forwardsTheWriteOnlyKeyAndReturnsOnlyPublicFields() throws Exception {
        String update = """
                {"provider":"openai","model":"new-model","base_url":"https://models.example.test/v1",
                 "api_key":"private-browser-key"}
                """;
        String body = mvc.perform(put("/api/model-config").contentType(MediaType.APPLICATION_JSON).content(update))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString();
        assertThat(JSON.readTree(requestBody)).isEqualTo(JSON.readTree(update));
        // Uvicorn receives an empty body when the JDK attempts an h2c upgrade.
        assertThat(requestUpgrade).isNull();
        assertThat(body).doesNotContain("private-browser-key", "private-upstream");
        assertThat(requests).containsExactly("PUT /model-config");
    }

    @Test
    void anOmittedKeyCanBeKeptByTheUpstreamService() throws Exception {
        mvc.perform(put("/api/model-config").contentType(MediaType.APPLICATION_JSON).content("""
                {"provider":"openai","model":"new-model","base_url":"https://models.example.test/v1"}
                """)).andExpect(status().isOk());
        assertThat(JSON.readTree(requestBody).has("api_key")).isFalse();
    }

    @Test
    void resettingIsForwardedExplicitly() throws Exception {
        mvc.perform(delete("/api/model-config"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.model").value("configured-model"));
        assertThat(requests).containsExactly("DELETE /model-config");
    }

    @ParameterizedTest
    @ValueSource(strings = {"{}", "[]", "\"private-json-key\"", "{\"api_key\":\"private-json-key\"",
            "{\"provider\":\"openai\",\"model\":123,\"base_url\":\"https://example.test\"}",
            "{\"provider\":\"openai\",\"model\":\"m\",\"base_url\":\"https://example.test\",\"api_key\":{}}",
            "{\"provider\":\"openai\",\"model\":\"m\",\"base_url\":\"https://example.test\",\"embedding_model\":\"other\"}"})
    void invalidBodiesNeverReachPythonOrEchoInput(String input) throws Exception {
        String body = mvc.perform(put("/api/model-config").contentType(MediaType.APPLICATION_JSON).content(input))
                .andExpect(status().isBadRequest()).andExpect(jsonPath("$.error").isNotEmpty())
                .andReturn().getResponse().getContentAsString();
        assertThat(body).doesNotContain("private-json-key");
        assertThat(requests).isEmpty();
    }

    @Test
    void destinationChangeWithoutANewKeyHasAnActionableError() throws Exception {
        upstreamStatus = 400;
        upstreamBody = "{\"detail\":{\"error\":\"API_KEY_REQUIRED\",\"input\":\"private-key\"}}";
        String body = mvc.perform(put("/api/model-config").contentType(MediaType.APPLICATION_JSON).content("""
                {"provider":"openai","model":"m","base_url":"https://new.example.test/v1"}
                """)).andExpect(status().isBadRequest()).andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);
        assertThat(body).contains("API Key").doesNotContain("private-key");
    }

    @ParameterizedTest
    @ValueSource(ints = {400, 422, 500, 503})
    void upstreamErrorsNeverLeakTheirResponseBodies(int code) throws Exception {
        upstreamStatus = code;
        upstreamBody = "{\"detail\":\"private-upstream-key\"}";
        int expected = code == 400 || code == 422 ? 400 : code == 503 ? 503 : 502;
        String body = mvc.perform(get("/api/model-config")).andExpect(status().is(expected))
                .andReturn().getResponse().getContentAsString();
        assertThat(body).doesNotContain("private-upstream");
    }

    @ParameterizedTest
    @ValueSource(strings = {"not-json private-upstream-key", "{}", "null"})
    void invalidUpstreamResponsesAreReportedAsFailures(String input) throws Exception {
        upstreamBody = input;
        String body = mvc.perform(get("/api/model-config")).andExpect(status().isBadGateway())
                .andReturn().getResponse().getContentAsString();
        assertThat(body).doesNotContain("private-upstream");
    }

    @Test
    void unavailablePythonIsReportedWithoutPretendingToSave() throws Exception {
        python.stop(0);
        mvc.perform(delete("/api/model-config")).andExpect(status().isBadGateway());
    }
}
