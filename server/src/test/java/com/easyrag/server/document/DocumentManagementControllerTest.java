package com.easyrag.server.document;

import com.easyrag.server.rag.RagOperationGate;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest({DocumentManagementController.class, DocumentController.class})
class DocumentManagementControllerTest {

    @Autowired
    private MockMvc mvc;

    @MockitoBean
    private DocumentManagementService service;

    @MockitoBean
    private DocumentIntakeService intake;

    @MockitoBean
    private DocumentQueryRepository documents;

    @Test
    void detailsExposeOriginalContentAndTheDocumentStateWithSnakeCaseFields() throws Exception {
        var created = LocalDateTime.of(2026, 9, 1, 12, 0);
        var updated = LocalDateTime.of(2026, 9, 14, 10, 0);
        when(documents.findDetail(42L)).thenReturn(Optional.of(new DocumentQueryRepository.DocumentDetail(
                42, "笔记", "# 笔记\r\n正文😀", List.of("知识"), "UPLOAD", "笔记.md", "FAILED",
                "stage=EMBED; cleanup=OK", 1, created, updated)));

        mvc.perform(get("/api/documents/42"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.id").value(42))
                .andExpect(jsonPath("$.content").value("# 笔记\r\n正文😀"))
                .andExpect(jsonPath("$.tags[0]").value("知识"))
                .andExpect(jsonPath("$.source_type").value("UPLOAD"))
                .andExpect(jsonPath("$.source_uri").value("笔记.md"))
                .andExpect(jsonPath("$.index_status").value("FAILED"))
                .andExpect(jsonPath("$.index_error").value("stage=EMBED; cleanup=OK"))
                .andExpect(jsonPath("$.chunk_count").value(1))
                .andExpect(jsonPath("$.created_at").value("2026-09-01T12:00:00"))
                .andExpect(jsonPath("$.updated_at").value("2026-09-14T10:00:00"))
                .andExpect(jsonPath("$.content_hash").doesNotExist());
    }

    @Test
    void chunksExposeUtf8ByteOffsetsAndTokenCount() throws Exception {
        when(documents.existsActive(42L)).thenReturn(true);
        when(documents.findChunks(42L)).thenReturn(List.of(
                new DocumentQueryRepository.ChunkRow(101L, 0, "正文😀", 10, 20, "笔记", 4)));

        mvc.perform(get("/api/documents/42/chunks"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].id").value(101))
                .andExpect(jsonPath("$.items[0].seq").value(0))
                .andExpect(jsonPath("$.items[0].text").value("正文😀"))
                .andExpect(jsonPath("$.items[0].byte_start").value(10))
                .andExpect(jsonPath("$.items[0].byte_end").value(20))
                .andExpect(jsonPath("$.items[0].heading_path").value("笔记"))
                .andExpect(jsonPath("$.items[0].token_count").value(4))
                .andExpect(jsonPath("$.items[0].char_start").doesNotExist());
    }

    @Test
    void updatesReturnWhetherTheContentTriggeredReindexing() throws Exception {
        when(service.update(42L, "新正文😀"))
                .thenReturn(new DocumentManagementService.ChangeResult(42L, "PENDING", true));

        mvc.perform(put("/api/documents/42").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"content\":\"新正文😀\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.id").value(42))
                .andExpect(jsonPath("$.index_status").value("PENDING"))
                .andExpect(jsonPath("$.reindexed").value(true));
        verify(service).update(42L, "新正文😀");
    }

    @Test
    void unchangedContentReturnsItsCurrentStatusAndFalseReindexed() throws Exception {
        when(service.update(42L, "原文"))
                .thenReturn(new DocumentManagementService.ChangeResult(42L, "INDEXED", false));

        mvc.perform(put("/api/documents/42").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"content\":\"原文\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.index_status").value("INDEXED"))
                .andExpect(jsonPath("$.reindexed").value(false));
    }

    @Test
    void manualReindexIsAcceptedAndDeleteHasNoResponseBody() throws Exception {
        when(service.reindex(42L)).thenReturn(new DocumentManagementService.ChangeResult(42L, "PENDING", true));

        mvc.perform(post("/api/documents/42/reindex"))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.id").value(42))
                .andExpect(jsonPath("$.index_status").value("PENDING"))
                .andExpect(jsonPath("$.reindexed").value(true));
        mvc.perform(delete("/api/documents/42"))
                .andExpect(status().isNoContent()).andExpect(content().string(""));
        verify(service).delete(42L);
    }

    @Test
    void notFoundMapsTo404ForEveryDocumentOperation() throws Exception {
        when(documents.findDetail(42L)).thenReturn(Optional.empty());
        when(service.update(42L, "新正文")).thenThrow(new DocumentManagementService.NotFound());
        when(service.reindex(42L)).thenThrow(new DocumentManagementService.NotFound());
        doThrow(new DocumentManagementService.NotFound()).when(service).delete(42L);

        mvc.perform(get("/api/documents/42")).andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").isNotEmpty());
        mvc.perform(get("/api/documents/42/chunks")).andExpect(status().isNotFound());
        mvc.perform(put("/api/documents/42").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"content\":\"新正文\"}"))
                .andExpect(status().isNotFound());
        mvc.perform(delete("/api/documents/42")).andExpect(status().isNotFound());
        mvc.perform(post("/api/documents/42/reindex")).andExpect(status().isNotFound());
    }

    @ParameterizedTest
    @ValueSource(strings = {"{}", "{\"content\":null}", "{\"content\":123}",
            "{\"content\":true}", "{\"content\":[]}", "{\"content\":{}}"})
    void invalidContentTypesAreRejectedWithoutCoercionOrServiceCalls(String body) throws Exception {
        mvc.perform(put("/api/documents/42").contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isBadRequest()).andExpect(jsonPath("$.error").isNotEmpty());
        verifyNoInteractions(service);
    }

    @Test
    void emptyOrOversizedContentUsesTheServiceRejectionMessage() throws Exception {
        when(service.update(42L, " ")).thenThrow(new DocumentIntakeService.Rejected("正文不能为空"));

        mvc.perform(put("/api/documents/42").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"content\":\" \"}"))
                .andExpect(status().isBadRequest()).andExpect(jsonPath("$.error").value("正文不能为空"));
    }

    @ParameterizedTest
    @EnumSource(value = RagOperationGate.State.class,
            names = {"QUERYING", "MUTATING", "RECOVERING", "RECOVERY_REQUIRED"})
    void busyMutationsReturnConflictAndTheActualGateState(RagOperationGate.State state) throws Exception {
        when(service.reindex(42L)).thenThrow(new DocumentManagementService.Busy(state));

        mvc.perform(post("/api/documents/42/reindex"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.state").value(state.name()))
                .andExpect(jsonPath("$.error").isNotEmpty());
    }

    @Test
    void uncertainMutationReturns503WithoutLeakingTheInternalCause() throws Exception {
        doThrow(new DocumentManagementService.MutationFailed("资料变更未能确认完成",
                new IllegalStateException("credential-secret"))).when(service).delete(42L);

        mvc.perform(delete("/api/documents/42"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.state").value("RECOVERY_REQUIRED"))
                .andExpect(jsonPath("$.error").value("资料变更未能确认完成"));
    }
}
