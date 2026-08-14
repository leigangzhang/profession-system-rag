from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Sequence

from rag_notion_kb.models import ChunkMetadata, ChunkType, ImageDoc, PageMetadata
from rag_notion_kb.utils.image_cache import unpack_image_url

logger = logging.getLogger(__name__)

_IMAGE_RE = re.compile(r"!\[(.*?)\]\((.*?)\)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE_OPEN_RE = re.compile(r"^```[ \t]*([^\n]*)$")
_FENCE_CLOSE_RE = re.compile(r"^```[ \t]*$")
_TABLE_DELIMITER_RE = re.compile(r"\|[-:\s]+\|")
_TEXT_FENCE_LANGUAGES = frozenset(
    {"text", "plain text", "plaintext", "plain", "txt", "markdown", "md"}
)


def clean_image_alt(alt: str) -> str:
    """Return a compact, human-readable image label."""
    value = alt.strip().split("?", 1)[0].split("#", 1)[0]
    value = value.replace("\\", "/").rsplit("/", 1)[-1]
    if not value:
        return "image"
    return value[:120] or "image"


@dataclass
class _Heading:
    level: int
    title: str


def _is_paragraph_boundary(line: str) -> bool:
    """A blank line marks a paragraph boundary."""
    return line.strip() == ""


class ImageExtractor:
    """Extract images from Markdown into standalone ImageDoc records."""

    def __init__(self, context_window: int = 200) -> None:
        self.context_window = context_window

    def extract(self, markdown: str, page_metadata: PageMetadata) -> list[ImageDoc]:
        """Extract images with surrounding paragraphs and header metadata.

        The *before* context is the full preceding paragraph (back to the previous
        blank line or heading).  The *after* context is the full following paragraph.
        If either is shorter than ``context_window`` characters we additionally
        include partial adjacent paragraphs up to the window size.

        Args:
            markdown: Full Markdown exported from a Notion page.
            page_metadata: Page-level metadata used to populate ImageDoc fields.

        Returns:
            A list of ImageDoc records ordered by appearance in the source.
        """
        lines = markdown.splitlines()
        excluded_lines, text_fence_markers = self._context_line_boundaries(markdown)
        heading_stack: list[_Heading] = []
        image_docs: list[ImageDoc] = []
        line_offset = 0

        for line_index, line in enumerate(lines):
            line_start = line_offset
            line_offset += len(line) + 1
            heading_match = _HEADING_RE.match(line)
            if heading_match:
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                while heading_stack and heading_stack[-1].level >= level:
                    heading_stack.pop()
                heading_stack.append(_Heading(level=level, title=title))
                continue

            for match in _IMAGE_RE.finditer(line):
                alt = clean_image_alt(match.group(1))
                url = match.group(2)
                display_url, remote_url = unpack_image_url(url)
                current_level = self._current_level(heading_stack)

                # Paragraph-based context extraction
                before = self._extract_paragraph_before(
                    lines, line_index, current_level, excluded_lines, text_fence_markers
                )
                after = self._extract_paragraph_after(
                    lines, line_index, current_level, excluded_lines, text_fence_markers
                )

                # If paragraph context is short, supplement with character window
                if len(before) < self.context_window:
                    char_before = self._extract_before(
                        lines,
                        line_index,
                        level=current_level,
                        excluded_lines=excluded_lines,
                        text_fence_markers=text_fence_markers,
                    )
                    if len(char_before) > len(before):
                        before = char_before
                if len(after) < self.context_window:
                    char_after = self._extract_after(
                        lines,
                        line_index,
                        level=current_level,
                        excluded_lines=excluded_lines,
                        text_fence_markers=text_fence_markers,
                    )
                    if len(char_after) > len(after):
                        after = char_after

                # Keep the readable label and surrounding text, but omit the full
                # image URL (Notion presigned URLs can be tens of thousands of
                # characters and are not useful for text retrieval).
                context_text = f"{before}\n[Image: {alt}]\n{after}"
                header_path = self._build_header_path(heading_stack)
                metadata = ChunkMetadata(
                    page_id=page_metadata.page_id,
                    page_title=page_metadata.title,
                    page_url=page_metadata.url,
                    header_path=header_path,
                    header_level=current_level,
                    last_edited_time=page_metadata.last_edited_time,
                    chunk_index=len(image_docs),
                    chunk_type=ChunkType.IMAGE,
                    image_url=url,
                )
                image_docs.append(
                    ImageDoc(
                        image_url=display_url,
                        remote_url=remote_url,
                        alt=alt,
                        context_text=context_text,
                        metadata=metadata,
                        source_offset=line_start + match.start(),
                    )
                )

        return image_docs

    # ----- paragraph extraction -----

    @staticmethod
    def _context_line_boundaries(markdown: str) -> tuple[set[int], set[int]]:
        """Find pure code/table boundaries and text-like fence markers."""
        lines = markdown.splitlines()
        excluded: set[int] = set()
        text_markers: set[int] = set()
        i = 0
        while i < len(lines):
            fence_match = _FENCE_OPEN_RE.match(lines[i])
            if fence_match:
                close_index = i + 1
                while close_index < len(lines) and not _FENCE_CLOSE_RE.match(lines[close_index]):
                    close_index += 1
                if close_index < len(lines):
                    language = fence_match.group(1).strip().lower()
                    if language in _TEXT_FENCE_LANGUAGES:
                        text_markers.update({i, close_index})
                    else:
                        excluded.update(range(i, close_index + 1))
                    i = close_index + 1
                    continue

            if lines[i].lstrip().startswith("|"):
                start = i
                while i < len(lines) and lines[i].lstrip().startswith("|"):
                    i += 1
                if _TABLE_DELIMITER_RE.search("\n".join(lines[start:i])):
                    excluded.update(range(start, i))
                continue
            i += 1
        return excluded, text_markers

    def _extract_paragraph_before(
        self,
        lines: Sequence[str],
        line_index: int,
        level: int,
        excluded_lines: set[int],
        text_fence_markers: set[int],
    ) -> str:
        """Return the full paragraph immediately before *line_index*."""
        para_lines: list[str] = []
        for i in range(line_index - 1, -1, -1):
            if i in excluded_lines:
                break
            if i in text_fence_markers:
                continue
            line = lines[i]
            heading_match = _HEADING_RE.match(line)
            if heading_match and len(heading_match.group(1)) <= level:
                break
            if _is_paragraph_boundary(line):
                if para_lines:
                    break
                continue
            para_lines.append(line)
        para_lines.reverse()
        return "\n".join(para_lines).strip()

    def _extract_paragraph_after(
        self,
        lines: Sequence[str],
        line_index: int,
        level: int,
        excluded_lines: set[int],
        text_fence_markers: set[int],
    ) -> str:
        """Return the full paragraph immediately after *line_index*."""
        para_lines: list[str] = []
        past_blank = False
        for i in range(line_index + 1, len(lines)):
            if i in excluded_lines:
                break
            if i in text_fence_markers:
                continue
            line = lines[i]
            heading_match = _HEADING_RE.match(line)
            if heading_match and len(heading_match.group(1)) <= level:
                break
            if _is_paragraph_boundary(line):
                if para_lines:
                    break
                past_blank = True
                continue
            para_lines.append(line)
        return "\n".join(para_lines).strip()

    # ----- character-based extraction (fallback supplement) -----

    def _current_level(self, heading_stack: list[_Heading]) -> int:
        return heading_stack[-1].level if heading_stack else 1

    def _build_header_path(self, heading_stack: list[_Heading]) -> str:
        if not heading_stack:
            return "# (root)"
        return " > ".join(f"{'#' * h.level} {h.title}" for h in heading_stack)

    def _extract_before(
        self,
        lines: list[str],
        line_index: int,
        level: int,
        excluded_lines: set[int],
        text_fence_markers: set[int],
    ) -> str:
        collected: list[str] = []
        remaining = self.context_window
        for i in range(line_index - 1, -1, -1):
            if i in excluded_lines:
                break
            if i in text_fence_markers:
                continue
            line = lines[i]
            heading_match = _HEADING_RE.match(line)
            if heading_match and len(heading_match.group(1)) <= level:
                break
            if len(line) > remaining:
                collected.append(line[-remaining:])
                break
            collected.append(line)
            remaining -= len(line) + 1
        collected.reverse()
        return "\n".join(collected).strip()

    def _extract_after(
        self,
        lines: list[str],
        line_index: int,
        level: int,
        excluded_lines: set[int],
        text_fence_markers: set[int],
    ) -> str:
        collected: list[str] = []
        remaining = self.context_window
        for i in range(line_index + 1, len(lines)):
            if i in excluded_lines:
                break
            if i in text_fence_markers:
                continue
            line = lines[i]
            heading_match = _HEADING_RE.match(line)
            if heading_match and len(heading_match.group(1)) <= level:
                break
            if len(line) > remaining:
                collected.append(line[:remaining])
                break
            collected.append(line)
            remaining -= len(line) + 1
        return "\n".join(collected).strip()
