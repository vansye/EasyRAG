package com.easyrag.server.contract;

import com.easyrag.server.document.ChunkBatch;
import com.fasterxml.jackson.annotation.JsonProperty;
import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestFactory;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import tools.jackson.databind.json.JsonMapper;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.DynamicTest.dynamicTest;

class Utf8OffsetContractTest {

    @TestFactory
    Stream<DynamicTest> sharedChunksRoundTripThroughUtf8AndUtf16() throws IOException {
        OffsetContract contract = loadContract();
        assertEquals("utf8_bytes", contract.offsetUnit());
        assertEquals("[start,end)", contract.interval());
        return contract.cases().stream().map(document -> dynamicTest(document.name(), () -> {
            batchOf(document).validateAgainst(document.text());
            byte[] source = document.text().getBytes(StandardCharsets.UTF_8);
            StringBuilder reconstructed = new StringBuilder();
            int previousEnd = 0;
            int sequence = 0;
            for (ChunkFixture chunk : document.chunks()) {
                assertEquals(sequence++, chunk.seq());
                assertEquals(previousEnd, chunk.byteStart());
                assertTrue(chunk.byteEnd() > chunk.byteStart());
                assertTrue(chunk.byteEnd() <= source.length);
                assertEquals(chunk.text(), decodeUtf8(source, chunk.byteStart(), chunk.byteEnd()));
                int utf16Start = decodeUtf8(source, 0, chunk.byteStart()).length();
                int utf16End = decodeUtf8(source, 0, chunk.byteEnd()).length();
                assertEquals(chunk.text(), document.text().substring(utf16Start, utf16End));
                reconstructed.append(chunk.text());
                previousEnd = chunk.byteEnd();
            }
            assertEquals(source.length, previousEnd);
            assertEquals(document.text(), reconstructed.toString());
        }));
    }

    @Test
    void byteOffsetsCannotBeUsedDirectlyAsJavaStringIndices() throws IOException {
        DocumentFixture document = documentNamed(loadContract(), "mixed_lf");
        ChunkFixture lastChunk = document.chunks().get(document.chunks().size() - 1);
        assertTrue(lastChunk.byteEnd() > document.text().length());
        assertThrows(IndexOutOfBoundsException.class,
                () -> document.text().substring(lastChunk.byteStart(), lastChunk.byteEnd()));
    }

    @Test
    void equalNormalizedHashesDoNotVersionRawOffsets() throws Exception {
        OffsetContract contract = loadContract();
        DocumentFixture previous = documentNamed(contract, "mixed_crlf");
        DocumentFixture updated = documentNamed(contract, "mixed_lf");
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        assertArrayEquals(digest.digest(normalized(previous.text()).getBytes(StandardCharsets.UTF_8)),
                digest.digest(normalized(updated.text()).getBytes(StandardCharsets.UTF_8)));
        int previousEnd = previous.chunks().get(previous.chunks().size() - 1).byteEnd();
        assertTrue(previousEnd > updated.text().getBytes(StandardCharsets.UTF_8).length);
        assertThrows(IllegalArgumentException.class,
                () -> batchOf(previous).validateAgainst(updated.text()));
    }

