package com.easyrag.server.document;

import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MaxUploadSizeExceededException;
import org.springframework.web.multipart.MultipartFile;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * 资料收录与列表（A-1 文档 §三）。模块 A 是唯一的对前端入口。
 */
@RestController
@RequestMapping("/api/documents")
public class DocumentController {

    private static final Set<String> KNOWN_STATUSES = Set.of("PENDING", "INDEXING", "INDEXED", "FAILED");
    private static final int MAX_PAGE_SIZE = 100;

    private final DocumentIntakeService intake;
    private final DocumentQueryRepository documents;
    private final DocumentDeletionService deletion;
    private final DocumentUpdateService update;

    public DocumentController(DocumentIntakeService intake, DocumentQueryRepository documents,
                              DocumentDeletionService deletion, DocumentUpdateService update) {
        this.intake = intake;
        this.documents = documents;
        this.deletion = deletion;
        this.update = update;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public DocumentIntakeService.DocumentCreated upload(@RequestParam("file") MultipartFile file) {
        try {
            // 不等索引：PENDING 即返回（A-1 §四示例①）
            return intake.intake(file.getOriginalFilename(), file.getBytes());
        } catch (IOException failure) {
            // 读取 multipart 临时文件失败：服务端自身问题，不是调用方错误
            throw new UncheckedIOException(failure);
        }
    }

    @GetMapping
    public DocumentQueryRepository.DocumentPage list(
            @RequestParam(name = "status", required = false) String status,
            @RequestParam(name = "page", defaultValue = "0") int page,
            @RequestParam(name = "size", defaultValue = "20") int size) {
        String normalized = status == null || status.isBlank()
                ? null : status.toUpperCase(Locale.ROOT);
        if (normalized != null && !KNOWN_STATUSES.contains(normalized)) {
            throw new BadRequest("status 只能是 PENDING / INDEXING / INDEXED / FAILED");
        }
        if (page < 0) {
            throw new BadRequest("page 不能为负数");
        }
        if (size < 1 || size > MAX_PAGE_SIZE) {
            throw new BadRequest("size 须在 1 到 " + MAX_PAGE_SIZE + " 之间");
        }
        return documents.findPage(normalized, page, size);
    }

    @GetMapping("/{id}")
    public DocumentQueryRepository.DocumentDetail detail(@PathVariable("id") long id) {
        return documents.findDetail(id).orElseThrow(NotFound::new);
    }

    /** 切片透明度（A-2 §三）：让"切成什么样"可见，也是调试切分策略的入口。 */
    @GetMapping("/{id}/chunks")
    public ChunkList chunks(@PathVariable("id") long id) {
        if (!documents.existsActive(id)) {
            throw new NotFound();
        }
        return new ChunkList(documents.findChunks(id));
    }

    /** 删除（A-2，U2）：先清索引后落库（方案 A），编排与失败语义见 DocumentDeletionService。 */
    @DeleteMapping("/{id}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void delete(@PathVariable("id") long id) {
        deletion.delete(id);
    }

    /**
     * 更新正文（A-2，U3）——multipart 形态：换一个文件覆盖原文。
     *
     * 两种形态分成两个方法而不是一个方法内判断 content type：Spring 按
     * consumes 分派本就支持，手写判断反而要处理"两个都给了/都没给"的组合。
     */
    @PutMapping(value = "/{id}", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public DocumentUpdateService.UpdateResult updateByUpload(
            @PathVariable("id") long id, @RequestParam("file") MultipartFile file) {
        try {
            return update.updateFromUpload(id, file.getOriginalFilename(), file.getBytes());
        } catch (IOException failure) {
            throw new UncheckedIOException(failure);
        }
    }

    /** 更新正文——JSON 形态：直接提交新正文，标题降级到文档现有标题。 */
    @PutMapping(value = "/{id}", consumes = MediaType.APPLICATION_JSON_VALUE)
    public DocumentUpdateService.UpdateResult updateByText(
            @PathVariable("id") long id, @RequestBody ContentUpdate body) {
        if (body == null || body.content() == null) {
            throw new BadRequest("content 不能为空");
        }
        return update.updateFromText(id, body.content());
    }

    /** 手动重索引（A-2）：FAILED 与 INDEXED 均可，PENDING/INDEXING 返回 409。 */
    @PostMapping("/{id}/reindex")
    @ResponseStatus(HttpStatus.ACCEPTED)
    public DocumentUpdateService.UpdateResult reindex(@PathVariable("id") long id) {
        return update.reindex(id);
    }

    /** 调用方错误：收录被拒（面向用户的原因）或查询参数非法，统一 400。 */
    @ExceptionHandler({DocumentIntakeService.Rejected.class, BadRequest.class})
    public ResponseEntity<Map<String, String>> badRequest(RuntimeException failure) {
        return ResponseEntity.badRequest().body(Map.of("error", failure.getMessage()));
    }

    /** 不存在或已软删：404，与"从未存在"不可区分（对前端两者是同一件事）。 */
    @ExceptionHandler({NotFound.class, DocumentDeletionService.NotFound.class,
            DocumentUpdateService.NotFound.class})
    public ResponseEntity<Map<String, String>> notFound(RuntimeException failure) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(Map.of("error", "文档不存在或已删除"));
    }

    /** 已在队列或索引在途：409，与"文档不存在"必须可区分。 */
    @ExceptionHandler(DocumentUpdateService.InFlight.class)
    public ResponseEntity<Map<String, String>> inFlight(DocumentUpdateService.InFlight failure) {
        return ResponseEntity.status(HttpStatus.CONFLICT)
                .body(Map.of("error", "文档正在索引或已在队列中，无需重复提交"));
    }

    /** 更新时闸门不可用：503 携带状态。 */
    @ExceptionHandler(DocumentUpdateService.Unavailable.class)
    public ResponseEntity<Map<String, String>> updateUnavailable(DocumentUpdateService.Unavailable failure) {
        return ResponseEntity.status(503)
                .body(Map.of("error", "更新暂不可用，请稍后重试", "state", failure.state()));
    }

    /** 删除时闸门不可用：503 携带状态，前端可区分"稍后再试"与"删除失败"。 */
    @ExceptionHandler(DocumentDeletionService.Unavailable.class)
    public ResponseEntity<Map<String, String>> deletionUnavailable(DocumentDeletionService.Unavailable failure) {
        return ResponseEntity.status(503)
                .body(Map.of("error", "删除暂不可用，请稍后重试", "state", failure.state()));
    }

    /** Python 清索引失败：文档保持原状（未删除），502 归因给上游。 */
    @ExceptionHandler(DocumentDeletionService.IndexCleanupFailed.class)
    public ResponseEntity<Map<String, String>> indexCleanupFailed(DocumentDeletionService.IndexCleanupFailed failure) {
        return ResponseEntity.status(502).body(Map.of("error", "索引清理暂不可用，文档未删除，请稍后重试"));
    }

    /** MySQL 落库失败：事务回滚，文档保持原状，500 如实归因给自身。 */
    @ExceptionHandler(DocumentDeletionService.StoreFailed.class)
    public ResponseEntity<Map<String, String>> storeFailed(DocumentDeletionService.StoreFailed failure) {
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(Map.of("error", "删除失败，文档保持原状，请稍后重试"));
    }

    /**
     * 超过 multipart 硬上限（4 MB，见 application.yml）的上传。产品上限
     * 1 MB 由 IntakeService 判定并能报实际大小；这个只兜更极端的体量。
     * resolve-lazily=true 让异常在参数解析阶段抛出，能进入本 handler
     * 映射成 400，而不是 multipart 预解析阶段裸抛成 500。
     */
    @ExceptionHandler(MaxUploadSizeExceededException.class)
    public ResponseEntity<Map<String, String>> tooLarge(MaxUploadSizeExceededException failure) {
        return ResponseEntity.badRequest()
                .body(Map.of("error", "文件超过收录上限（1 MB），请拆分后再上传"));
    }

    static final class BadRequest extends RuntimeException {
        BadRequest(String message) {
            super(message);
        }
    }

    static final class NotFound extends RuntimeException {
        NotFound() {
            super("document not found or deleted");
        }
    }

    record ChunkList(@JsonProperty("items") List<DocumentQueryRepository.ChunkRow> items) {}

    record ContentUpdate(@JsonProperty("content") String content) {}
}
