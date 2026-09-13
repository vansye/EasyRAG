package com.easyrag.server.document;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.BDDMockito.given;
import static org.mockito.BDDMockito.then;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;

/**
 * 收录解析与校验的行为测试，不依赖数据库。
 *
 * 守住的口径：标题三级降级、frontmatter 可选增强（解析失败不拒绝收录）、
 * 哈希 = 规范化后的正文（换行与首尾空白不产生假变更）、BOM 在入口剥除。
 */
class DocumentIntakeServiceTest {

    private final DocumentQueryRepository documents = mock(DocumentQueryRepository.class);
    private final IndexingTrigger indexingTrigger = mock(IndexingTrigger.class);
    private final DocumentIntakeService service = new DocumentIntakeService(documents, indexingTrigger);

    @Test
    @DisplayName("无 frontmatter：title 取正文首个一级标题，tags 为空，落库 PENDING 并触发索引")
    void ingestsWithoutFrontMatterTakingFirstHeading() {
        given(documents.insertPending(any())).willReturn(42L);
        String content = "# KV Cache\n\n推理缓存的正文。";

        DocumentIntakeService.DocumentCreated created =
                service.intake("笔记.md", content.getBytes(StandardCharsets.UTF_8));

        assertThat(created).isEqualTo(
                new DocumentIntakeService.DocumentCreated(42L, "KV Cache", "UPLOAD", "PENDING"));
        DocumentQueryRepository.NewDocument saved = capturedInsert();
        assertThat(saved.title()).isEqualTo("KV Cache");
        assertThat(saved.tags()).isEmpty();
        assertThat(saved.content()).isEqualTo(content);
        assertThat(saved.sourceType()).isEqualTo("UPLOAD");
        assertThat(saved.sourceUri()).isEqualTo("笔记.md");
        assertThat(saved.contentHash()).hasSize(64);
        then(indexingTrigger).should().submit(42L);
    }

    @Test
    @DisplayName("无 H1 时 title 降级到文件名去扩展名；带路径的文件名先剥掉目录")
    void fallsBackToSanitizedFilenameWhenNoHeading() {
        given(documents.insertPending(any())).willReturn(1L);

        service.intake("C:\\Users\\vansye\\Redis过期Key处理.md",
                "没有标题的正文".getBytes(StandardCharsets.UTF_8));

        DocumentQueryRepository.NewDocument saved = capturedInsert();
        assertThat(saved.title()).isEqualTo("Redis过期Key处理");
        assertThat(saved.sourceUri()).isEqualTo("Redis过期Key处理.md");
    }

    @Test
    @DisplayName("有 frontmatter：title 与内联数组 tags 被提取，忽略正文标题")
    void extractsFrontMatterTitleAndTags() {
        given(documents.insertPending(any())).willReturn(1L);
        String content = "---\ntitle: 自定义标题\ntags: [redis, \"缓存\"]\n---\n\n# 不是这个标题\n正文";

        service.intake("任意.md", content.getBytes(StandardCharsets.UTF_8));

        DocumentQueryRepository.NewDocument saved = capturedInsert();
        assertThat(saved.title()).isEqualTo("自定义标题");
        assertThat(saved.tags()).containsExactly("redis", "缓存");
        // 存库的 content 保留 frontmatter 原文——它属于笔记本身
        assertThat(saved.content()).isEqualTo(content);
    }

    @Test
    @DisplayName("畸形 frontmatter（无闭合）降级为普通正文，不拒绝收录")
    void degradesGracefullyOnMalformedFrontMatter() {
        given(documents.insertPending(any())).willReturn(1L);

        service.intake("降级.md", "---\ntitle: 不会被采用\n正文没有闭合".getBytes(StandardCharsets.UTF_8));

        DocumentQueryRepository.NewDocument saved = capturedInsert();
        assertThat(saved.title()).isEqualTo("降级");
        assertThat(saved.tags()).isEmpty();
    }

    @Test
    @DisplayName("CRLF 与 LF 是同一内容：存储原文不同，content_hash 相同")
    void normalizesLineEndingsForHashButStoresOriginal() {
        given(documents.insertPending(any())).willReturn(1L, 2L);

        service.intake("a.md", "# 标题\r\n\r\n正文一\r\n".getBytes(StandardCharsets.UTF_8));
        service.intake("b.md", "# 标题\n\n正文一\n".getBytes(StandardCharsets.UTF_8));

        ArgumentCaptor<DocumentQueryRepository.NewDocument> all =
                ArgumentCaptor.forClass(DocumentQueryRepository.NewDocument.class);
        then(documents).should(times(2)).insertPending(all.capture());
        DocumentQueryRepository.NewDocument crlf = all.getAllValues().get(0);
        DocumentQueryRepository.NewDocument lf = all.getAllValues().get(1);
        assertThat(crlf.content()).isNotEqualTo(lf.content());
        assertThat(crlf.contentHash()).isEqualTo(lf.contentHash());
    }

