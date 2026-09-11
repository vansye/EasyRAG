-- EasyRAG 初始化 schema：document 与 chunk（子 Issue A §二数据原型）
-- 约定：MySQL 8、utf8mb4、InnoDB。表结构与伪代码保持一致，字段注释回指文档编号。

CREATE TABLE document (
    id           BIGINT       NOT NULL AUTO_INCREMENT,
    source_type  ENUM('UPLOAD','URL') NOT NULL,          -- 预留 FOLDER（见 #1 收录入口裁决）
    source_uri   VARCHAR(1024) NOT NULL,                 -- 上传=原始文件名，URL=链接
    title        VARCHAR(512)  NOT NULL,                 -- 见降级顺序（frontmatter → 一级标题 → 文件名）
    content      LONGTEXT      NOT NULL,                 -- 原文（真相源，溯源展示与全量重建依赖它）
    content_hash CHAR(64)      NOT NULL,                 -- SHA-256(规范化正文)
    tags         JSON          DEFAULT (JSON_ARRAY()),   -- frontmatter.tags，可空（无 frontmatter 用户）
    index_status ENUM('PENDING','INDEXING','INDEXED','FAILED') NOT NULL DEFAULT 'PENDING',
    index_error  VARCHAR(1024) NULL,
    chunk_count  INT           NOT NULL DEFAULT 0,        -- MySQL 中现存 chunk 行数（库内事实，非索引事实，见 A-6）
    created_at   DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    indexed_at   DATETIME      NULL,
    deleted_at   DATETIME      NULL,                      -- 软删标记

    PRIMARY KEY (id),
    KEY idx_index_status (index_status),
    KEY idx_deleted_at (deleted_at)
    -- 不对 content_hash 加唯一约束：同内容不同来源是两份资料，去重会让
    --   "上传成功但列表里没有"变成难以解释的行为（子 Issue A §二）。
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE chunk (
    id           BIGINT       NOT NULL AUTO_INCREMENT,    -- 即 chunk_id，由 Java 生成（唯一写入者）
    document_id  BIGINT       NOT NULL,
    seq          INT          NOT NULL,                   -- 文档内序号，从 0 起
    text         TEXT         NOT NULL,                   -- 切片正文（原文子串，见契约测试）
    char_start   INT          NOT NULL,                   -- 在 document.content 中的起始偏移
    char_end     INT          NOT NULL,                   -- 结束偏移（前端据此高亮原文）
    heading_path VARCHAR(512) NULL,                       -- 所属标题路径，如 "三、工作机制 > 1. 推理阶段划分"
    token_count  INT          NOT NULL DEFAULT 0,
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    KEY idx_document_seq (document_id, seq),
    CONSTRAINT fk_chunk_document FOREIGN KEY (document_id) REFERENCES document (id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
