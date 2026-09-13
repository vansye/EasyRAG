package com.easyrag.server.document;

import com.easyrag.server.rag.RagOperationGate;
import com.easyrag.server.rag.RagOperationGate.Operation;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;
import tools.jackson.databind.JsonNode;

import java.util.List;
import java.util.Map;

/**
 * POST /api/questions —— 问答入口（U4 / U5，模块 C 的对外端点）。
 *
 * 闸门用 QUERY 租约：问答之间并发、与索引变更互斥（既有语义，无需改动）。
 * 未就绪/变更中不调 LLM，明确 503——不把"现在不能问"伪装成"检索无结果"，
 * 两种反馈对用户意味着完全不同的事。
 *
 * 出处补全在本层完成：Python 只认识 chunk_id（派生索引无业务数据），
 * 标题与字节偏移是 MySQL 的真相，去查就违反"B/C 不反向调用 A"。
 */
@RestController
@RequestMapping("/api/questions")
public class QuestionController {

    private static final Logger LOGGER = LoggerFactory.getLogger(QuestionController.class);

    private final RagOperationGate gate;
    private final RagQueryClient ragQueryClient;
    private final DocumentQueryRepository documents;

    public QuestionController(RagOperationGate gate, RagQueryClient ragQueryClient,
                              DocumentQueryRepository documents) {
        this.gate = gate;
        this.ragQueryClient = ragQueryClient;
        this.documents = documents;
    }

    @PostMapping
    public AnsweredQuestion ask(@RequestBody QuestionRequest request) {
        String question = request.question();
        if (question == null || question.isBlank()
                || question.codePointCount(0, question.length()) > 2000) {
            throw new BadRequest("question 不能为空且不超过 2000 字");
        }
        RagOperationGate.Admission admission = gate.tryAcquire(Operation.QUERY);
        if (admission.lease().isEmpty()) {
            // RECOVERY_REQUIRED / MUTATING / RECOVERING：明确反馈不就绪
            throw new Unavailable(admission.state().name());
        }
        try (RagOperationGate.Lease lease = admission.lease().orElseThrow()) {
            RagQueryClient.QueryResponse answer = ragQueryClient.ask(question);
            List<DocumentQueryRepository.ChunkSource> sources =
                    documents.findSources(answer.chunkIds() == null ? List.of() : answer.chunkIds());
            return new AnsweredQuestion(answer.answer(), answer.status(), sources, answer.trace());
        }
    }

    public record QuestionRequest(String question) {}

    public record AnsweredQuestion(String answer, String status,
                                   List<DocumentQueryRepository.ChunkSource> sources,
                                   JsonNode trace) {}

    @ExceptionHandler(BadRequest.class)
    public ResponseEntity<Map<String, String>> badRequest(BadRequest failure) {
        return ResponseEntity.badRequest().body(Map.of("error", failure.getMessage()));
    }

    /** 闸门不可用：503 携带状态，前端可区分"稍后再试"与"问题无效"。 */
    @ExceptionHandler(Unavailable.class)
    public ResponseEntity<Map<String, String>> unavailable(Unavailable failure) {
        return ResponseEntity.status(503).body(Map.of("error", "问答暂不可用", "state", failure.state));
    }

    /**
     * Python /query 不可达或响应异常：如实 502，归因给上游而不是吞掉。
     *
     * 对外只给一句话（不泄漏内部细节），但必须记日志——否则线上只能看到
     * "暂时不可用"，连"是超时还是响应畸形"都分不出来。排查时第一手材料
     * 就在这里（与分层健康检查同一条判据：报告要能支撑归因）。
     */
    @ExceptionHandler({RestClientException.class, IllegalArgumentException.class})
    public ResponseEntity<Map<String, String>> upstream(RuntimeException failure) {
        LOGGER.warn("question upstream failed: {}", failure.toString(), failure);
        return ResponseEntity.status(502).body(Map.of("error", "问答服务暂时不可用"));
    }

    static final class BadRequest extends RuntimeException {
        BadRequest(String message) {
            super(message);
        }
    }

    static final class Unavailable extends RuntimeException {
        private final String state;

        Unavailable(String state) {
            super("gate is " + state);
            this.state = state;
        }
    }
}
