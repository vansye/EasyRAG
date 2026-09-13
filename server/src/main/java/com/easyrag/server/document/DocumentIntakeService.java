package com.easyrag.server.document;

import com.fasterxml.jackson.annotation.JsonProperty;
import org.springframework.stereotype.Service;

/**
 * 收录入口：校验 → 解析 → 落库为 PENDING → 触发异步索引。
 *
 * 三条边界（A-1 文档）：
 * 1. 收录本身不过 RagOperationGate（§九）——闸门保护索引一致性，不保护入库；
 *    正文进 MySQL 无条件安全，闸门未就绪时文档停在 PENDING 而非 FAILED。
 * 2. intake 不等待索引：落库即返回。insertPending 的事务在其返回时已提交，
 *    之后才调 IndexingTrigger.submit——DocumentIndexingService.index() 拒绝在
 *    事务内运行，这里必须保证调用点在事务之外。
 * 3. 存进 MySQL 的 content 是唯一的原文真相（切片、字节偏移、溯源展示都以它
 *    为准）。BOM 在 DocumentContent 的解析入口一次性剥掉——它是编码痕迹而非
 *    内容，剥掉后全链路只面对一种形态，不需要每个下游各自处理。
 *
 * 校验与解析（扩展名、大小、UTF-8、frontmatter、标题降级、内容哈希）在
 * DocumentContent：A-2 的 PUT 更新复用同一条路径，避免哈希口径两处漂移。
 */
@Service
public class DocumentIntakeService {

    private final DocumentQueryRepository documents;
    private final IndexingTrigger indexingTrigger;

    public DocumentIntakeService(DocumentQueryRepository documents, IndexingTrigger indexingTrigger) {
        this.documents = documents;
        this.indexingTrigger = indexingTrigger;
    }

    public DocumentCreated intake(String filename, byte[] bytes) {
        DocumentContent.Parsed parsed = DocumentContent.fromUpload(filename, bytes);
        DocumentContent content = parsed.content();
        long id = documents.insertPending(new DocumentQueryRepository.NewDocument(
                "UPLOAD", parsed.sourceUri(), content.title(), content.content(),
                content.contentHash(), content.tags()));
        indexingTrigger.submit(id);
        return new DocumentCreated(id, content.title(), "UPLOAD", "PENDING");
    }

    public record DocumentCreated(@JsonProperty("id") long id,
                                  @JsonProperty("title") String title,
                                  @JsonProperty("source_type") String sourceType,
                                  @JsonProperty("index_status") String indexStatus) {}

    /** 收录被拒：message 面向用户，HTTP 层映射为 400。 */
    public static final class Rejected extends RuntimeException {
        public Rejected(String message) {
            super(message);
        }
    }
}
