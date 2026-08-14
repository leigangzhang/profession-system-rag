from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from rag_notion_kb.exceptions import StorageError
from rag_notion_kb.models import Chunk, ChunkMetadata, ChunkType, ImageDoc
from rag_notion_kb.storage.milvus_store import MilvusStore
from rag_notion_kb.storage.schema import COLLECTION_SCHEMA


def _sample_chunk(chunk_id: int = 1, page_id: str = "page-1") -> Chunk:
    return Chunk(
        text=f"chunk text {chunk_id}",
        metadata=ChunkMetadata(
            page_id=page_id,
            page_title="Title",
            page_url=f"https://notion.so/{page_id}",
            header_path="# Root",
            header_level=1,
            last_edited_time="2026-08-10T10:00:00Z",
            chunk_index=chunk_id,
            chunk_type=ChunkType.TEXT,
        ),
    )


def _sample_image_doc() -> ImageDoc:
    meta = ChunkMetadata(
        page_id="page-1",
        page_title="Title",
        page_url="https://notion.so/page-1",
        header_path="# Root > ## Section",
        header_level=2,
        last_edited_time="2026-08-10T10:00:00Z",
        chunk_index=10,
        chunk_type=ChunkType.IMAGE,
        image_url="https://example.com/img.png",
    )
    return ImageDoc(
        image_url="https://example.com/img.png",
        alt="diagram",
        context_text="image context",
        metadata=meta,
    )


