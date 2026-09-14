package com.easyrag.server.runtime;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import java.net.http.HttpClient;
import java.time.Duration;
import java.util.Map;
import java.util.Set;

/** Model settings remain on the RAG service; browser responses never include a key. */
@RestController
@RequestMapping("/api/model-config")
public class ModelConfigController {
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final Set<String> FIELDS = Set.of("provider", "model", "base_url", "api_key");
    private final RestClient rag;

    public ModelConfigController(@Value("${rag.base-url}") String baseUrl) {
        var transport = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3))
                .version(HttpClient.Version.HTTP_1_1).build();
        var factory = new JdkClientHttpRequestFactory(transport);
        factory.setReadTimeout(3000);
        rag = RestClient.builder().baseUrl(baseUrl).requestFactory(factory).build();
    }

    @GetMapping
    public ResponseEntity<ModelConfig> read() {
        return exchange(HttpMethod.GET, null);
    }

    @PutMapping(consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<ModelConfig> save(@RequestBody JsonNode body) {
        if (!body.isObject() || !FIELDS.containsAll(body.propertyNames())
                || !text(body, "provider") || !text(body, "model") || !text(body, "base_url")
                || (body.has("api_key") && !body.get("api_key").isNull() && !body.get("api_key").isString())) {
            throw new InvalidConfiguration();
        }
        return exchange(HttpMethod.PUT, body);
    }

    @DeleteMapping
    public ResponseEntity<ModelConfig> reset() {
        return exchange(HttpMethod.DELETE, null);
    }

    private static boolean text(JsonNode body, String name) {
        return body.has(name) && body.get(name).isString() && !body.get(name).stringValue().isBlank();
    }

    private ResponseEntity<ModelConfig> exchange(HttpMethod method, JsonNode body) {
        var request = rag.method(method).uri("/model-config");
        if (body != null) {
            request.contentType(MediaType.APPLICATION_JSON).body(body);
        }
        ModelConfig result = request.retrieve().body(ModelConfig.class);
        if (result == null || result.provider() == null || result.model() == null
                || result.baseUrl() == null || result.source() == null
                || !Set.of("openai", "deepseek").contains(result.provider())
                || !Set.of("environment", "local").contains(result.source())
                || (result.configured() && result.model().isBlank())) {
            throw new RestClientException("invalid model configuration response");
        }
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(result);
    }

    @ExceptionHandler({InvalidConfiguration.class, HttpMessageNotReadableException.class})
    public ResponseEntity<Map<String, String>> invalid() {
        return ResponseEntity.badRequest().body(Map.of("error", "模型配置无效，请检查服务类型、接口地址、模型名和 API Key。"));
    }

    @ExceptionHandler(RestClientException.class)
    public ResponseEntity<Map<String, String>> unavailable(RestClientException failure) {
        if (failure instanceof RestClientResponseException response) {
            int status = response.getStatusCode().value();
            if (status == 400 || status == 422) {
                try {
                    var code = JSON.readTree(response.getResponseBodyAsByteArray()).path("detail").path("error");
                    if (code.isString() && code.stringValue().equals("API_KEY_REQUIRED")) {
                        return ResponseEntity.badRequest().body(Map.of("error", "请填写 API Key；更换服务类型或接口地址后，需要重新填写密钥。"));
                    }
                } catch (JacksonException ignored) {
                    // Only our known error code is forwarded; upstream bodies may contain secrets.
                }
                return invalid();
            }
            if (status == 503) {
                return ResponseEntity.status(503).body(Map.of("error", "模型配置暂时不可用，请重试或恢复启动配置。"));
            }
        }
        return ResponseEntity.status(502).body(Map.of("error", "无法连接模型配置服务，请稍后重试。"));
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record ModelConfig(boolean configured, String provider, String model,
                              @JsonProperty("base_url") String baseUrl,
                              @JsonProperty("api_key_configured") boolean apiKeyConfigured,
                              String source) {}

    static final class InvalidConfiguration extends RuntimeException {}
}