    @Test
    void chunkResponseUsesExplicitByteFields() throws IOException {
        JsonMapper mapper = JsonMapper.builder().build();
        ChunkBatch batch = mapper.readValue("""
                {"chunks":[{"seq":0,"text":"中😀x","byte_start":0,"byte_end":8,
                            "heading_path":"章节","token_count":4}]}
                """, ChunkBatch.class);

        batch.validateAgainst("中😀x");
        assertEquals(0, batch.chunks().get(0).byteStart());
        assertEquals(8, batch.chunks().get(0).byteEnd());
        assertEquals("章节", batch.chunks().get(0).headingPath());
        assertEquals(4, batch.chunks().get(0).tokenCount());
        var serialized = mapper.readTree(mapper.writeValueAsString(batch)).get("chunks").get(0);
        assertTrue(serialized.has("byte_start"));
        assertTrue(serialized.has("byte_end"));
        assertFalse(serialized.has("char_start"));
        assertFalse(serialized.has("char_end"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"seq", "text", "byte_start", "byte_end", "heading_path", "token_count"})
    void missingFieldsAreNotSilentlyDefaulted(String field) throws IOException {
        Map<String, Object> chunk = new LinkedHashMap<>(Map.of(
                "seq", 0, "text", "中😀x", "byte_start", 0, "byte_end", 8,
                "heading_path", "", "token_count", 4));
        chunk.remove(field);
        JsonMapper mapper = JsonMapper.builder().build();
        ChunkBatch batch = mapper.readValue(
                mapper.writeValueAsString(Map.of("chunks", List.of(chunk))), ChunkBatch.class);

        assertThrows(IllegalArgumentException.class, () -> batch.validateAgainst("中😀x"));
    }

    @TestFactory
    Stream<DynamicTest> invalidRangesAndSequencesAreRejected() {
        List<InvalidResult> results = List.of(
                new InvalidResult("negative start", List.of(chunk(0, "中😀x", -1, 8))),
                new InvalidResult("empty range", List.of(chunk(0, "中😀x", 0, 0))),
                new InvalidResult("reversed range", List.of(chunk(0, "中😀x", 3, 2))),
                new InvalidResult("out of bounds", List.of(chunk(0, "中😀x", 0, 9))),
                new InvalidResult("split Chinese encoding", List.of(chunk(0, "中", 0, 2),
                        chunk(1, "😀x", 2, 8))),
                new InvalidResult("split emoji encoding", List.of(chunk(0, "中", 0, 4),
                        chunk(1, "😀x", 4, 8))),
                new InvalidResult("wrong text", List.of(chunk(0, "文😀x", 0, 8))),
                new InvalidResult("negative seq", List.of(chunk(-1, "中😀x", 0, 8))),
                new InvalidResult("first seq not zero", List.of(chunk(1, "中😀x", 0, 8))),
                new InvalidResult("duplicate seq", List.of(chunk(0, "中", 0, 3),
                        chunk(0, "😀x", 3, 8))),
                new InvalidResult("skipped seq", List.of(chunk(0, "中", 0, 3),
                        chunk(2, "😀x", 3, 8))),
                new InvalidResult("missing prefix", List.of(chunk(0, "😀x", 3, 8))),
                new InvalidResult("missing suffix", List.of(chunk(0, "中", 0, 3))),
                new InvalidResult("gap", List.of(chunk(0, "中", 0, 3), chunk(1, "x", 7, 8))),
                new InvalidResult("overlap", List.of(chunk(0, "中", 0, 3),
                        chunk(1, "中😀x", 0, 8))),
                new InvalidResult("negative tokens", List.of(
                        new ChunkBatch.Chunk(0, "中😀x", 0, 8, "", -1))),
                new InvalidResult("invalid chunk Unicode", List.of(chunk(0, "\uD800", 0, 8))),
                new InvalidResult("invalid heading Unicode", List.of(
                        new ChunkBatch.Chunk(0, "中😀x", 0, 8, "\uD800", 1))),
                new InvalidResult("heading exceeds database limit", List.of(
                        new ChunkBatch.Chunk(0, "中😀x", 0, 8, "😀".repeat(513), 1))));

        return results.stream().map(result -> dynamicTest(result.name(), () ->
                assertThrows(IllegalArgumentException.class,
                        () -> new ChunkBatch(result.chunks()).validateAgainst("中😀x"))));
    }

    @Test
    void batchRequiresNonemptyChunksWithoutNullElements() {
        assertThrows(IllegalArgumentException.class, () -> new ChunkBatch(null));
        assertThrows(IllegalArgumentException.class, () -> new ChunkBatch(List.of()));
        assertThrows(IllegalArgumentException.class, () -> new ChunkBatch(Arrays.asList((ChunkBatch.Chunk) null)));
    }

    @Test
    void batchDefensivelyCopiesChunksBeforeValidationAndPersistence() {
        List<ChunkBatch.Chunk> chunks = new ArrayList<>(List.of(chunk(0, "中😀x", 0, 8)));
        ChunkBatch batch = new ChunkBatch(chunks);
        chunks.clear();

        batch.validateAgainst("中😀x");
        assertEquals(1, batch.chunks().size());
        assertThrows(UnsupportedOperationException.class, () -> batch.chunks().clear());
    }

    @ParameterizedTest
    @ValueSource(strings = {"", " \t\r\n", "\u00A0", "\u0085", "\u2007", "\u202F"})
    void blankChunkCannotProceedToEmbed(String text) {
        ChunkBatch batch = new ChunkBatch(List.of(chunk(0, text, 0, text.getBytes(StandardCharsets.UTF_8).length)));

        assertThrows(IllegalArgumentException.class, () -> batch.validateAgainst(text));
    }

    @Test
    void sourceCannotUseReplacementEncodingForUnpairedSurrogates() {
        ChunkBatch batch = new ChunkBatch(List.of(chunk(0, "?", 0, 1)));

        assertThrows(IllegalArgumentException.class, () -> batch.validateAgainst("\uD800"));
        assertThrows(IllegalArgumentException.class, () -> batch.validateAgainst(null));
    }

    @Test
    void databaseLimitsUseUtf8BytesForTextAndCodePointsForHeading() {
        String text = "中".repeat(21845);
        ChunkBatch batch = new ChunkBatch(List.of(
                new ChunkBatch.Chunk(0, text, 0, 65535, "😀".repeat(512), 0)));
        batch.validateAgainst(text);

        String tooLarge = text + "x";
        ChunkBatch oversized = new ChunkBatch(List.of(chunk(0, tooLarge, 0, 65536)));
        assertThrows(IllegalArgumentException.class, () -> oversized.validateAgainst(tooLarge));
    }

    @Test
    void validCodePointBoundariesNeedNotBeWholeGraphemeBoundaries() {
        String source = "e\u0301👩\u200D💻";
        ChunkBatch batch = new ChunkBatch(List.of(
                chunk(0, "e", 0, 1), chunk(1, "\u0301", 1, 3),
                chunk(2, "👩", 3, 7), chunk(3, "\u200D", 7, 10), chunk(4, "💻", 10, 14)));

        batch.validateAgainst(source);
    }

    private static ChunkBatch.Chunk chunk(Integer seq, String text, Integer start, Integer end) {
        return new ChunkBatch.Chunk(seq, text, start, end, "", 1);
    }

    private ChunkBatch batchOf(DocumentFixture document) {
        return new ChunkBatch(document.chunks().stream().map(chunk ->
                new ChunkBatch.Chunk(chunk.seq(), chunk.text(), chunk.byteStart(), chunk.byteEnd(),
                        chunk.headingPath(), 1)).toList());
    }

    private OffsetContract loadContract() throws IOException {
        try (var input = getClass().getResourceAsStream("/contracts/utf8-offsets.json")) {
            assertNotNull(input);
            return JsonMapper.builder().build().readValue(input, OffsetContract.class);
        }
    }

    private DocumentFixture documentNamed(OffsetContract contract, String name) {
        return contract.cases().stream().filter(document -> document.name().equals(name)).findFirst().orElseThrow();
    }

    private String decodeUtf8(byte[] source, int start, int end) throws CharacterCodingException {
        return StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(source, start, end - start)).toString();
    }

    private String normalized(String text) {
        return text.replace("\r\n", "\n").replace('\r', '\n').strip();
    }

    private record OffsetContract(@JsonProperty("offset_unit") String offsetUnit,
                                  String interval, List<DocumentFixture> cases) {}

    private record DocumentFixture(String name, String text, List<ChunkFixture> chunks) {}

    private record ChunkFixture(int seq, String text, @JsonProperty("heading_path") String headingPath,
                                @JsonProperty("byte_start") int byteStart,
                                @JsonProperty("byte_end") int byteEnd) {}

    private record InvalidResult(String name, List<ChunkBatch.Chunk> chunks) {}
}
