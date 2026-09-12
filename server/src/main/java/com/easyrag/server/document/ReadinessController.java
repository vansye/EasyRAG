package com.easyrag.server.document;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * POST /api/admin/ready —— 闸门就绪与 PENDING 重推（A1-1）。
 *
 * 手动端点而非启动自动恢复：仅重启 Java 不会终止旧 Python 请求，自动就绪
 * 等于未确认旧执行者退出就放行（保持子 Issue A 的 A-2 裁决无例外）。
 * 显式调用让「谁确认过」可追溯，代价只是 demo 多一步。
 */
@RestController
public class ReadinessController {

    private final ReadinessService readiness;

    public ReadinessController(ReadinessService readiness) {
        this.readiness = readiness;
    }

    @PostMapping("/api/admin/ready")
    public ReadinessService.ReadinessResult ready() {
        return readiness.ready();
    }

    @ExceptionHandler(ReadinessService.Busy.class)
    public ResponseEntity<Map<String, String>> busy(ReadinessService.Busy failure) {
        return ResponseEntity.status(409).body(Map.of("state", failure.state().name()));
    }
}
