package com.easyrag.server.document;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
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

    public DocumentController(DocumentIntakeService intake, DocumentQueryRepository documents) {
        this.intake = intake;
        this.documents = documents;
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

    /** 调用方错误：收录被拒（面向用户的原因）或查询参数非法，统一 400。 */
    @ExceptionHandler({DocumentIntakeService.Rejected.class, BadRequest.class})
    public ResponseEntity<Map<String, String>> badRequest(RuntimeException failure) {
        return ResponseEntity.badRequest().body(Map.of("error", failure.getMessage()));
    }

    /** 不存在或已软删：404，与"从未存在"不可区分（对前端两者是同一件事）。 */
    @ExceptionHandler(NotFound.class)
    public ResponseEntity<Map<String, String>> notFound(NotFound failure) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(Map.of("error", "文档不存在或已删除"));
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
}
