from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from rag_notion_kb.config import ChunkingConfig
from rag_notion_kb.models import Chunk, ChunkMetadata, ChunkType, PageMetadata

logger = logging.getLogger(__name__)

_CODE_BLOCK_RE = re.compile(r"^```[ \t]*[^\n]*\n[\s\S]*?^```[ \t]*$", re.MULTILINE)
_TABLE_DELIMITER_RE = re.compile(r"\|[-:\s]+\|")
_IMAGE_RE = re.compile(r"!\[.*?\]\(.*?\)")
_TEXT_FENCE_RE = re.compile(
    r"^```[ \t]*(?:text|plain text|plaintext|plain|txt|markdown|md)[ \t]*\r?\n"
    r"(?P<body>.*?)^```[ \t]*\r?$",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_PLACEHOLDER_TEMPLATE = "__PRESERVED_BLOCK_{idx}__"
# Hard upper bound for protected blocks. DashScope accepts about 60k tokens and
# Milvus VARCHAR caps at 65535 chars; this keeps individual chunks safely below both.
PROTECTED_HARD_LIMIT = 8000
# Bump when any chunking/image-extraction behavior change must invalidate old indexes.
CHUNKING_VERSION = 3


@dataclass
class _ProtectedBlock:
    index: int
    text: str
    kind: str  # "code" or "table"


class MarkdownProcessor:

    """Split Notion-exported Markdown into semantic chunks.

    Tables, fenced code blocks, and image tags are extracted before header
    splitting and re-inserted as standalone chunks so they are never fragmented
    across chunk boundaries.  Image URLs (which can be very long Notion
    pre-signed URLs) are excluded from the chunk-size budget.
    """

    def __init__(self, chunking_config: ChunkingConfig) -> None:
        self.chunking_config = chunking_config
        headers = [
            ("#" * level, f"h{level}")
            for level in sorted(chunking_config.header_levels)
        ]
        self._splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=headers,
            strip_headers=False,
        )

    def split(self, markdown: str, page_metadata: PageMetadata) -> list[Chunk]:
        """Split markdown into chunks preserving tables/code blocks.

        Args:
            markdown: Markdown exported from a Notion page.
            page_metadata: Page-level metadata propagated to chunk metadata.

        Returns:
            Ordered list of chunks for this page.
        """
        prepared_source = self._normalize_text_fences(markdown)
        prepared_text, blocks = self._extract_protected_blocks(prepared_source)
        documents = self._splitter.split_text(prepared_text)

        pieces: list[tuple[str, dict]] = []
        for document in documents:
            pieces.extend(
                self._expand_placeholders(document.page_content, document.metadata, blocks)
            )

        pieces = self._enforce_chunk_size(pieces)
        pieces = self._merge_image_chunks(pieces)
        pieces = self._merge_small_chunks(pieces)

        chunks: list[Chunk] = []
        for chunk_index, (content, metadata_dict) in enumerate(pieces):
            chunk_type = self._classify(content, blocks, metadata_dict)
            header_level, header_path = self._derive_header_info(metadata_dict)
            chunk_metadata = ChunkMetadata(
                page_id=page_metadata.page_id,
                page_title=page_metadata.title,
                page_url=page_metadata.url,
                header_path=header_path,
                header_level=header_level,
                last_edited_time=page_metadata.last_edited_time,
                chunk_index=chunk_index,
                chunk_type=chunk_type,
                image_url=None,
            )
            chunks.append(Chunk(text=content, metadata=chunk_metadata))

        self._assign_source_offsets(chunks, markdown)
        return chunks

    @staticmethod
    def _normalize_text_fences(text: str) -> str:
        """Strip text-like fenced blocks so their bodies join normal Markdown text."""
        return _TEXT_FENCE_RE.sub(lambda match: match.group("body"), text)

    @staticmethod
    def _assign_source_offsets(chunks: list[Chunk], markdown: str) -> None:
        """Attach each chunk's position in the source Markdown.

        The splitter preserves document order, so a moving cursor can
        disambiguate repeated first lines such as multiple code fences. This
        is intentionally based on the first non-empty line rather than the
        full chunk text, because normalization can alter surrounding
        whitespace while leaving the leading content unchanged.
        """
        cursor = 0
        for chunk in chunks:
            stripped = chunk.text.strip()
            position = markdown.find(stripped, cursor)
            matched_length = len(stripped)
            if position < 0:
                first_line = next(
                    (line.strip() for line in stripped.splitlines() if line.strip()),
                    "",
                )
                if not first_line:
                    continue
                position = markdown.find(first_line, cursor)
                if position < 0:
                    position = markdown.find(first_line)
                matched_length = len(first_line)
            if position >= 0:
                chunk.source_offset = position
                cursor = max(cursor, position + max(matched_length, 1))

    def _extract_protected_blocks(
        self,
        text: str,
    ) -> tuple[str, dict[int, _ProtectedBlock]]:
        """Extract code blocks, tables, and images, replacing them with placeholders."""
        spans: list[tuple[int, int, str, str]] = []

        if self.chunking_config.preserve_code_blocks:
            for match in _CODE_BLOCK_RE.finditer(text):
                spans.append((match.start(), match.end(), match.group(), "code"))

        for match in _IMAGE_RE.finditer(text):
            spans.append((match.start(), match.end(), match.group(), "image"))

        if self.chunking_config.preserve_tables:
            spans.extend(self._find_table_spans(text))

        # Sort by start; code blocks take precedence, then images, then tables.
        _kind_order = {"code": 0, "image": 1, "table": 2}
        spans.sort(key=lambda s: (s[0], _kind_order.get(s[3], 99)))
        filtered_spans: list[tuple[int, int, str, str]] = []
        last_end = -1
        for start, end, content, kind in spans:
            if start >= last_end:
                filtered_spans.append((start, end, content, kind))
                last_end = end

        blocks: dict[int, _ProtectedBlock] = {}
        if not filtered_spans:
            return text, blocks

        # Build replaced text from spans.
        parts: list[str] = []
        cursor = 0
        for idx, (start, end, content, kind) in enumerate(filtered_spans):
            blocks[idx] = _ProtectedBlock(index=idx, text=content, kind=kind)
            if cursor < start:
                parts.append(text[cursor:start])
            placeholder = f"\n\n{_PLACEHOLDER_TEMPLATE.format(idx=idx)}\n\n"
            parts.append(placeholder)
            cursor = end
        if cursor < len(text):
            parts.append(text[cursor:])

        return "".join(parts), blocks

    def _find_table_spans(self, text: str) -> list[tuple[int, int, str, str]]:
        """Find maximal consecutive runs of Markdown table lines."""
        lines = text.splitlines(keepends=True)
        spans: list[tuple[int, int, str, str]] = []
        i = 0
        n = len(lines)
        offset = 0
        while i < n:
            if lines[i].lstrip().startswith("|"):
                start_line = i
                while i < n and lines[i].lstrip().startswith("|"):
                    i += 1
                end_line = i
                block = "".join(lines[start_line:end_line])
                if _TABLE_DELIMITER_RE.search(block):
                    start_offset = sum(len(line) for line in lines[:start_line])
                    end_offset = start_offset + len(block)
                    spans.append((start_offset, end_offset, block, "table"))
            else:
                i += 1
            offset += len(lines[i - 1]) if i > 0 else 0
        return spans

    def _expand_placeholders(
        self,
        text: str,
        metadata: dict,
        blocks: dict[int, _ProtectedBlock],
    ) -> list[tuple[str, dict]]:
        """Split a document into plain-text pieces and protected blocks.

        Returns pieces paired with the document's header metadata so every
        resulting chunk inherits the correct header context.
        """
        if not blocks:
            stripped = text.strip()
            return [(stripped, metadata)] if stripped else []

        pattern = re.compile(
            "(" + "|".join(re.escape(_PLACEHOLDER_TEMPLATE.format(idx=i)) for i in blocks) + ")"
        )
        parts = pattern.split(text)

        pieces: list[tuple[str, dict]] = []
        placeholder_to_block = {
            _PLACEHOLDER_TEMPLATE.format(idx=idx): block for idx, block in blocks.items()
        }
        for part in parts:
            stripped = part.strip()
            if not stripped:
                continue
            if stripped in placeholder_to_block:
                block = placeholder_to_block[stripped]
                pieces.append((block.text, {**metadata, '_protected': True, '_protected_kind': block.kind}))
            else:
                pieces.append((stripped, metadata))
        return pieces

    def _classify(
        self,
        content: str,
        blocks: dict[int, _ProtectedBlock],
        metadata: dict | None = None,
    ) -> ChunkType:
        """Determine chunk type from original block kind or content heuristics."""
        if metadata and metadata.get('_protected_kind'):
            kind = metadata['_protected_kind']
            if kind == 'code':
                return ChunkType.CODE
            if kind == 'table':
                return ChunkType.TABLE
        for block in blocks.values():
            if content == block.text:
                if block.kind == "code":
                    return ChunkType.CODE
                if block.kind == "table":
                    return ChunkType.TABLE
                if block.kind == "image":
                    return ChunkType.TEXT

        stripped = content.lstrip()
        if stripped.startswith("```"):
            return ChunkType.CODE
        if "|---" in content:
            return ChunkType.TABLE
        return ChunkType.TEXT

    @staticmethod
    def _heading_keys(meta: dict) -> dict:
        """Extract heading key-value pairs from splitter metadata."""
        return {
            k: meta[k]
            for k in sorted(meta, key=lambda x: int(x[1:]) if x[1:].isdigit() else 0)
            if k.startswith("h") and k[1:].isdigit()
        }

    @staticmethod
    def _is_heading_chunk(text: str, meta: dict) -> bool:
        """Check whether the chunk text is itself a heading line."""
        headings = MarkdownProcessor._heading_keys(meta)
        if not headings:
            return False
        first_line = text.strip().split("\n", 1)[0]
        deepest_key = max(headings, key=lambda k: int(k[1:]))
        expected = "#" * int(deepest_key[1:]) + " " + headings[deepest_key]
        return first_line == expected

    def _merge_small_chunks(self, pieces: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
        """Merge chunks smaller than min_chunk_size with same-section preceding chunk.

        Only merges when:
        - The chunk text length is below min_chunk_size.
        - The preceding chunk belongs to the same heading section (all heading
          keys and values match, not just the numeric level).
        - The current chunk is not a heading itself.
        - The merged result would not exceed max_chunk_size.
        - Protected blocks (code, table, image) are never merged.
        """
        min_size = self.chunking_config.min_chunk_size
        max_size = self.chunking_config.max_chunk_size
        if not pieces:
            return pieces

        result: list[tuple[str, dict]] = []
        for text, meta in pieces:
            stripped = text.strip()
            if len(stripped) < min_size and result:
                is_protected = meta.get('_protected', False)
                is_heading = MarkdownProcessor._is_heading_chunk(stripped, meta)
                if not is_protected and not is_heading:
                    prev_text, prev_meta = result[-1]
                    if MarkdownProcessor._heading_keys(meta) == MarkdownProcessor._heading_keys(prev_meta):
                        candidate = prev_text + "\n\n" + stripped
                        if len(candidate) <= max_size:
                            result[-1] = (candidate, prev_meta)
                            continue
            result.append((stripped, meta))
        return result


    def _enforce_chunk_size(self, pieces: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
        """Further split pieces that exceed max_chunk_size.

        Protected blocks (code, table, image) are never split regardless of
        size — they must remain intact as standalone chunks.
        """
        max_size = self.chunking_config.max_chunk_size
        overlap = int(max_size * 0.1)
        sub_splitter = RecursiveCharacterTextSplitter(
            chunk_size=max_size,
            chunk_overlap=overlap,
            separators=["\n\n", "\n", "。", "，", " ", ""],
        )
        hard_splitter = RecursiveCharacterTextSplitter(
            chunk_size=PROTECTED_HARD_LIMIT,
            chunk_overlap=0,
            separators=["\n", " ", ""],
        )
        result: list[tuple[str, dict]] = []
        for text, meta in pieces:
            if meta.get('_protected'):
                # Image blocks are handled separately by ImageExtractor and
                # should stay intact; only oversized code/table blocks are split.
                if meta.get('_protected_kind') == 'image' or len(text) <= PROTECTED_HARD_LIMIT:
                    result.append((text, meta))
                else:
                    for sub in hard_splitter.split_text(text):
                        if sub.strip():
                            result.append((
                                sub,
                                {**meta, '_protected': False},
                            ))
            elif len(text) <= max_size:
                result.append((text, meta))
            else:
                sub_texts = sub_splitter.split_text(text)
                for sub in sub_texts:
                    if sub.strip():
                        result.append((sub, meta))
        return result

    def _merge_image_chunks(self, pieces: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
        """Merge image-only pieces into the preceding text chunk.

        Standalone image markdown (``![alt](url)``) has no independent value
        for text retrieval, so it is merged backward into the preceding text
        chunk whenever the combined size stays within ``max_chunk_size``.
        Forward merging is deliberately avoided — it pulls images into the
        next section and breaks the original text-image ordering.
        Code and table pieces are left untouched.
        """
        max_size = self.chunking_config.max_chunk_size
        if not pieces:
            return pieces

        result: list[tuple[str, dict]] = []
        i = 0
        while i < len(pieces):
            text, meta = pieces[i]
            stripped = text.strip()
            is_image_only = _IMAGE_RE.fullmatch(stripped) is not None

            if not is_image_only:
                result.append((text, meta))
                i += 1
                continue

            # Try merging into the preceding piece (if it is not itself a protected block).
            if result:
                prev_text, prev_meta = result[-1]
                if not prev_meta.get("_protected"):
                    candidate = prev_text + "\n\n" + stripped
                    if len(candidate) <= max_size:
                        result[-1] = (candidate, prev_meta)
                        i += 1
                        continue

            # Cannot merge — drop the image piece; it has no retrieval value on its own.
            # Image content is handled separately via ImageDoc (multimodal embedding).
            i += 1

        return result

    def _derive_header_info(self, metadata: dict) -> tuple[int, str]:
        """Derive header_level and header_path from splitter metadata."""
        levels = sorted(
            int(key[1:]) for key in metadata if key.startswith("h") and key[1:].isdigit()
        )
        if not levels:
            return 1, "# (root)"

        header_level = levels[-1]
        parts = [f"{'#' * level} {metadata[f'h{level}']}" for level in levels]
        return header_level, " > ".join(parts)
