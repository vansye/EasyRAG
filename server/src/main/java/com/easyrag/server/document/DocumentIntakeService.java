package com.easyrag.server.document;

import com.fasterxml.jackson.annotation.JsonProperty;
import org.springframework.stereotype.Service;

import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

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
 *    为准）。本类在入口一次性剥掉 UTF-8 BOM——它是编码痕迹而非内容，剥掉后
 *    全链路只面对一种形态，不需要每个下游各自处理。
 */
@Service
public class DocumentIntakeService {

    /** 产品上限 1 MB（A-3）：超限返回含实际大小的提示，不静默截断。 */
    static final int MAX_CONTENT_BYTES = 1024 * 1024;
    static final int MAX_TITLE_CODE_POINTS = 512;
    static final int MAX_SOURCE_URI_CODE_POINTS = 1024;

    private static final Set<String> ALLOWED_EXTENSIONS = Set.of("md", "txt");
    private static final Pattern H1 = Pattern.compile("^ {0,3}#[ \\t]+(.*?)[ \\t]*$", Pattern.MULTILINE);
    private static final Pattern FRONTMATTER = Pattern.compile(
            "\\A---[ \\t]*\\r?\\n(.*?)\\r?\\n---[ \\t]*(?:\\r?\\n|\\z)", Pattern.DOTALL);
    private static final Pattern KEY_VALUE = Pattern.compile("^([A-Za-z][A-Za-z0-9_-]*)[ \\t]*:[ \\t]*(.*)$");

    private final DocumentQueryRepository documents;
    private final IndexingTrigger indexingTrigger;

    public DocumentIntakeService(DocumentQueryRepository documents, IndexingTrigger indexingTrigger) {
        this.documents = documents;
        this.indexingTrigger = indexingTrigger;
    }

    public DocumentCreated intake(String filename, byte[] bytes) {
        String name = sanitizeFilename(filename);
        int dot = name.lastIndexOf('.');
        String extension = dot < 0 ? "" : name.substring(dot + 1).toLowerCase(Locale.ROOT);
        if (!ALLOWED_EXTENSIONS.contains(extension)) {
            throw new Rejected("不支持的文件类型"
                    + (extension.isBlank() ? "" : " ." + extension) + "（仅支持 .md / .txt）");
        }
        if (bytes.length > MAX_CONTENT_BYTES) {
            throw new Rejected("文件 " + describe(bytes.length) + " 超过收录上限 "
                    + describe(MAX_CONTENT_BYTES) + "，请拆分后再上传");
        }
        String content = stripBom(decodeStrictUtf8(bytes));
        if (content.isBlank()) {
            throw new Rejected("文件内容为空");
        }
        FrontMatter frontMatter = FrontMatter.parse(content);
        String title = resolveTitle(frontMatter.title(), frontMatter.body(),
                dot < 0 ? name : name.substring(0, dot));
        String contentHash = contentHash(content);
        long id = documents.insertPending(new DocumentQueryRepository.NewDocument(
                "UPLOAD", limited(name, MAX_SOURCE_URI_CODE_POINTS), title, content, contentHash,
                frontMatter.tags()));
        indexingTrigger.submit(id);
        return new DocumentCreated(id, title, "UPLOAD", "PENDING");
    }

    /**
     * 标题降级顺序（子 Issue A §一.1）：frontmatter.title → 正文首个 # 一级
     * 标题 → 文件名去扩展名。H1 扫描取 body（剥掉 frontmatter 块后的正文）；
     * 形如 "# 标题" 的行出现在围栏代码块内时会被误认，属已知近似——常规笔记
     * 中首个 H1 出现在代码块之前的概率极低，为它移植整套围栏解析不值得。
     */
    static String resolveTitle(String frontMatterTitle, String body, String fallback) {
        if (frontMatterTitle != null && !frontMatterTitle.isBlank()) {
            return limited(frontMatterTitle.strip(), MAX_TITLE_CODE_POINTS);
        }
        Matcher heading = H1.matcher(body);
        if (heading.find() && !heading.group(1).isBlank()) {
            return limited(heading.group(1).strip(), MAX_TITLE_CODE_POINTS);
        }
        return limited(fallback.isBlank() ? "未命名文档" : fallback, MAX_TITLE_CODE_POINTS);
    }

    /**
     * frontmatter 是可选增强（子 Issue A §一.1）：Obsidian / Hugo / Jekyll
     * 用户的笔记里常见，但不是普遍习惯。因此解析失败一律降级为「无
     * frontmatter」，不拒绝收录——无 frontmatter 用户的功能完整性优先于
     * 对格式严格性的追求。只认 title（单值）与 tags（内联数组 [a, b]），
     * 其余键忽略；不引入 YAML 库，这是两个键的全部需求。
     */
    record FrontMatter(String title, List<String> tags, String body) {

