package com.easyrag.server.document;

import com.easyrag.server.rag.RagOperationGate;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.BDDMockito.given;
import static org.mockito.BDDMockito.then;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * /api/admin/ready 的 HTTP 契约：200 携带 state 与 recovered；
 * 有未结束活动时 409 携带当前闸门状态，不夺取。
 */
@WebMvcTest(ReadinessController.class)
class ReadinessControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private ReadinessService readiness;

    @Test
    @DisplayName("恢复成功：200 { state: READY, recovered: n }")
    void returnsReadyStateWithRecoveredCount() throws Exception {
        given(readiness.ready()).willReturn(new ReadinessService.ReadinessResult("READY", 3));

        mockMvc.perform(post("/api/admin/ready"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.state").value("READY"))
                .andExpect(jsonPath("$.recovered").value(3));
    }

    @Test
    @DisplayName("有未结束活动：409 { state: MUTATING }")
    void mapsBusyToConflictWithGateState() throws Exception {
        given(readiness.ready()).willThrow(new ReadinessService.Busy(RagOperationGate.State.MUTATING));

        mockMvc.perform(post("/api/admin/ready"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.state").value("MUTATING"));

        // 409 语义是"现在不行，稍后再试"，不是失败——ready() 恰好被调用一次
        then(readiness).should().ready();
    }
}
