package com.easyrag.server.document;

import com.fasterxml.jackson.annotation.JsonProperty;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;
import tools.jackson.core.JacksonException;
import tools.jackson.core.StreamReadFeature;
import tools.jackson.databind.DeserializationFeature;
import tools.jackson.databind.ObjectReader;
import tools.jackson.databind.cfg.CoercionAction;
import tools.jackson.databind.cfg.CoercionInputShape;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.type.LogicalType;

import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.time.Duration;

@Component
public class ChunkClient {

    private static final JsonMapper JSON = JsonMapper.builder()
            .withCoercionConfig(LogicalType.Integer, coercion -> {
                coercion.setCoercion(CoercionInputShape.Float, CoercionAction.Fail);
                coercion.setCoercion(CoercionInputShape.String, CoercionAction.Fail);
                coercion.setCoercion(CoercionInputShape.Boolean, CoercionAction.Fail);
            })
            .withCoercionConfig(LogicalType.Textual, coercion -> {
                coercion.setCoercion(CoercionInputShape.Integer, CoercionAction.Fail);
                coercion.setCoercion(CoercionInputShape.Float, CoercionAction.Fail);
                coercion.setCoercion(CoercionInputShape.Boolean, CoercionAction.Fail);
            })
            .enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
            .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
            .build();
    private static final ObjectReader CHUNK_READER = JSON.readerFor(ChunkBatch.class);
    private final RestClient http;

    public ChunkClient(@Value("${rag.base-url}") String baseUrl,
                       @Value("${rag.connect-timeout-ms}") int connectTimeoutMillis,
                       @Value("${rag.read-timeout-ms}") int readTimeoutMillis) {
        if (connectTimeoutMillis <= 0 || readTimeoutMillis <= 0) {
            throw new IllegalArgumentException("rag timeouts must be positive milliseconds");
        }
        HttpClient transport = HttpClient.newBuilder().connectTimeout(Duration.ofMillis(connectTimeoutMillis))
                .followRedirects(HttpClient.Redirect.NEVER).version(HttpClient.Version.HTTP_1_1).build();
        JdkClientHttpRequestFactory requestFactory = new JdkClientHttpRequestFactory(transport);
        requestFactory.setReadTimeout(readTimeoutMillis);
        requestFactory.enableCompression(false);
        http = RestClient.builder().baseUrl(baseUrl).requestFactory(requestFactory).build();
    }

    public ChunkBatch chunk(long documentId, String text, String title) {
        return http.post().uri("/chunk")
                .contentType(MediaType.APPLICATION_JSON).accept(MediaType.APPLICATION_JSON)
                .body(JSON.writeValueAsBytes(new ChunkRequest(documentId, text, title)))
                .exchange((request, response) -> {
                    int status = response.getStatusCode().value();
                    byte[] body = response.getBody().readAllBytes();
                    if (status != 200) {
                        throw new RestClientResponseException("Python /chunk returned HTTP " + status,
                                response.getStatusCode(), response.getStatusText(), response.getHeaders(),
                                body, StandardCharsets.UTF_8);
                    }
                    try {
                        MediaType contentType = response.getHeaders().getContentType();
                        if (contentType == null || !MediaType.APPLICATION_JSON.equalsTypeAndSubtype(contentType)
                                || body.length == 0) {
                            throw new IllegalArgumentException("expected a non-empty application/json body");
                        }
                        ChunkBatch batch = CHUNK_READER.readValue(body);
                        if (batch == null) {
                            throw new IllegalArgumentException("chunk response must not be null");
                        }
                        batch.validateAgainst(text);
                        return batch;
                    } catch (JacksonException | IllegalArgumentException failure) {
                        throw new RestClientException("Invalid /chunk response", failure);
                    }
                });
    }

    private record ChunkRequest(@JsonProperty("document_id") long documentId, String text, String title) {

        private ChunkRequest {
            if (documentId <= 0) {
                throw new IllegalArgumentException("document_id must be positive");
            }
            if (text == null || title == null || !StandardCharsets.UTF_8.newEncoder().canEncode(text)
                    || !StandardCharsets.UTF_8.newEncoder().canEncode(title)) {
                throw new IllegalArgumentException("text and title must be valid UTF-8 strings");
            }
            if (text.codePoints().allMatch(character -> Character.isWhitespace(character)
                    || Character.isSpaceChar(character) || character == 0x85)) {
                throw new IllegalArgumentException("text must not be blank");
            }
        }
    }
}
