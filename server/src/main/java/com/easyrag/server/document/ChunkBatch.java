package com.easyrag.server.document;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.nio.ByteBuffer;
import java.nio.CharBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Objects;

public record ChunkBatch(List<Chunk> chunks) {

    public ChunkBatch {
        if (chunks == null || chunks.isEmpty() || chunks.stream().anyMatch(Objects::isNull)) {
            throw new IllegalArgumentException("chunks must be non-empty and contain no null elements");
        }
        chunks = List.copyOf(chunks);
    }

    public void validateAgainst(String content) {
        byte[] source = utf8(content, "content");
        int previousEnd = 0;
        for (int sequence = 0; sequence < chunks.size(); sequence++) {
            Chunk chunk = chunks.get(sequence);
            if (chunk.seq() == null || chunk.seq() != sequence) {
                throw new IllegalArgumentException("seq must be consecutive from zero in response order");
            }
            if (chunk.byteStart() == null || chunk.byteEnd() == null
                    || chunk.byteStart() != previousEnd || chunk.byteEnd() <= chunk.byteStart()
                    || chunk.byteEnd() > source.length) {
                throw new IllegalArgumentException("byte_start/byte_end must cover consecutive non-empty UTF-8 ranges");
            }
            byte[] textBytes = utf8(chunk.text(), "text");
            if (chunk.text().codePoints().allMatch(character -> Character.isWhitespace(character)
                    || Character.isSpaceChar(character) || character == 0x85)) {
                throw new IllegalArgumentException("text must not be blank");
            }
            if (textBytes.length > 65535) {
                throw new IllegalArgumentException("text exceeds the MySQL TEXT limit of 65535 UTF-8 bytes");
            }
            utf8(chunk.headingPath(), "heading_path");
            if (chunk.headingPath().codePointCount(0, chunk.headingPath().length()) > 512) {
                throw new IllegalArgumentException("heading_path exceeds the MySQL limit of 512 code points");
            }
            if (chunk.tokenCount() == null || chunk.tokenCount() < 0) {
                throw new IllegalArgumentException("token_count must be a non-negative integer");
            }
            String decoded;
            try {
                decoded = StandardCharsets.UTF_8.newDecoder()
                        .onMalformedInput(CodingErrorAction.REPORT)
                        .onUnmappableCharacter(CodingErrorAction.REPORT)
                        .decode(ByteBuffer.wrap(source, chunk.byteStart(), chunk.byteEnd() - chunk.byteStart()))
                        .toString();
            } catch (CharacterCodingException failure) {
                throw new IllegalArgumentException("chunk range splits a UTF-8 encoding sequence", failure);
            }
            if (!decoded.equals(chunk.text())) {
                throw new IllegalArgumentException("text does not match the original content at byte_start/byte_end");
            }
            previousEnd = chunk.byteEnd();
        }
        if (previousEnd != source.length) {
            throw new IllegalArgumentException("chunks must cover the complete original content");
        }
    }

    private static byte[] utf8(String value, String field) {
        if (value == null) {
            throw new IllegalArgumentException(field + " must not be null");
        }
        try {
            ByteBuffer encoded = StandardCharsets.UTF_8.newEncoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .encode(CharBuffer.wrap(value));
            byte[] bytes = new byte[encoded.remaining()];
            encoded.get(bytes);
            return bytes;
        } catch (CharacterCodingException failure) {
            throw new IllegalArgumentException(field + " is not valid Unicode", failure);
        }
    }

    public record Chunk(Integer seq, String text,
                        @JsonProperty("byte_start") Integer byteStart,
                        @JsonProperty("byte_end") Integer byteEnd,
                        @JsonProperty("heading_path") String headingPath,
                        @JsonProperty("token_count") Integer tokenCount) {}
}