        static FrontMatter parse(String content) {
            Matcher block = FRONTMATTER.matcher(content);
            if (!block.find()) {
                return new FrontMatter(null, List.of(), content);
            }
            String title = null;
            List<String> tags = List.of();
            for (String line : block.group(1).split("\n", -1)) {
                Matcher entry = KEY_VALUE.matcher(line.strip());
                if (!entry.matches()) {
                    continue;
                }
                String key = entry.group(1);
                String value = entry.group(2).strip();
                if (key.equals("title") && title == null) {
                    // 引号未闭合说明这个值跨了行，而闭合边界 --- 可能落在值内部
                    // （对抗性审查发现：title: "说明\n---\n仍在标题值" 会把 title
                    // 解析成半截的 `"说明`）。正确处理跨行值需要真 YAML 解析器，
                    // 而本模块的口径是"只认两个键、不引 YAML 库"（A-1 §三）。
                    // 因此选保守失败：放弃这个 title，降级到正文 H1 或文件名。
                    // 宁可用一个次优但正确的标题，也不要一个截断错误的标题。
                    if (!hasBalancedQuotes(value)) {
                        continue;
                    }
                    title = unquote(value);
                } else if (key.equals("tags") && value.startsWith("[") && value.endsWith("]")) {
                    tags = parseTags(value);
                }
            }
            return new FrontMatter(title, tags, content.substring(block.end()));
        }

        private static boolean hasBalancedQuotes(String value) {
            return value.chars().filter(character -> character == '"').count() % 2 == 0;
        }

        /**
         * 逐字符扫描切分，引号内的逗号不算分隔符。
         *
         * 早先用 split(",")，`tags: ["a,b", c]` 会被切成 `["a, b", c]`——引号内
         * 的逗号被当成分隔符，且 unquote 无法还原（对抗性审查发现）。标签内容
         * 静默出错比拒绝更糟：用户看不出哪里不对，按标签过滤时才发现对不上。
         */
        private static List<String> parseTags(String value) {
            String inner = value.substring(1, value.length() - 1);
            List<String> items = new java.util.ArrayList<>();
            StringBuilder current = new StringBuilder();
            boolean quoted = false;
            for (int index = 0; index < inner.length(); index++) {
                char character = inner.charAt(index);
                if (character == '"') {
                    quoted = !quoted;
                    current.append(character);
                } else if (character == ',' && !quoted) {
                    addTag(items, current.toString());
                    current.setLength(0);
                } else {
                    current.append(character);
                }
            }
            addTag(items, current.toString());
            return List.copyOf(items);
        }

        private static void addTag(List<String> items, String raw) {
            String tag = unquote(raw.strip()).strip();
            if (!tag.isEmpty()) {
                items.add(tag);
            }
        }

        private static String unquote(String value) {
            return value.length() >= 2 && value.startsWith("\"") && value.endsWith("\"")
                    ? value.substring(1, value.length() - 1) : value;
        }
    }

    /** 变更检测的哈希口径（子 Issue A §一.2）：统一换行符 + 去首尾空白。 */
    private static String normalize(String content) {
        return content.replace("\r\n", "\n").replace("\r", "\n").strip();
    }

    static String contentHash(String content) {
        return sha256Hex(normalize(content));
    }

    private static String sha256Hex(String normalized) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(normalized.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException failure) {
            // JVM 规范保证 SHA-256 存在，到达这里说明运行时不合规
            throw new IllegalStateException("SHA-256 unavailable", failure);
        }
    }

    private static String decodeStrictUtf8(byte[] bytes) {
        try {
            return StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(bytes)).toString();
        } catch (CharacterCodingException failure) {
            throw new Rejected("文件内容不是有效的 UTF-8 文本");
        }
    }

    private static String stripBom(String content) {
        // 0xFEFF = UTF-8 BOM 解码后的字符（EF BB BF）；用数值常量而非
        // 字面量，避免源文件里出现不可见字符
        return !content.isEmpty() && content.charAt(0) == 0xFEFF ? content.substring(1) : content;
    }

    private static String sanitizeFilename(String filename) {
        if (filename == null || filename.isBlank()) {
            throw new Rejected("缺少文件名");
        }
        int separator = Math.max(filename.lastIndexOf('/'), filename.lastIndexOf('\\'));
        return separator >= 0 ? filename.substring(separator + 1) : filename;
    }

    private static String limited(String value, int maxCodePoints) {
        return value.codePointCount(0, value.length()) <= maxCodePoints ? value
                : value.substring(0, value.offsetByCodePoints(0, maxCodePoints));
    }

    /**
     * 人类可读的大小，附带精确字节数。
     *
     * 只给"1.0 MB"是不够的：超限 4 字节时两边都四舍五入成 1.0 MB，提示变成
     * "文件 1.0 MB 超过收录上限 1.0 MB"，用户看不出差在哪（端到端实测发现）。
     * 附上字节数让差异始终可见。
     */
    private static String describe(int bytes) {
        if (bytes >= 1024 * 1024) {
            return String.format(Locale.ROOT, "%.2f MB（%,d 字节）", bytes / 1048576.0, bytes);
        }
        if (bytes >= 1024) {
            return String.format(Locale.ROOT, "%.1f KB（%,d 字节）", bytes / 1024.0, bytes);
        }
        return bytes + " 字节";
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
