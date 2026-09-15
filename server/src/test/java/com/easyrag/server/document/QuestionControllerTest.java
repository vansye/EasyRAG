package com.easyrag.server.document;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.easyrag.server.rag.RagOperationGate;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClientResponseException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import java.net.http.HttpTimeoutException;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Optional;
import java.util.regex.Pattern;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.BDDMockito.given;
import static org.mockito.BDDMockito.then;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * /api/questions 的 HTTP 契约测试。闸门是 mock（@WebMvcTest 切片不装 rag 包
 * bean），状态语义用真实实例的迁移规则驱动——见 makeGateReady/makeGateBusy。
 * Python 客户端与出处查询是 mock——任何环境（含 CI）都能跑。
 *
 * 守住的行为：问答并发放行、变更中/未就绪明确 503（不调 LLM、不伪装成
 * 无结果）、拒答是正常返回、出处由本层补全、空/超长问题 400。
 */
@WebMvcTest(QuestionController.class)
class QuestionControllerTest {

    private static final JsonMapper JSON = JsonMapper.builder().build();

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private RagQueryClient ragQueryClient;

    @MockitoBean
    private DocumentQueryRepository documents;

    @MockitoBean
    private RagOperationGate gate;

    /** 真实闸门驱动状态迁移（Lease 构造器私有，不能伪造），mock 只做转发。 */
    private final RagOperationGate realGate = new RagOperationGate();

    private void makeGateReady() {
        try (var lease = realGate.tryAcquire(RagOperationGate.Operation.RECOVERY).lease().orElseThrow()) {
            lease.confirmCompletion();
        }
        given(gate.tryAcquire(any(RagOperationGate.Operation.class))).willAnswer(
                invocation -> realGate.tryAcquire(invocation.getArgument(0)));
        given(gate.state()).willAnswer(invocation -> realGate.state());
    }

    private void makeGateBusy() {
        makeGateReady();
        // 真实闸门持有 MUTATION 租约直至测试结束
        realGate.tryAcquire(RagOperationGate.Operation.MUTATION);
    }

    private static RagQueryClient.QueryResponse answered(String answer, String status, List<Long> chunkIds) {
        JsonNode trace = JSON.readTree(
                "{\"rounds\":1,\"retrieved\":[{\"chunk_id\":101,\"rank\":1}],\"decision\":\"SUFFICIENT\"}");
        return new RagQueryClient.QueryResponse(answer, status, chunkIds, trace);
    }

    @Test
    @DisplayName("直答路径：200，answer + status + sources（含 byte 偏移）+ trace 透传")
    void answersWithSourcesAndTrace() throws Exception {
        makeGateReady();
        given(ragQueryClient.ask("什么是 ACID？")).willReturn(
                answered("ACID 是四个特性 [1]。", "ANSWERED", List.of(101L)));
        given(documents.findSources(List.of(101L))).willReturn(List.of(
                new DocumentQueryRepository.ChunkSource(101L, 11L, "数据库事务 ACID 特性",
                        "ACID 指原子性……", 333, 662, "详细 > 概念")));

        mockMvc.perform(post("/api/questions")
                        .contentType("application/json").content("{\"question\": \"什么是 ACID？\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.answer").value("ACID 是四个特性 [1]。"))
                .andExpect(jsonPath("$.status").value("ANSWERED"))
                .andExpect(jsonPath("$.sources[0].chunk_id").value(101))
                .andExpect(jsonPath("$.sources[0].title").value("数据库事务 ACID 特性"))
                .andExpect(jsonPath("$.sources[0].byte_start").value(333))
                .andExpect(jsonPath("$.sources[0].byte_end").value(662))
                .andExpect(jsonPath("$.trace.decision").value("SUFFICIENT"));

        then(ragQueryClient).should().ask("什么是 ACID？");
    }

