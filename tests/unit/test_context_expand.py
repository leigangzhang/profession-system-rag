from __future__ import annotations

import unittest

from rag_notion_kb.models import ChunkMetadata, ChunkType, SearchHit
from rag_notion_kb.retrieval.context_expand import ContextExpander
from rag_notion_kb.storage.vector_store import VectorStore
from rag_notion_kb.utils.image_cache import pack_image_url


class _FakeStore(VectorStore):
    def __init__(self, chunks: list[SearchHit]) -> None:
        self.chunks = chunks


    def init_collection(self, dim: int) -> None: ...

    def upsert_page(
        self,
        page_id: str,
        chunks: list[SearchHit],
        image_docs: list,
        embeddings: list[list[float]],
    ) -> int:
        return 0

    def delete_by_page_id(self, page_id: str) -> int:
        return 0

    def search_dense(
        self,
        vector: list[float],
        limit: int,
        filters: dict | None = None,
    ) -> list[SearchHit]:
        return []

    def search_sparse(
        self,
        query_text: str,
        limit: int,
        filters: dict | None = None,
    ) -> list[SearchHit]:
        return []

    def stats(self) -> dict:
        return {}
    def get_page_chunks(self, page_id: str) -> list[SearchHit]:
        return [c for c in self.chunks if c.metadata.page_id == page_id]


def _chunk(
    chunk_id: int,
    page_id: str,
    text: str,
    header_path: str,
    header_level: int,
    image_url: str | None = None,
    chunk_type: ChunkType | None = None,
) -> SearchHit:
    return SearchHit(
        id=chunk_id,
        chunk_text=text,
        score=0.5,
        metadata=ChunkMetadata(
            page_id=page_id,
            page_title="Test",
            page_url="https://notion.so/page",
            header_path=header_path,
            header_level=header_level,
            last_edited_time="2026-08-10T10:00:00Z",
            chunk_index=chunk_id,
            chunk_type=ChunkType.IMAGE if image_url else chunk_type or ChunkType.TEXT,
            image_url=image_url,
        ),
    )


