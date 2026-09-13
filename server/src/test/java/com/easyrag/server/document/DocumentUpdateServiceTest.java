package com.easyrag.server.document;

import com.easyrag.server.rag.RagOperationGate;
import com.easyrag.server.rag.RagOperationGate.Operation;
import com.easyrag.server.rag.RagOperationGate.State;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.mockito.InOrder;

import java.nio.charset.StandardCharsets;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.BDDMockito.given;
import static org.mockito.BDDMockito.then;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;

/**
 * 更新与重索引的编排测试：真闸门 + mock 仓库与触发器。
 *
 * 守住的行为：哈希不变只 touch 不重索引（§一.2 的硬要求——正文不能悄悄
 * 替换，否则已落库 chunk 的字节偏移错位）、哈希变化写新正文并排队、
 * submit 必须在租约释放之后（否则 index() 撞 BUSY 停在 PENDING）、
 * reindex 的三态（QUEUED / IN_FLIGHT / MISSING）分别映射。
 */
class DocumentUpdateServiceTest {

    private static final String CONTENT = "# 标题\n正文内容";
    private static final DocumentUpdateRepository.CurrentDocument INDEXED_DOCUMENT =
            new DocumentUpdateRepository.CurrentDocument(42L, "原标题", "oldhash", "INDEXED");

    private RagOperationGate gate;
    private DocumentUpdateRepository documents;
    private IndexingTrigger indexingTrigger;
    private DocumentUpdateService service;

    @BeforeEach
    void setUp() {
        gate = new RagOperationGate();
        documents = mock(DocumentUpdateRepository.class);
        indexingTrigger = mock(IndexingTrigger.class);
        service = new DocumentUpdateService(gate, documents, indexingTrigger);
        gate.tryAcquire(Operation.RECOVERY).lease().orElseThrow().confirmCompletion();
    }