    @Test
    @DisplayName("超过 1 MB：拒绝且提示能看出实际大小与上限的差异，不入库不触发")
    void rejectsOversizeWithActualSizeInMessage() {
        byte[] oversize = new byte[DocumentContent.MAX_CONTENT_BYTES + 1];

        assertThatThrownBy(() -> service.intake("大文件.md", oversize))
                .isInstanceOf(DocumentIntakeService.Rejected.class)
                // 只写 "1.0 MB" 时两边四舍五入后一样大，用户看不出差在哪，
                // 所以提示必须带上精确字节数（端到端实测发现）
                .hasMessageContaining("1,048,577")
                .hasMessageContaining("1,048,576")
                .hasMessageContaining("上限")
                .hasMessageContaining("拆分");
        then(documents).should(never()).insertPending(any());
        then(indexingTrigger).shouldHaveNoInteractions();
    }

    @Test
    @DisplayName("恰好 1 MB 的边界放行")
    void acceptsExactlyOneMegabyte() {
        given(documents.insertPending(any())).willReturn(1L);
        byte[] boundary = new byte[DocumentContent.MAX_CONTENT_BYTES];
        java.util.Arrays.fill(boundary, (byte) 'a');

        assertThat(service.intake("边界.md", boundary).id()).isEqualTo(1L);
    }

    @Test
    @DisplayName("非 UTF-8 字节：拒绝，不入库")
    void rejectsInvalidUtf8() {
        assertThatThrownBy(() -> service.intake("坏编码.md", new byte[]{'a', (byte) 0xFF, 'b'}))
                .isInstanceOf(DocumentIntakeService.Rejected.class)
                .hasMessageContaining("UTF-8");
        then(documents).should(never()).insertPending(any());
    }

    @Test
    @DisplayName("不支持的扩展名与无扩展名：拒绝，不入库")
    void rejectsUnsupportedExtension() {
        assertThatThrownBy(() -> service.intake("论文.pdf", "# x".getBytes(StandardCharsets.UTF_8)))
                .isInstanceOf(DocumentIntakeService.Rejected.class)
                .hasMessageContaining(".pdf")
                .hasMessageContaining(".md");
        assertThatThrownBy(() -> service.intake("无扩展名", "# x".getBytes(StandardCharsets.UTF_8)))
                .isInstanceOf(DocumentIntakeService.Rejected.class)
                .hasMessageContaining("仅支持");
        then(documents).shouldHaveNoInteractions();
    }

    @Test
    @DisplayName("空白内容：拒绝")
    void rejectsBlankContent() {
        assertThatThrownBy(() -> service.intake("空.md", "  \n\t ".getBytes(StandardCharsets.UTF_8)))
                .isInstanceOf(DocumentIntakeService.Rejected.class)
                .hasMessageContaining("空");
        then(documents).should(never()).insertPending(any());
    }

    @Test
    @DisplayName("UTF-8 BOM 在入口剥除：title 从 H1 正确提取，存库内容无 BOM")
    void stripsBomAtEntry() {
        given(documents.insertPending(any())).willReturn(1L);
        String rest = "# 标题\n正文";
        byte[] restBytes = rest.getBytes(StandardCharsets.UTF_8);
        byte[] withBom = new byte[3 + restBytes.length];
        withBom[0] = (byte) 0xEF;
        withBom[1] = (byte) 0xBB;
        withBom[2] = (byte) 0xBF;
        System.arraycopy(restBytes, 0, withBom, 3, restBytes.length);

        service.intake("bom.md", withBom);

        DocumentQueryRepository.NewDocument saved = capturedInsert();
        assertThat(saved.title()).isEqualTo("标题");
        assertThat(saved.content()).isEqualTo(rest);
    }

    @Test
    @DisplayName("超长 title 截断到列宽 512 码点，不因溢出落库失败")
    void truncatesOverlongTitle() {
        given(documents.insertPending(any())).willReturn(1L);
        String longTitle = "标".repeat(600);

        service.intake("长标题.md", ("# " + longTitle + "\n正文").getBytes(StandardCharsets.UTF_8));

        assertThat(capturedInsert().title()).hasSize(512);
    }

    private DocumentQueryRepository.NewDocument capturedInsert() {
        ArgumentCaptor<DocumentQueryRepository.NewDocument> saved =
                ArgumentCaptor.forClass(DocumentQueryRepository.NewDocument.class);
        then(documents).should().insertPending(saved.capture());
        return saved.getValue();
    }
}
