package com.easyrag.server.runtime;

import com.easyrag.server.rag.RagOperationGate;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import tools.jackson.databind.json.JsonMapper;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.clearInvocations;
import static org.mockito.Mockito.spy;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoMoreInteractions;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

class RuntimeControllerTest {

    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final String MODELS = """
            {"llm":{"configured":true,"provider":"openai","model":"answer-model"},
             "embedding":{"provider":"ollama","model":"bge-m3","dim":1024}}
            """;
    private final List<String> requests = new CopyOnWriteArrayList<>();
    private HttpServer server;
    private ExecutorService worker;
    private volatile int upstreamStatus = 200;
    private volatile String requestUpgrade;

    @BeforeEach
    void startLoopbackPython() throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        worker = Executors.newSingleThreadExecutor();
        server.setExecutor(worker);
        server.createContext("/", exchange -> {
            try (exchange) {
                requests.add(exchange.getRequestMethod() + " " + exchange.getRequestURI().getPath());
                requestUpgrade = exchange.getRequestHeaders().getFirst("Upgrade");
                byte[] body = MODELS.getBytes(StandardCharsets.UTF_8);
                exchange.getResponseHeaders().set("Content-Type", "application/json");
                exchange.sendResponseHeaders(upstreamStatus, body.length);
                exchange.getResponseBody().write(body);
            }
        });
        server.start();
    }

    @AfterEach
    void stopLoopbackPython() throws Exception {
        server.stop(0);
        worker.shutdownNow();
        assertThat(worker.awaitTermination(5, TimeUnit.SECONDS)).isTrue();
    }

    @Test
    void returnsOnlyPublicModelFieldsAlongsideTheCurrentGateState() throws Exception {
        var response = mvc(new RagOperationGate()).perform(get("/api/runtime"))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsByteArray();

        assertThat(JSON.readTree(response)).isEqualTo(JSON.readTree("""
                {"state":"RECOVERY_REQUIRED","rag_available":true,
                 "llm":{"configured":true,"provider":"openai","model":"answer-model"},
                 "embedding":{"provider":"ollama","model":"bge-m3","dim":1024}}
                """));
        assertThat(requests).containsExactly("GET /runtime");
        assertThat(requestUpgrade).isNull();
    }

    @Test
    void unavailablePythonKeepsRuntimeReadableAndReportsRagUnavailable() throws Exception {
        var gate = new RagOperationGate();
        var mvc = mvc(gate);
        server.stop(0);

        var response = mvc.perform(get("/api/runtime")).andExpect(status().isOk())
                .andReturn().getResponse().getContentAsByteArray();

        assertThat(JSON.readTree(response)).isEqualTo(JSON.readTree("""
                {"state":"RECOVERY_REQUIRED","rag_available":false,"llm":null,"embedding":null}
                """));
        assertThat(gate.state()).isEqualTo(RagOperationGate.State.RECOVERY_REQUIRED);
        assertThat(requests).isEmpty();
    }

    @Test
    void pythonHttpFailureAlsoReportsUnavailableWithoutConfirmingReadiness() throws Exception {
        upstreamStatus = 503;
        var gate = new RagOperationGate();

        mvc(gate).perform(get("/api/runtime"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.rag_available").value(false))
                .andExpect(jsonPath("$.state").value("RECOVERY_REQUIRED"));

        assertThat(gate.state()).isEqualTo(RagOperationGate.State.RECOVERY_REQUIRED);
        assertThat(requests).containsExactly("GET /runtime");
    }

    @ParameterizedTest
    @EnumSource(RagOperationGate.State.class)
    void probingNeverAcquiresALeaseOrChangesAnyGateState(RagOperationGate.State state) throws Exception {
        var gate = spy(new RagOperationGate());
        RagOperationGate.Lease held = null;
        if (state != RagOperationGate.State.RECOVERY_REQUIRED && state != RagOperationGate.State.RECOVERING) {
            gate.tryAcquire(RagOperationGate.Operation.RECOVERY).lease().orElseThrow().confirmCompletion();
        }
        if (state == RagOperationGate.State.QUERYING) {
            held = gate.tryAcquire(RagOperationGate.Operation.QUERY).lease().orElseThrow();
        } else if (state == RagOperationGate.State.MUTATING) {
            held = gate.tryAcquire(RagOperationGate.Operation.MUTATION).lease().orElseThrow();
        } else if (state == RagOperationGate.State.RECOVERING) {
            held = gate.tryAcquire(RagOperationGate.Operation.RECOVERY).lease().orElseThrow();
        }
        clearInvocations(gate);
        try {
            mvc(gate).perform(get("/api/runtime"))
                    .andExpect(status().isOk())
                    .andExpect(jsonPath("$.state").value(state.name()));

            verify(gate).state();
            verifyNoMoreInteractions(gate);
            assertThat(gate.state()).isEqualTo(state);
            assertThat(requests).containsExactly("GET /runtime");
        } finally {
            if (held != null) {
                held.close();
            }
        }
    }

    private MockMvc mvc(RagOperationGate gate) {
        var controller = new RuntimeController(gate, "http://127.0.0.1:" + server.getAddress().getPort());
        return MockMvcBuilders.standaloneSetup(controller).build();
    }
}
