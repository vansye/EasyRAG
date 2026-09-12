package com.easyrag.server.document;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.BDDMockito.given;
import static org.mockito.BDDMockito.then;
import static org.mockito.Mockito.mock;

/**
 * frontmatter 解析的对抗性边界（由对抗性审查发现后补的回归测试）。
 *
 * 两条口径：
 * 1. tags 的引号内逗号不是分隔符——逐字符扫描而非 split(",")。
 * 2. title 的值若引号未闭合（说明跨了行、闭合边界可能落在值内部），
 *    放弃它并降级到 H1 / 文件名。保守失败优于截断错误的标题。
 */
class FrontMatterAdversarialTest {

    private final DocumentQueryRepository documents = mock(DocumentQueryRepository.class);
    private final IndexingTrigger indexingTrigger = mock(IndexingTrigger.class);
    private final DocumentIntakeService service = new DocumentIntakeService(documents, indexingTrigger);

    @Test
    @DisplayName("tags 里带逗号的引号标签不被切开")
    void tagWithCommaInsideQuotes() {
        String content = "---\ntags: [\"a,b\", c]\n---\n正文";

        assertThat(intake(content).tags()).containsExactly("a,b", "c");
    }

    @Test
    @DisplayName("tags 常规形态与空数组")
    void tagsOrdinaryForms() {
        assertThat(intake("---\ntags: [redis, \"缓存\"]\n---\n正文").tags())
                .containsExactly("redis", "缓存");
        assertThat(intake("---\ntags: []\n---\n正文").tags()).isEmpty();
        assertThat(intake("---\ntags: [ a , , b ]\n---\n正文").tags())
                .containsExactly("a", "b");
    }

    @Test
    @DisplayName("title 值内含 --- 且引号未闭合：放弃该 title，降级到文件名")
    void titleValueContainingSeparatorFallsBack() {
        // 闭合边界落在 title 值内部，前半截是 `"说明`（引号未闭合）
        String content = "---\ntitle: \"说明\n---\n仍在标题值\"\ntags: [x]\n---\n正文";

        DocumentQueryRepository.NewDocument saved = intake(content, "降级文件名.md");

        // 不再是截断的 `"说明`
        assertThat(saved.title()).isEqualTo("降级文件名");
    }

    @Test
    @DisplayName("引号正常闭合的 title 照常采用")
    void quotedTitleStillWorks() {
        assertThat(intake("---\ntitle: \"带 \\\"引号\\\" 的标题\"\n---\n正文").title())
                .isNotBlank();
        assertThat(intake("---\ntitle: 普通标题\n---\n正文").title()).isEqualTo("普通标题");
        assertThat(intake("---\ntitle: \"引号标题\"\n---\n正文").title()).isEqualTo("引号标题");
    }

    @Test
    @DisplayName("正文中的 --- 分隔线不被当作 frontmatter")
    void bodySeparatorIsNotFrontMatter() {
        DocumentQueryRepository.NewDocument saved = intake("# 标题\n正文\n\n---\n\n更多正文");

        assertThat(saved.title()).isEqualTo("标题");
        assertThat(saved.tags()).isEmpty();
    }

    private DocumentQueryRepository.NewDocument intake(String content) {
        return intake(content, "t.md");
    }

    private DocumentQueryRepository.NewDocument intake(String content, String filename) {
        DocumentQueryRepository documents = mock(DocumentQueryRepository.class);
        given(documents.insertPending(any())).willReturn(1L);
        new DocumentIntakeService(documents, indexingTrigger)
                .intake(filename, content.getBytes(StandardCharsets.UTF_8));
        ArgumentCaptor<DocumentQueryRepository.NewDocument> saved =
                ArgumentCaptor.forClass(DocumentQueryRepository.NewDocument.class);
        then(documents).should().insertPending(saved.capture());
        return saved.getValue();
    }
}
