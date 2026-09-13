package com.easyrag.server.document;

import com.fasterxml.jackson.annotation.JsonProperty;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;
import tools.jackson.core.JacksonException;
import tools.jackson.core.StreamReadFeature;
import tools.jackson.databind.DeserializationFeature;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectReader;
import tools.jackson.databind.json.JsonMapper;

import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;

/**
 * 对 Python /query 的客户端（模块 C 的问答调用）。
 *
 * 校验口径与三个既有客户端一致：只认约定内的字段与形状，异常形状视为
 * 调用失败——宁可得 503 重来，也不能把残缺答案当成有效回答交给用户。
 * trace 是 U7（检索过程展示）与模块 D（评估）的数据源，原样透传。
 */
@Component
public class RagQueryClient {

    private static final JsonMapper JSON = JsonMapper.builder()
            .enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
            .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
            .build();
    private static final ObjectReader ANSWER_READER = JSON.readerFor(QueryResponse.class);
    private static final int MAX_QUESTION_CODE_POINTS = 2000;
    private final RestClient http;

    public RagQueryClient(@Value("${rag.base-url}") String baseUrl,
                          @Value("${rag.connect-timeout-ms}") int connectTimeoutMillis,
                          @Value("${rag.query-read-timeout-ms}") int readTimeoutMillis) {
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

    public QueryResponse ask(String question) {
        if (question == null || question.isBlank()
                || question.codePointCount(0, question.length()) > MAX_QUESTION_CODE_POINTS) {
            throw new IllegalArgumentException("question must be 1.." + MAX_QUESTION_CODE_POINTS + " code points");
        }
        byte[] payload = JSON.writeValueAsBytes(new QueryRequest(question.strip()));
        return http.post().uri("/query")
                .contentType(MediaType.APPLICATION_JSON).accept(MediaType.APPLICATION_JSON)
                .body(payload)
                .exchange((request, response) -> {
                    int status = response.getStatusCode().value();
                    byte[] body = response.getBody().readAllBytes();
                    if (status != 200) {
                        throw new RestClientResponseException("Python /query returned HTTP " + status,
                                response.getStatusCode(), response.getStatusText(), response.getHeaders(),
                                body, StandardCharsets.UTF_8);
                    }
                    try {
                        MediaType contentType = response.getHeaders().getContentType();
                        if (contentType == null || !MediaType.APPLICATION_JSON.equalsTypeAndSubtype(contentType)
                                || body.length == 0) {
                            throw new IllegalArgumentException("expected a non-empty application/json body");
                        }
                        QueryResponse result = ANSWER_READER.readValue(body);
                        if (result == null || result.answer() == null || result.answer().isBlank()
                                || result.status() == null) {
                            throw new IllegalArgumentException("answer and status are required");
                        }
                        if (result.trace() == null) {
                            throw new IllegalArgumentException("trace is required");
                        }
                        return result;
                    } catch (JacksonException | IllegalArgumentException invalidResponse) {
                        throw new IllegalArgumentException("invalid /query response", invalidResponse);
                    }
                });
    }

    private record QueryRequest(@JsonProperty("question") String question) {}

    /** chunk_ids 不在此校验唯一性：Python 侧已去重排序，重复属协议违约，溯源查库自然报错。 */
    public record QueryResponse(@JsonProperty("answer") String answer,
                                @JsonProperty("status") String status,
                                @JsonProperty("chunk_ids") List<Long> chunkIds,
                                @JsonProperty("trace") JsonNode trace) {}
}