class TestMilvusStore(unittest.TestCase):
    @patch("rag_notion_kb.storage.milvus_store.MilvusClient")
    def test_init_collection_creates_when_missing(self, mock_client_cls: MagicMock) -> None:
        client = MagicMock()
        client.has_collection.return_value = False
        mock_client_cls.return_value = client

        store = MilvusStore(Path("/tmp/test.db"))
        # init_collection is called automatically in __init__ now;
        # this explicit call is a no-op (has_collection returns True after creation).
        # We test that create_collection was called at least once.
        store.init_collection()

        client.create_collection.assert_called()
        args = client.create_collection.call_args.kwargs
        self.assertEqual(args["collection_name"], "rag_kb_chunks")
        self.assertEqual(args["schema"], COLLECTION_SCHEMA)

    @patch("rag_notion_kb.storage.milvus_store.MilvusClient")
    def test_upsert_page_deletes_then_inserts(self, mock_client_cls: MagicMock) -> None:
        client = MagicMock()
        mock_client_cls.return_value = client

        store = MilvusStore(Path("/tmp/test.db"))
        chunks = [_sample_chunk(0), _sample_chunk(1)]
        images = [_sample_image_doc()]
        embeddings = [[0.1] * 2048, [0.2] * 2048, [0.3] * 2048]
        count = store.upsert_page("page-1", chunks, images, embeddings)

        self.assertEqual(count, 3)
        client.delete.assert_called_once_with("rag_kb_chunks", filter='page_id == "page-1"')
        inserted = client.insert.call_args.kwargs["data"]
        self.assertEqual(len(inserted), 3)
        self.assertEqual(inserted[0]["chunk_type"], "text")
        self.assertEqual(inserted[2]["chunk_type"], "image")
        self.assertEqual(inserted[2]["image_url"], "https://example.com/img.png")

    @patch("rag_notion_kb.storage.milvus_store.MilvusClient")
    def test_upsert_page_restores_previous_rows_on_insert_failure(
        self, mock_client_cls: MagicMock
    ) -> None:
        client = MagicMock()
        old_fields = {
            "id": 99,
            "chunk_text": "old chunk",
            "dense_vector": [0.5] * 2048,
            "sparse_vector": [],
            "page_id": "page-1",
            "page_title": "Old title",
            "page_url": "https://notion.so/page-1",
            "header_path": "# Old",
            "header_level": 1,
            "last_edited_time": "2026-08-10T10:00:00Z",
            "chunk_index": 0,
            "chunk_type": "text",
            "image_url": None,
        }
        client.query.return_value = [old_fields]
        client.insert.side_effect = [RuntimeError("insert failed"), None]
        mock_client_cls.return_value = client

        store = MilvusStore(Path("/tmp/test.db"))
        with self.assertRaises(StorageError):
            store.upsert_page(
                "page-1",
                [_sample_chunk(0)],
                [],
                [[0.1] * 2048],
            )

        self.assertEqual(client.insert.call_count, 2)
        restored = client.insert.call_args_list[1].kwargs["data"]
        self.assertEqual(len(restored), 1)
        self.assertNotIn("id", restored[0])
        self.assertNotIn("sparse_vector", restored[0])
        self.assertEqual(restored[0]["chunk_text"], "old chunk")

    @patch("rag_notion_kb.storage.milvus_store.MilvusClient")
    def test_delete_by_page_id(self, mock_client_cls: MagicMock) -> None:
        client = MagicMock()
        client.delete.return_value = ["id-1", "id-2"]
        mock_client_cls.return_value = client

        store = MilvusStore(Path("/tmp/test.db"))
        removed = store.delete_by_page_id("page-1")
        self.assertEqual(removed, 2)

    @patch("rag_notion_kb.storage.milvus_store.MilvusClient")
    def test_search_dense(self, mock_client_cls: MagicMock) -> None:
        client = MagicMock()
        client.search.return_value = [
            [
                {
                    "id": 1,
                    "distance": 0.9,
                    "entity": {
                        "chunk_text": "hit",
                        "page_id": "page-1",
                        "page_title": "Title",
                        "page_url": "https://notion.so/page-1",
                        "header_path": "# Root",
                        "header_level": 1,
                        "last_edited_time": "2026-08-10T10:00:00Z",
                        "chunk_index": 0,
                        "chunk_type": "text",
                        "image_url": None,
                    },
                }
            ]
        ]
        mock_client_cls.return_value = client

        store = MilvusStore(Path("/tmp/test.db"))
        hits = store.search_dense([0.1] * 2048, limit=5)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].id, 1)
        self.assertEqual(hits[0].score, 0.9)
        self.assertEqual(client.search.call_args.kwargs["anns_field"], "dense_vector")

    @patch("rag_notion_kb.storage.milvus_store.MilvusClient")
    def test_search_sparse(self, mock_client_cls: MagicMock) -> None:
        client = MagicMock()
        client.search.return_value = [[]]
        mock_client_cls.return_value = client

        store = MilvusStore(Path("/tmp/test.db"))
        store.search_sparse("query", limit=10, filters={"page_ids": ["0123456789abcdef0123456789abcdef"]})
        kwargs = client.search.call_args.kwargs
        self.assertEqual(kwargs["anns_field"], "sparse_vector")
        self.assertEqual(kwargs["data"], ["query"])
        self.assertIn("page_id in", kwargs["filter"])

    @patch("rag_notion_kb.storage.milvus_store.MilvusClient")
    def test_get_page_chunks(self, mock_client_cls: MagicMock) -> None:
        client = MagicMock()
        client.query.return_value = [
            {
                "id": 3,
                "chunk_text": "c",
                "page_id": "page-1",
                "page_title": "Title",
                "page_url": "https://notion.so/page-1",
                "header_path": "# Root",
                "header_level": 1,
                "last_edited_time": "2026-08-10T10:00:00Z",
                "chunk_index": 1,
                "chunk_type": "text",
                "image_url": None,
            }
        ]
        mock_client_cls.return_value = client

        store = MilvusStore(Path("/tmp/test.db"))
        hits = store.get_page_chunks("page-1")
        self.assertEqual(len(hits), 1)
        client.query.assert_called_once()

    @patch("rag_notion_kb.storage.milvus_store.MilvusClient")
    def test_stats(self, mock_client_cls: MagicMock) -> None:
        client = MagicMock()
        client.get_collection_stats.return_value = {"row_count": 42}
        mock_client_cls.return_value = client

        store = MilvusStore(Path("/tmp/test.db"))
        self.assertEqual(store.stats(), {"total_chunks": 42})

    @patch("rag_notion_kb.storage.milvus_store.MilvusClient")
    def test_get_page_embedding_stats_reports_actual_dimension(
        self, mock_client_cls: MagicMock
    ) -> None:
        client = MagicMock()
        client.query.return_value = [
            {
                "id": 1,
                "dense_vector": [0.1] * 1024,
                "chunk_index": 0,
            }
        ]
        mock_client_cls.return_value = client

        store = MilvusStore(Path("/tmp/test.db"))
        stats = store.get_page_embedding_stats("page-1")

        self.assertEqual(stats["dim"], 1024)
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["nonzero"], 1)


if __name__ == "__main__":
    unittest.main()