    @Test
    @DisplayName("拒答路径：200 + REFUSED，chunk_ids 为空则 sources 为空")
    void refusalIsANormalResponse() throws Exception {
        makeGateReady();
        given(ragQueryClient.ask(anyString())).willReturn(
                answered("知识库中没有找到能回答这个问题的内容。", "REFUSED", List.of()));
        given(documents.findSources(List.of())).willReturn(List.of());

        mockMvc.perform(post("/api/questions")
                        .contentType("application/json").content("{\"question\": \"Redis GEO 怎么用？\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("REFUSED"))
                .andExpect(jsonPath("$.answer").value("知识库中没有找到能回答这个问题的内容。"))
                .andExpect(jsonPath("$.sources").isEmpty());
    }

    @Test
    @DisplayName("索引变更进行中：503 携带状态，不调 Python、不查出处")
    void mutatingGateBlocksQuestionsWithoutCallingLlm() throws Exception {
        makeGateReady();
        makeGateBusy();

        mockMvc.perform(post("/api/questions")
                        .contentType("application/json").content("{\"question\": \"问题\"}"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.state").value("MUTATING"));

        then(ragQueryClient).shouldHaveNoInteractions();
        then(documents).shouldHaveNoInteractions();
    }

    @Test
    @DisplayName("闸门未就绪（RECOVERY_REQUIRED）：503，同样不调 LLM")
    void recoveryRequiredGateRefusesToAnswer() throws Exception {
        given(gate.tryAcquire(any())).willReturn(
                new RagOperationGate.Admission(RagOperationGate.State.RECOVERY_REQUIRED, Optional.empty()));

        mockMvc.perform(post("/api/questions")
                        .contentType("application/json").content("{\"question\": \"问题\"}"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.state").value("RECOVERY_REQUIRED"));
        then(ragQueryClient).shouldHaveNoInteractions();
    }

    @Test
    @DisplayName("空问题与超长问题：400")
    void rejectsBlankAndOverlongQuestions() throws Exception {
        mockMvc.perform(post("/api/questions")
                        .contentType("application/json").content("{\"question\": \"  \"}"))
                .andExpect(status().isBadRequest());
        mockMvc.perform(post("/api/questions")
                        .contentType("application/json")
                        .content("{\"question\": \"" + "字".repeat(2001) + "\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    @DisplayName("Python /query 失败：502，如实归因上游")
    void pythonFailureMapsToBadGateway() throws Exception {
        makeGateReady();
        given(ragQueryClient.ask(anyString()))
                .willThrow(new org.springframework.web.client.ResourceAccessException("connection refused"));

        mockMvc.perform(post("/api/questions")
                        .contentType("application/json").content("{\"question\": \"问题\"}"))
                .andExpect(status().isBadGateway());
    }

    @ParameterizedTest
    @MethodSource("upstreamFailures")
    @DisplayName("问答失败日志包含默认格式可见的类别和耗时，不泄露请求或上游细节")
    void logsSafeFailureDiagnosticsAndReleasesQueryLease(
            RuntimeException failure, String causeType, String upstreamStatus) throws Exception {
        makeGateReady();
        given(ragQueryClient.ask(anyString())).willAnswer(invocation -> {
            Thread.sleep(25);
            throw failure;
        });
        Logger logger = (Logger) LoggerFactory.getLogger(QuestionController.class);
        ListAppender<ILoggingEvent> events = new ListAppender<>();
        events.start();
        logger.addAppender(events);
        try {
            mockMvc.perform(post("/api/questions").contentType("application/json")
                            .header("Authorization", "Bearer private-api-key")
                            .content("{\"question\":\"private-question\"}"))
                    .andExpect(status().isBadGateway())
                    .andExpect(jsonPath("$.error").value("问答服务暂时不可用"));

            assertThat(realGate.state()).isEqualTo(RagOperationGate.State.READY);
            then(documents).shouldHaveNoInteractions();
            assertThat(events.list).hasSize(1);
            ILoggingEvent event = events.list.get(0);
            assertThat(event.getThrowableProxy()).isNull();
            String message = event.getFormattedMessage();
            assertThat(message).startsWith("question_upstream_failure ")
                    .contains("exception_type=" + failure.getClass().getSimpleName(),
                            "cause_type=" + causeType, "upstream_status=" + upstreamStatus)
                    .doesNotContain("private-question", "private-model.example.test", "private-api-key",
                            "private-header", "private-response-body", "private-status-text", "Authorization");
            var elapsed = Pattern.compile("duration_ms=(\\d+)").matcher(message);
            assertThat(elapsed.find()).isTrue();
            assertThat(Long.parseLong(elapsed.group(1))).isGreaterThanOrEqualTo(20);
        } finally {
            logger.detachAppender(events);
            events.stop();
        }
    }

    static Stream<Arguments> upstreamFailures() {
        HttpHeaders headers = new HttpHeaders();
        headers.setBearerAuth("private-api-key");
        headers.set("X-Private-Header", "private-header");
        return Stream.of(
                Arguments.of(new ResourceAccessException("https://private-model.example.test/v1",
                        new HttpTimeoutException("Authorization: Bearer private-api-key")),
                        "HttpTimeoutException", "none"),
                Arguments.of(new RestClientResponseException("private-response-body",
                        HttpStatus.SERVICE_UNAVAILABLE, "private-status-text", headers,
                        "private-response-body".getBytes(StandardCharsets.UTF_8), StandardCharsets.UTF_8),
                        "RestClientResponseException", "503"),
                Arguments.of(new IllegalArgumentException("private-response-body",
                        new IllegalArgumentException("https://private-model.example.test/v1")),
                        "IllegalArgumentException", "none"));
    }
}
