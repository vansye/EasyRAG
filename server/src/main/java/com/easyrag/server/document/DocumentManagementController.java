package com.easyrag.server.document;

import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;

import java.util.Map;

@RestController
@RequestMapping("/api/documents")
public class DocumentManagementController {

    private final DocumentManagementService service;

    public DocumentManagementController(DocumentManagementService service) {
        this.service = service;
    }

    @PutMapping(value = "/{id}", consumes = MediaType.APPLICATION_JSON_VALUE)
    public DocumentManagementService.ChangeResult update(@PathVariable long id, @RequestBody UpdateRequest request) {
        if (request.content() == null || !request.content().isString()) {
            throw new DocumentIntakeService.Rejected("content 必须是字符串");
        }
        return service.update(id, request.content().stringValue());
    }

    @DeleteMapping("/{id}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void delete(@PathVariable long id) {
        service.delete(id);
    }

    @PostMapping("/{id}/reindex")
    @ResponseStatus(HttpStatus.ACCEPTED)
    public DocumentManagementService.ChangeResult reindex(@PathVariable long id) {
        return service.reindex(id);
    }

    @ExceptionHandler(DocumentManagementService.NotFound.class)
    public ResponseEntity<Map<String, String>> notFound(DocumentManagementService.NotFound failure) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(Map.of("error", failure.getMessage()));
    }

    @ExceptionHandler(DocumentIntakeService.Rejected.class)
    public ResponseEntity<Map<String, String>> badRequest(DocumentIntakeService.Rejected failure) {
        return ResponseEntity.badRequest().body(Map.of("error", failure.getMessage()));
    }

    @ExceptionHandler(DocumentManagementService.Busy.class)
    public ResponseEntity<Map<String, String>> busy(DocumentManagementService.Busy failure) {
        return ResponseEntity.status(HttpStatus.CONFLICT)
                .body(Map.of("error", failure.getMessage(), "state", failure.state().name()));
    }

    @ExceptionHandler(DocumentManagementService.MutationFailed.class)
    public ResponseEntity<Map<String, String>> failed(DocumentManagementService.MutationFailed failure) {
        return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
                .body(Map.of("error", failure.getMessage(), "state", "RECOVERY_REQUIRED"));
    }

    public record UpdateRequest(JsonNode content) {}
}
