from __future__ import annotations

import logging
import re

from rag_notion_kb.models import ChunkType, SearchHit
from rag_notion_kb.storage.vector_store import VectorStore
from rag_notion_kb.utils.image_cache import remote_image_url, unpack_image_url

logger = logging.getLogger(__name__)

_SEGMENT_RE = re.compile(r"^(#+)\s+(.*)$")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]*)\)")


class ContextExpander:
    """Expand a retrieved chunk to include sibling content under a target heading level."""

    def __init__(self, store: VectorStore) -> None:
        self.store = store

    def expand(self, hit: SearchHit, expand_to_level: int) -> str:
        """Return the expanded context text for a search hit.

        Args:
            hit: The retrieved chunk.
            expand_to_level: Target heading level to expand to (1-6).

        Returns:
            Combined text of all chunks sharing the same ancestor heading prefix.
            If no sibling chunks are found, the original chunk text is returned.
        """
        target_level = min(hit.metadata.header_level, expand_to_level)
        prefix = self._parse_path(hit.metadata.header_path)[:target_level]

        chunks = self.store.get_page_chunks(hit.metadata.page_id)
        chunks.sort(key=lambda c: c.metadata.chunk_index)

        selected: list[SearchHit] = []
        for chunk in chunks:
            if chunk.metadata.chunk_type not in (ChunkType.TEXT, ChunkType.IMAGE):
                continue
            chunk_segments = self._parse_path(chunk.metadata.header_path)
            if len(chunk_segments) >= len(prefix) and chunk_segments[: len(prefix)] == prefix:
                selected.append(chunk)

        if not selected:
            return hit.chunk_text

        parts: list[str] = []
        seen_texts: set[str] = set()
        seen_image_urls: set[str] = set()
        for chunk in selected:
            if chunk.metadata.image_url:
                image_url = remote_image_url(chunk.metadata.image_url) or chunk.metadata.image_url
                display_url, _ = unpack_image_url(chunk.metadata.image_url)
                image_key = display_url or chunk.metadata.image_url
                if image_key in seen_image_urls:
                    continue
                seen_image_urls.add(image_key)
                parts.append(f"![image]({image_url})")
            else:
                text = chunk.chunk_text.strip()
                if not text or text in seen_texts:
                    continue
                seen_texts.add(text)

                cursor = 0
                for match in _IMAGE_RE.finditer(text):
                    text_before = text[cursor : match.start()].strip()
                    if text_before:
                        parts.append(text_before)

                    url = match.group(1)
                    display_url, _ = unpack_image_url(url)
                    image_key = display_url or url
                    if image_key not in seen_image_urls:
                        seen_image_urls.add(image_key)
                        image_url = remote_image_url(url) or url
                        parts.append(f"![image]({image_url})")
                    cursor = match.end()

                text_after = text[cursor:].strip()
                if text_after:
                    parts.append(text_after)
        return "\n\n".join(parts)

    def _parse_path(self, header_path: str) -> list[tuple[int, str]]:
        """Parse '# A > ## B' into [(1, 'A'), (2, 'B')]."""
        segments: list[tuple[int, str]] = []
        for segment in header_path.split(">"):
            segment = segment.strip()
            if not segment:
                continue
            match = _SEGMENT_RE.match(segment)
            if match:
                level = len(match.group(1))
                title = match.group(2).strip()
                segments.append((level, title))
        return segments