    @Test
    @DisplayName("哈希变化：写新正文、回到 PENDING、触发重索引")
    void changedContentQueuesReindex() {
        given(documents.findCurrent(42L)).willReturn(Optional.of(INDEXED_DOCUMENT));
        given(documents.updateContent(anyLong(), any())).willReturn(true);

        var result = service.updateFromText(42L, CONTENT);

        assertThat(result.indexStatus()).isEqualTo("PENDING");
        assertThat(result.reindexed()).isTrue();
        ArgumentCaptor<DocumentContent> captor = ArgumentCaptor.forClass(DocumentContent.class);
        then(documents).should().updateContent(eq(42L), captor.capture());
        assertThat(captor.getValue().content()).isEqualTo(CONTENT);
        assertThat(captor.getValue().title()).isEqualTo("标题");
        then(documents).should(never()).touch(anyLong());
        then(indexingTrigger).should().submit(42L);
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @Test
    @DisplayName("哈希不变：只 touch，不动正文、不重索引，保留原状态")
    void unchangedContentOnlyTouches() {
        String sameHash = hashOf(CONTENT);
        given(documents.findCurrent(42L)).willReturn(Optional.of(
                new DocumentUpdateRepository.CurrentDocument(42L, "原标题", sameHash, "INDEXED")));
        given(documents.touch(42L)).willReturn(true);

        var result = service.updateFromText(42L, CONTENT);

        assertThat(result.indexStatus()).isEqualTo("INDEXED");
        assertThat(result.reindexed()).isFalse();
        then(documents).should().touch(42L);
        then(documents).should(never()).updateContent(anyLong(), any());
        then(indexingTrigger).shouldHaveNoInteractions();
    }

    @Test
    @DisplayName("换行差异不算变更：CRLF 与 LF 同哈希，跳过重索引")
    void lineEndingDifferenceIsNotAChange() {
        given(documents.findCurrent(42L)).willReturn(Optional.of(
                new DocumentUpdateRepository.CurrentDocument(42L, "原标题", hashOf(CONTENT), "INDEXED")));
        given(documents.touch(42L)).willReturn(true);

        var result = service.updateFromText(42L, CONTENT.replace("\n", "\r\n"));

        assertThat(result.reindexed()).isFalse();
        then(indexingTrigger).shouldHaveNoInteractions();
    }

    @Test
    @DisplayName("submit 必须在租约释放之后，否则 index() 撞 BUSY")
    void submitsOnlyAfterReleasingTheLease() {
        given(documents.findCurrent(42L)).willReturn(Optional.of(INDEXED_DOCUMENT));
        given(documents.updateContent(anyLong(), any())).willAnswer(invocation -> {
            // 落库时仍持租：闸门处于 MUTATING
            assertThat(gate.state()).isEqualTo(State.MUTATING);
            return true;
        });

        service.updateFromText(42L, CONTENT);

        InOrder order = inOrder(documents, indexingTrigger);
        order.verify(documents).updateContent(eq(42L), any());
        order.verify(indexingTrigger).submit(42L);
        // submit 时闸门已回到 READY，index() 能取到自己的 MUTATION 租约
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @Test
    @DisplayName("文档不存在：404，不落库不触发，闸门回到 READY")
    void missingDocumentIsNotFound() {
        given(documents.findCurrent(999L)).willReturn(Optional.empty());

        assertThatThrownBy(() -> service.updateFromText(999L, CONTENT))
                .isInstanceOf(DocumentUpdateService.NotFound.class);

        then(indexingTrigger).shouldHaveNoInteractions();
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @Test
    @DisplayName("校验失败（空正文）：400 语义，闸门不退回需恢复")
    void rejectedContentDoesNotPoisonTheGate() {
        given(documents.findCurrent(42L)).willReturn(Optional.of(INDEXED_DOCUMENT));

        assertThatThrownBy(() -> service.updateFromText(42L, "   "))
                .isInstanceOf(DocumentIntakeService.Rejected.class);

        // 校验失败没碰索引，闸门必须干净地回到 READY（退成 RECOVERY_REQUIRED 是误报）
        assertThat(gate.state()).isEqualTo(State.READY);
        then(documents).should(never()).updateContent(anyLong(), any());
        then(documents).should(never()).touch(anyLong());
    }

    @Test
    @DisplayName("上传形态：不支持的扩展名被拒，闸门保持 READY")
    void uploadRejectsUnsupportedExtension() {
        given(documents.findCurrent(42L)).willReturn(Optional.of(INDEXED_DOCUMENT));

        assertThatThrownBy(() -> service.updateFromUpload(42L, "笔记.pdf",
                CONTENT.getBytes(StandardCharsets.UTF_8)))
                .isInstanceOf(DocumentIntakeService.Rejected.class)
                .hasMessageContaining(".pdf");

        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @Test
    @DisplayName("JSON 形态无文件名：标题降级到文档现有标题，不变成「未命名文档」")
    void jsonUpdateFallsBackToExistingTitle() {
        given(documents.findCurrent(42L)).willReturn(Optional.of(INDEXED_DOCUMENT));
        given(documents.updateContent(anyLong(), any())).willReturn(true);

        service.updateFromText(42L, "没有标题行的正文");

        ArgumentCaptor<DocumentContent> captor = ArgumentCaptor.forClass(DocumentContent.class);
        then(documents).should().updateContent(eq(42L), captor.capture());
        assertThat(captor.getValue().title()).isEqualTo("原标题");
    }

    @Test
    @DisplayName("闸门被占用：503，不碰存储")
    void rejectsWhenGateBusy() {
        gate.tryAcquire(Operation.MUTATION);

        assertThatThrownBy(() -> service.updateFromText(42L, CONTENT))
                .isInstanceOf(DocumentUpdateService.Unavailable.class)
                .extracting("state").isEqualTo(State.MUTATING.name());

        then(documents).shouldHaveNoInteractions();
        then(indexingTrigger).shouldHaveNoInteractions();
    }

    @Test
    @DisplayName("reindex：FAILED / INDEXED 放行，回到 PENDING 并排队")
    void reindexQueuesEligibleDocument() {
        given(documents.markForReindex(42L)).willReturn(DocumentUpdateRepository.MarkResult.QUEUED);

        var result = service.reindex(42L);

        assertThat(result.indexStatus()).isEqualTo("PENDING");
        assertThat(result.reindexed()).isTrue();
        then(indexingTrigger).should().submit(42L);
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @Test
    @DisplayName("reindex：已在队列或索引在途返回 409，不重复排队")
    void reindexRejectsInFlightDocument() {
        given(documents.markForReindex(42L)).willReturn(DocumentUpdateRepository.MarkResult.IN_FLIGHT);

        assertThatThrownBy(() -> service.reindex(42L))
                .isInstanceOf(DocumentUpdateService.InFlight.class);

        then(indexingTrigger).shouldHaveNoInteractions();
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @Test
    @DisplayName("reindex：文档不存在返回 404")
    void reindexRejectsMissingDocument() {
        given(documents.markForReindex(999L)).willReturn(DocumentUpdateRepository.MarkResult.MISSING);

        assertThatThrownBy(() -> service.reindex(999L))
                .isInstanceOf(DocumentUpdateService.NotFound.class);

        then(indexingTrigger).shouldHaveNoInteractions();
        assertThat(gate.state()).isEqualTo(State.READY);
    }

    @Test
    @DisplayName("非法 id：直接拒绝，不申请闸门")
    void rejectsNonPositiveId() {
        assertThatThrownBy(() -> service.updateFromText(0L, CONTENT))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> service.reindex(-1L))
                .isInstanceOf(IllegalArgumentException.class);

        assertThat(gate.state()).isEqualTo(State.READY);
        then(documents).shouldHaveNoInteractions();
    }

    /** 与 DocumentContent 同一口径：规范化后 SHA-256。 */
    private static String hashOf(String content) {
        return DocumentContent.fromText(content, "任意").contentHash();
    }
}
