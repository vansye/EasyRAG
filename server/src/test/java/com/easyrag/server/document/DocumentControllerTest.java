package com.easyrag.server.document;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.nio.charset.StandardCharsets;
import java.time.LocalDateTime;
import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.BDDMockito.given;
import static org.mockito.BDDMockito.then;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * /api/documents 的 HTTP 契约测试，不依赖数据库与 Python——收录服务是被
 * mock 的，因此任何环境（含 CI）都能跑。
 *
 * 守住的行为：上传 201 立即返回 PENDING（不等索引）、收录被拒映射为
 * 400 且 error 面向用户、列表的分页与状态过滤参数校验。
 */
@WebMvcTest(DocumentController.class)
class DocumentControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private DocumentIntakeService intake;

    @MockitoBean
    private DocumentQueryRepository documents;

    @Test
    @DisplayName("上传成功：201，立即返回 PENDING，不等待索引")
    void returnsCreatedWithPendingStatus() throws Exception {
        given(intake.intake(eq("笔记.md"), any(byte[].class))).willReturn(
                new DocumentIntakeService.DocumentCreated(42L, "KV Cache", "UPLOAD", "PENDING"));

        mockMvc.perform(multipart("/api/documents").file(
                        new MockMultipartFile("file", "笔记.md", "text/markdown",
                                "# KV Cache\n正文".getBytes(StandardCharsets.UTF_8))))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.id").value(42))
                .andExpect(jsonPath("$.title").value("KV Cache"))
                .andExpect(jsonPath("$.source_type").value("UPLOAD"))
                .andExpect(jsonPath("$.index_status").value("PENDING"));
    }

    @Test
    @DisplayName("收录被拒：400，error 是面向用户的原因")
    void mapsRejectionToBadRequestWithUserMessage() throws Exception {
        given(intake.intake(any(), any(byte[].class))).willThrow(
                new DocumentIntakeService.Rejected("文件 2.5 MB 超过收录上限 1.0 MB，请拆分后再上传"));

        mockMvc.perform(multipart("/api/documents").file(
                        new MockMultipartFile("file", "大文件.md", "text/markdown", new byte[64])))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("文件 2.5 MB 超过收录上限 1.0 MB，请拆分后再上传"));
    }

    @Test
    @DisplayName("列表：200，含 total 与 items，chunk_count 实时口径")
    void listsDocumentsWithLiveChunkCount() throws Exception {
        given(documents.findPage(null, 0, 20)).willReturn(new DocumentQueryRepository.DocumentPage(
                1, List.of(new DocumentQueryRepository.DocumentSummary(
                        42L, "KV Cache", "UPLOAD", List.of("redis", "缓存"), "INDEXED", 12,
                        LocalDateTime.of(2026, 9, 12, 6, 0)))));

        mockMvc.perform(get("/api/documents"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.total").value(1))
                .andExpect(jsonPath("$.items[0].id").value(42))
                .andExpect(jsonPath("$.items[0].title").value("KV Cache"))
                .andExpect(jsonPath("$.items[0].source_type").value("UPLOAD"))
                .andExpect(jsonPath("$.items[0].tags[0]").value("redis"))
                .andExpect(jsonPath("$.items[0].index_status").value("INDEXED"))
                .andExpect(jsonPath("$.items[0].chunk_count").value(12))
                .andExpect(jsonPath("$.items[0].updated_at").value("2026-09-12T06:00:00"));
    }

    @Test
    @DisplayName("status 过滤：小写输入归一为大写后传给查询层")
    void normalizesStatusFilter() throws Exception {
        given(documents.findPage("PENDING", 0, 20)).willReturn(
                new DocumentQueryRepository.DocumentPage(0, List.of()));

        mockMvc.perform(get("/api/documents").param("status", "pending"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.total").value(0));

        then(documents).should().findPage("PENDING", 0, 20);
    }

    @Test
    @DisplayName("非法查询参数：未知 status、负 page、越界 size 都返回 400")
    void rejectsBadQueryParameters() throws Exception {
        mockMvc.perform(get("/api/documents").param("status", "BROKEN"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").isNotEmpty());
        mockMvc.perform(get("/api/documents").param("page", "-1"))
                .andExpect(status().isBadRequest());
        mockMvc.perform(get("/api/documents").param("size", "0"))
                .andExpect(status().isBadRequest());
        mockMvc.perform(get("/api/documents").param("size", "101"))
                .andExpect(status().isBadRequest());
    }
}
