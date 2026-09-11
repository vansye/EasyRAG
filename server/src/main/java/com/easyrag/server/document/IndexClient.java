package com.easyrag.server.document;

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
public class IndexClient {

    private static final ObjectReader DELETE_READER = JsonMapper.builder()
            .withCoercionConfig(LogicalType.Integer, coercion -> {
                coercion.setCoercion(CoercionInputShape.Float, CoercionAction.Fail);
                coercion.setCoercion(CoercionInputShape.String, CoercionAction.Fail);
                coercion.setCoercion(CoercionInputShape.Boolean, CoercionAction.Fail);
            })
            .enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
            .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
            .build().readerFor(DeleteResponse.class);
    private final RestClient http;

    public IndexClient(@Value("${rag.base-url}") String baseUrl,
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

    public int deleteDocument(long documentId) {
        if (documentId <= 0) {
            throw new IllegalArgumentException("document_id must be positive");
        }
        return http.delete().uri("/index/{documentId}", documentId).accept(MediaType.APPLICATION_JSON)
                .exchange((request, response) -> {
                    int status = response.getStatusCode().value();
                    byte[] body = response.getBody().readAllBytes();
                    if (status != 200) {
                        throw new RestClientResponseException("Python DELETE /index returned HTTP " + status,
                                response.getStatusCode(), response.getStatusText(), response.getHeaders(),
                                body, StandardCharsets.UTF_8);
                    }
                    try {
                        MediaType contentType = response.getHeaders().getContentType();
                        if (contentType == null || !MediaType.APPLICATION_JSON.equalsTypeAndSubtype(contentType)
                                || body.length == 0) {
                            throw new IllegalArgumentException("expected a non-empty application/json body");
                        }
                        DeleteResponse result = DELETE_READER.readValue(body);
                        if (result == null || result.removed() == null || result.removed() < 0) {
                            throw new IllegalArgumentException("removed must be a non-negative integer");
                        }
                        return result.removed();
                    } catch (JacksonException | IllegalArgumentException failure) {
                        throw new RestClientException("Invalid /index response", failure);
                    }
                });
    }

    private record DeleteResponse(Integer removed) {}
}
