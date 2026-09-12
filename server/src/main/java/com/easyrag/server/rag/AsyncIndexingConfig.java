package com.easyrag.server.rag;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;

/**
 * 索引推进的异步配置。
 *
 * 单线程是裁决而非节省：RagOperationGate 允许多个 MUTATION 排队申请，但
 * 索引变更在 M2 全局串行（子 Issue A §一.3），多线程执行器只会把串行
 * 变成「多个请求在闸门前排队、日志交错、失败归因变难」，没有任何收益。
 * 队列无界是可接受的：每个任务只是一次 index(documentId) 调用，真正的
 * 背压在闸门（MUTATION 互斥）而不在线程池。
 */
@Configuration
@EnableAsync
public class AsyncIndexingConfig {

    @Bean("indexingExecutor")
    public ThreadPoolTaskExecutor indexingExecutor() {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(1);
        executor.setMaxPoolSize(1);
        executor.setThreadNamePrefix("indexing-");
        // 不拒绝、不丢弃：收录方已把 PENDING 落库，任务丢失意味着文档
        // 永远停在 PENDING 且无人知晓（要等手动恢复才发现）
        executor.setRejectedExecutionHandler(new java.util.concurrent.ThreadPoolExecutor.DiscardPolicy() {
            @Override
            public void rejectedExecution(Runnable task, java.util.concurrent.ThreadPoolExecutor pool) {
                // 队列无界时理论上到不了这里；到了说明关闭中，记日志留痕
                org.slf4j.LoggerFactory.getLogger("indexing-executor")
                        .warn("indexing task rejected during executor shutdown");
            }
        });
        executor.initialize();
        return executor;
    }
}