class TestContextExpander(unittest.TestCase):
    def test_expand_to_parent_heading(self) -> None:
        page_id = "page-1"
        chunks = [
            _chunk(0, page_id, "Intro", "# Root", 1),
            _chunk(1, page_id, "A detail", "# Root > ## Section A", 2),
            _chunk(2, page_id, "Another detail", "# Root > ## Section A", 2),
            _chunk(3, page_id, "B detail", "# Root > ## Section B", 2),
        ]
        hit = _chunk(2, page_id, "Another detail", "# Root > ## Section A", 2)
        expander = ContextExpander(_FakeStore(chunks))
        result = expander.expand(hit, expand_to_level=2)
        self.assertIn("A detail", result)
        self.assertIn("Another detail", result)
        self.assertNotIn("B detail", result)

    def test_expand_to_root(self) -> None:
        page_id = "page-1"
        chunks = [
            _chunk(0, page_id, "Intro", "# Root", 1),
            _chunk(1, page_id, "A detail", "# Root > ## Section A", 2),
            _chunk(2, page_id, "B detail", "# Root > ## Section B", 2),
        ]
        hit = _chunk(1, page_id, "A detail", "# Root > ## Section A", 2)
        expander = ContextExpander(_FakeStore(chunks))
        result = expander.expand(hit, expand_to_level=1)
        self.assertIn("Intro", result)
        self.assertIn("A detail", result)
        self.assertIn("B detail", result)

    def test_image_rendered(self) -> None:
        page_id = "page-1"
        chunks = [
            _chunk(0, page_id, "Text", "# Root", 1),
            _chunk(
                1,
                page_id,
                "",
                "# Root > ## Section",
                2,
                image_url="https://example.com/img.png",
            ),
        ]
        hit = _chunk(1, page_id, "", "# Root > ## Section", 2, image_url="https://example.com/img.png")
        expander = ContextExpander(_FakeStore(chunks))
        result = expander.expand(hit, expand_to_level=2)
        self.assertIn("![image](https://example.com/img.png)", result)

    def test_packed_image_uses_remote_url(self) -> None:
        page_id = "page-1"
        packed = pack_image_url(
            "/images/page/file.png",
            "https://example.com/signed.png?X-Amz-Signature=abc",
        )
        chunks = [_chunk(0, page_id, "", "# Root", 1, image_url=packed)]
        hit = _chunk(0, page_id, "", "# Root", 1, image_url=packed)

        result = ContextExpander(_FakeStore(chunks)).expand(hit, expand_to_level=1)

        self.assertIn("![image](https://example.com/signed.png?X-Amz-Signature=abc)", result)

    def test_image_expansion_excludes_code_and_table_chunks(self) -> None:
        page_id = "page-1"
        chunks = [
            _chunk(0, page_id, "Text sibling", "# Root", 1),
            _chunk(1, page_id, "select 1;", "# Root", 1, chunk_type=ChunkType.CODE),
            _chunk(2, page_id, "| Col | Value |", "# Root", 1, chunk_type=ChunkType.TABLE),
            _chunk(
                3,
                page_id,
                "",
                "# Root",
                1,
                image_url="https://example.com/img.png",
            ),
        ]
        hit = _chunk(3, page_id, "", "# Root", 1, image_url="https://example.com/img.png")

        result = ContextExpander(_FakeStore(chunks)).expand(hit, expand_to_level=1)

        self.assertIn("Text sibling", result)
        self.assertIn("![image](https://example.com/img.png)", result)
        self.assertNotIn("select 1;", result)
        self.assertNotIn("| Col | Value |", result)

    def test_duplicate_image_is_rendered_once(self) -> None:
        page_id = "page-1"
        packed = pack_image_url(
            "/images/page/file.png",
            "https://example.com/signed.png?X-Amz-Signature=abc",
        )
        chunks = [
            _chunk(0, page_id, "Before", "# Root > ## Section", 2),
            _chunk(
                1,
                page_id,
                f"Before image\n![image]({packed})\nAfter image",
                "# Root > ## Section",
                2,
            ),
            _chunk(
                2,
                page_id,
                "image context",
                "# Root > ## Section",
                2,
                image_url=packed,
            ),
        ]
        hit = _chunk(1, page_id, "", "# Root > ## Section", 2)

        result = ContextExpander(_FakeStore(chunks)).expand(hit, expand_to_level=2)

        self.assertEqual(result.count("![image]("), 1)
        self.assertIn("Before image", result)
        self.assertIn("After image", result)

    def test_repeated_image_in_text_chunk_is_deduplicated(self) -> None:
        page_id = "page-1"
        packed = pack_image_url(
            "/images/page/file.png",
            "https://example.com/signed.png?X-Amz-Signature=abc",
        )
        text = f"Start\n![image]({packed})\n![image]({packed})\nEnd"
        chunks = [_chunk(0, page_id, text, "# Root", 1)]

        result = ContextExpander(_FakeStore(chunks)).expand(
            _chunk(0, page_id, text, "# Root", 1),
            expand_to_level=1,
        )

        self.assertEqual(result.count("![image]("), 1)
        self.assertIn("Start", result)
        self.assertIn("End", result)

    def test_duplicate_text_chunks_are_deduplicated(self) -> None:
        page_id = "page-1"
        chunks = [
            _chunk(0, page_id, "Same content", "# Root", 1),
            _chunk(1, page_id, "Same content", "# Root", 1),
            _chunk(2, page_id, "Different content", "# Root", 1),
        ]

        result = ContextExpander(_FakeStore(chunks)).expand(
            _chunk(0, page_id, "Same content", "# Root", 1),
            expand_to_level=1,
        )

        self.assertEqual(result.count("Same content"), 1)
        self.assertIn("Different content", result)

    def test_no_siblings_returns_original(self) -> None:
        page_id = "page-1"
        chunks = [
            _chunk(0, page_id, "Original", "# Root", 1),
        ]
        hit = _chunk(99, page_id, "Original", "# Root", 1)
        expander = ContextExpander(_FakeStore(chunks))
        result = expander.expand(hit, expand_to_level=1)
        self.assertIn("Original", result)

    def test_expand_to_level_capped_by_hit_level(self) -> None:
        page_id = "page-1"
        chunks = [
            _chunk(0, page_id, "A", "# Root > ## Section", 2),
            _chunk(1, page_id, "B", "# Root > ## Section > ### Sub", 3),
        ]
        hit = _chunk(1, page_id, "B", "# Root > ## Section > ### Sub", 3)
        expander = ContextExpander(_FakeStore(chunks))
        # expand_to_level=6 is capped to hit header_level=3, so prefix is the full Sub path.
        result = expander.expand(hit, expand_to_level=6)
        self.assertNotIn("A", result)
        self.assertIn("B", result)
        # expand_to_level=2 expands to Section and includes both chunks.
        result = expander.expand(hit, expand_to_level=2)
        self.assertIn("A", result)
        self.assertIn("B", result)


if __name__ == "__main__":
    unittest.main()
