package com.easyrag.server.monitor;

import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 进程级健康检查。M1 验收项：三进程可起、健康检查通。
 *
 * <p>与 Actuator 的 /actuator/health 无关——那是给托管平台看的，这里是给
 * 编排与前端展示"数据权威层是否活着"。返回结构刻意做成"进程状态 + 数据库状态"
 * 两层：进程起得来但库连不上时，必须还能如实告诉调用方"我活着，但 DB 不可用"，
 * 而不是整包 503。进程起得来是 M1 的一个独立事实。</p>
 *
 * <p>设计取舍：不用 JPA 实体，避免过早固化领域模型；健康检查只探活链路，
 * 不做业务 SQL。</p>
 */
@RestController
public class HealthController {

    private static final String SERVICE_NAME = "easyrag-server";
    private static final String DATABASE = "mysql";

    private final JdbcTemplate jdbcTemplate;

    public HealthController(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    @GetMapping("/health")
    public ResponseEntity<Map<String, Object>> health() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "UP");
        body.put("service", SERVICE_NAME);

        Map<String, Object> db = new LinkedHashMap<>();
        db.put("database", DATABASE);
        try {
            // Hikari 配置为惰性连接（initialization-fail-timeout: -1），
            // 库不可用时应用仍能起、此方法如实返回 DOWN。
            jdbcTemplate.queryForObject("SELECT 1", Integer.class);
            db.put("status", "UP");
        } catch (Exception e) {
            db.put("status", "DOWN");
            db.put("error", e.getClass().getSimpleName());
        }
        body.put("db", db);

        return ResponseEntity.ok(body);
    }
}
