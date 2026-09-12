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
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Objects;
import java.util.Set;

@Component
public class EmbedClient {

    private static final JsonMapper JSON = JsonMapper.builder()
            .withCoercionConfig(LogicalType.Integer, coercion -> {
                coercion.setCoercion(CoercionInputShape.Float, CoercionAction.Fail);
                coercion.setCoercion(CoercionInputShape.String, CoercionAction.Fail);
                coercion.setCoercion(CoercionInputShape.Boolean, CoercionAction.Fail);
            })
            .enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
            .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
            .build();
    private static final ObjectReader EMBED_READER = JSON.readerFor(EmbedResponse.class);
    private final RestClient http;

    public EmbedClient(@Value("${rag.base-url}") String baseUrl,
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

    public int embed(long documentId, List<ChunkRepository.StoredChunk> chunks, List<String> tags) {
        EmbedRequest payload = prepareRequest(documentId, chunks, tags);
        return http.post().uri("/embed")
                .contentType(MediaType.APPLICATION_JSON).accept(MediaType.APPLICATION_JSON)
                .body(JSON.writeValueAsBytes(payload))
                .exchange((request, response) -> {
                    int status = response.getStatusCode().value();
                    byte[] body = response.getBody().readAllBytes();
                    if (status != 200) {
                        throw new RestClientResponseException("Python /embed returned HTTP " + status,
                                response.getStatusCode(), response.getStatusText(), response.getHeaders(),
                                body, StandardCharsets.UTF_8);
                    }
                    try {
                        MediaType contentType = response.getHeaders().getContentType();
                        if (contentType == null || !MediaType.APPLICATION_JSON.equalsTypeAndSubtype(contentType)
                                || body.length == 0) {
                            throw new IllegalArgumentException("expected a non-empty application/json body");
                        }
                        EmbedResponse result = EMBED_READER.readValue(body);
                        if (result == null || result.indexed() == null) {
                            throw new IllegalArgumentException("indexed must be a non-null integer");
                        }
                        if (result.indexed() != payload.chunks().size()) {
                            throw new IllegalArgumentException("indexed count mismatch: expected "
                                    + payload.chunks().size() + " but got " + result.indexed());
                        }
                        return result.indexed();
                    } catch (JacksonException | IllegalArgumentException failure) {
                        throw new RestClientException("Invalid /embed response", failure);
                    }
                });
    }

    private static EmbedRequest prepareRequest(long documentId, List<ChunkRepository.StoredChunk> chunks,
                                               List<String> tags) {
        if (documentId <= 0) {
            throw new IllegalArgumentException("document_id must be positive");
        }
        if (chunks == null || chunks.isEmpty() || chunks.stream().anyMatch(Objects::isNull)) {
            throw new IllegalArgumentException("chunks must be non-empty and contain no null elements");
        }
        if (tags == null) {
            throw new IllegalArgumentException("tags must not be null");
        }
        tags.forEach(tag -> requireUtf8(tag, "tags"));
        List<String> tagSnapshot = List.copyOf(tags);
        List<ChunkRepository.StoredChunk> ordered = chunks.stream()
                .sorted(Comparator.comparingInt(ChunkRepository.StoredChunk::seq)).toList();
        Set<Long> chunkIds = new HashSet<>();
        List<EmbedChunk> requestChunks = new ArrayList<>(ordered.size());
        for (int sequence = 0; sequence < ordered.size(); sequence++) {
            ChunkRepository.StoredChunk chunk = ordered.get(sequence);
            if (chunk.documentId() != documentId) {
                throw new IllegalArgumentException("all chunks must belong to document_id");
            }
            if (chunk.chunkId() <= 0 || !chunkIds.add(chunk.chunkId())) {
                throw new IllegalArgumentException("chunk_id must be positive and unique within a document");
            }
            if (chunk.seq() != sequence) {
                throw new IllegalArgumentException("seq must be consecutive from zero");
            }
            requireUtf8(chunk.text(), "text");
            if (chunk.text().codePoints().allMatch(character -> Character.isWhitespace(character)
                    || Character.isSpaceChar(character) || character == 0x85)) {
                throw new IllegalArgumentException("text must not be blank");
            }
            requireUtf8(chunk.headingPath(), "heading_path");
            requestChunks.add(new EmbedChunk(chunk.chunkId(), chunk.text(), chunk.headingPath(), tagSnapshot));
        }
        return new EmbedRequest(documentId, List.copyOf(requestChunks));
    }

    private static void requireUtf8(String text, String field) {
        if (text == null || !StandardCharsets.UTF_8.newEncoder().canEncode(text)) {
            throw new IllegalArgumentException(field + " must be a valid UTF-8 string");
        }
    }

    private record EmbedRequest(@JsonProperty("document_id") long documentId, List<EmbedChunk> chunks) {}

    private record EmbedChunk(@JsonProperty("chunk_id") long chunkId, String text,
                              @JsonProperty("heading_path") String headingPath, List<String> tags) {}

    private record EmbedResponse(Integer indexed) {}
}
