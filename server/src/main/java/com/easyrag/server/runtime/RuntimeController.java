package com.easyrag.server.runtime;

import com.easyrag.server.rag.RagOperationGate;
import com.fasterxml.jackson.annotation.JsonProperty;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.net.http.HttpClient;
import java.time.Duration;

/** Read-only workspace status. A probe never confirms readiness or exposes credentials. */
@RestController
public class RuntimeController {
    private final RagOperationGate gate;
    private final RestClient rag;

    public RuntimeController(RagOperationGate gate, @Value("${rag.base-url}") String baseUrl) {
        this.gate = gate;
        var transport = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3))
                .version(HttpClient.Version.HTTP_1_1).build();
        var factory = new JdkClientHttpRequestFactory(transport);
        factory.setReadTimeout(3000);
        this.rag = RestClient.builder().baseUrl(baseUrl).requestFactory(factory).build();
    }

    @GetMapping("/api/runtime")
    public WorkspaceRuntime runtime() {
        try {
            Models models = rag.get().uri("/runtime").retrieve().body(Models.class);
            if (models != null && models.llm() != null && models.embedding() != null) {
                return new WorkspaceRuntime(gate.state().name(), true, models.llm(), models.embedding());
            }
        } catch (RestClientException ignored) {
            // The document list remains usable when the RAG process is offline.
        }
        return new WorkspaceRuntime(gate.state().name(), false, null, null);
    }

    public record Llm(boolean configured, String provider, String model) {}
    public record Embedding(String provider, String model, int dim) {}
    public record Models(Llm llm, Embedding embedding) {}
    public record WorkspaceRuntime(String state,
                                   @JsonProperty("rag_available") boolean ragAvailable,
                                   Llm llm, Embedding embedding) {}
}
