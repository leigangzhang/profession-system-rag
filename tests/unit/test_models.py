from __future__ import annotations

import unittest

from rag_notion_kb.models import (
    Chunk,
    ChunkMetadata,
    ChunkType,
    ImageDoc,
    PageSyncState,
    SearchResult,
    SyncResult,
)


class TestModels(unittest.TestCase):
    def test_chunk_metadata_validation(self) -> None:
        meta = ChunkMetadata(
            page_id="page-1",
            page_title="Test",
            page_url="https://notion.so/page-1",
            header_path="# Test",
            header_level=2,
            last_edited_time="2026-08-10T10:00:00Z",
            chunk_index=0,
            chunk_type=ChunkType.TEXT,
        )
        self.assertEqual(meta.header_level, 2)
        self.assertIsNone(meta.image_url)

    def test_invalid_header_level(self) -> None:
        with self.assertRaises(ValueError):
            ChunkMetadata(
                page_id="page-1",
                page_title="Test",
                page_url="https://notion.so/page-1",
                header_path="# Test",
                header_level=7,
                last_edited_time="2026-08-10T10:00:00Z",
                chunk_index=0,
                chunk_type=ChunkType.TEXT,
            )

    def test_sync_result_defaults(self) -> None:
        result = SyncResult()
        self.assertEqual(result.added, 0)
        self.assertEqual(result.failed, 0)

    def test_image_doc(self) -> None:
        meta = ChunkMetadata(
            page_id="page-1",
            page_title="Test",
            page_url="https://notion.so/page-1",
            header_path="# Test > ## Img",
            header_level=2,
            last_edited_time="2026-08-10T10:00:00Z",
            chunk_index=0,
            chunk_type=ChunkType.IMAGE,
            image_url="https://example.com/img.png",
        )
        doc = ImageDoc(
            image_url="https://example.com/img.png",
            alt="diagram",
            context_text="context",
            metadata=meta,
        )
        self.assertEqual(doc.metadata.image_url, meta.image_url)

    def test_search_result(self) -> None:
        meta = ChunkMetadata(
            page_id="page-1",
            page_title="Test",
            page_url="https://notion.so/page-1",
            header_path="# Test",
            header_level=1,
            last_edited_time="2026-08-10T10:00:00Z",
            chunk_index=0,
            chunk_type=ChunkType.TEXT,
        )
        result = SearchResult(text="hello", score=0.9, source=meta)
        self.assertEqual(result.score, 0.9)

    def test_page_sync_state(self) -> None:
        state = PageSyncState(
            page_id="page-1",
            page_title="Test",
            page_url="https://notion.so/page-1",
            last_edited_time="2026-08-10T10:00:00Z",
            parent_id=None,
            chunk_count=10,
            image_count=2,
            status="synced",
            error_message=None,
            last_synced_time="2026-08-10T10:00:00Z",
        )
        self.assertEqual(state.status, "synced")

    def test_chunk_type_enum(self) -> None:
        chunk = Chunk(
            text="body",
            metadata=ChunkMetadata(
                page_id="p",
                page_title="T",
                page_url="u",
                header_path="# H",
                header_level=1,
                last_edited_time="2026-08-10T10:00:00Z",
                chunk_index=0,
                chunk_type=ChunkType.CODE,
            ),
        )
        self.assertEqual(chunk.metadata.chunk_type, "code")


if __name__ == "__main__":
    unittest.main()
