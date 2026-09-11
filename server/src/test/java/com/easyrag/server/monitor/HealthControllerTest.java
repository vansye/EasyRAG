package com.easyrag.server.monitor;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.BDDMockito.given;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * /health 的行为测试。不依赖真实数据库——数据库是被 mock 的，
 * 因此本测试在任何环境（含 CI）都能跑。
 *
 * <p>要守住的核心行为：<b>数据库不可用时，进程仍然报 UP</b>。
 * 这不是形式主义——模块 A 的索引状态机要靠 /health 区分
 * "Python 挂了" 与 "我自己挂了"，如果 DB 一断整包 503，
 * 调用方就无法分辨这两种失败，FAILED 的归因会错。</p>
 */
@WebMvcTest(HealthController.class)
class HealthControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private JdbcTemplate jdbcTemplate;

    @Test
    @DisplayName("数据库可用时：进程 UP，db UP")
    void reportsUpWhenDatabaseReachable() throws Exception {
        given(jdbcTemplate.queryForObject(anyString(), any(Class.class))).willReturn(1);

        mockMvc.perform(get("/health"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("UP"))
                .andExpect(jsonPath("$.service").value("easyrag-server"))
                .andExpect(jsonPath("$.db.database").value("mysql"))
                .andExpect(jsonPath("$.db.status").value("UP"));
    }

    @Test
    @DisplayName("数据库不可用时：仍返回 200 且进程 UP，只有 db 转 DOWN 并带错误类型")
    void staysUpButReportsDatabaseDownWhenUnreachable() throws Exception {
        given(jdbcTemplate.queryForObject(anyString(), any(Class.class)))
                .willThrow(new DataAccessResourceFailureException("connection refused"));

        mockMvc.perform(get("/health"))
                // 关键断言：不是 503。进程活着是一个独立于数据库的事实。
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("UP"))
                .andExpect(jsonPath("$.db.status").value("DOWN"))
                // 只断言"有错误类型"而不写死类名：真实环境抛的是
                // CannotGetJdbcConnectionException，写死 mock 抛的那个类名
                // 会变成自证循环——测试永远通过，却与生产行为无关。
                .andExpect(jsonPath("$.db.error").isNotEmpty());
    }
}
