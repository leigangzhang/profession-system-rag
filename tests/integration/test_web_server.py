"""Integration tests for the web server API."""
from __future__ import annotations

import tempfile
import unittest
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from rag_notion_kb.config import Settings
from rag_notion_kb.models import (
    ChunkMetadata,
    ChunkType,
    PageSyncState,
    SearchHit,
)
from rag_notion_kb.web.web_server import create_app

PAGE_ID = "0123456789abcdef0123456789abcdef"
MISSING_PAGE_ID = "fedcba9876543210fedcba9876543210"


class TestWebServerAPI(unittest.TestCase):
    """Integration tests for the web server API endpoints."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.sync_service = MagicMock()
        self.search_service = MagicMock()
        self.store = MagicMock()
        self.state_store = MagicMock()
        self.config = MagicMock()
        self.config.storage.data_dir = self.tmpdir.name

        self.app = create_app(
            sync_service=self.sync_service,
            search_service=self.search_service,
            store=self.store,
            state_store=self.state_store,
            config=self.config,
        )
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_health(self) -> None:
        """GET /health reports liveness without touching external services."""
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok", "worker_running": False})

    def test_list_pages_empty(self) -> None:
        """GET /api/pages returns empty list when no pages synced."""
        self.state_store.list_all.return_value = []
        resp = self.client.get("/api/pages")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_list_pages_with_data(self) -> None:
        """GET /api/pages returns page summaries."""
        state = PageSyncState(
            page_id=PAGE_ID,
            page_title="Test Page",
            page_url="https://notion.so/page",
            last_edited_time="2026-08-10T10:00:00Z",
            chunk_count=5,
            image_count=0,
            status="synced",
            last_synced_time="2026-08-10T12:00:00Z",
            vector_status="indexing",
            vector_progress=62,
            vector_stage="embedding",
        )
        self.state_store.list_all.return_value = [state]
        self.store.get_page_embedding_stats.return_value = {
            "total": 5, "nonzero": 5, "zero": 0, "dim": 2048, "sample_first5": []
        }

        resp = self.client.get("/api/pages")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["page_id"], PAGE_ID)
        self.assertEqual(data[0]["chunk_count"], 5)
        self.assertEqual(data[0]["zero_vector_chunks"], 0)
        self.assertEqual(data[0]["vector_status"], "indexing")
        self.assertEqual(data[0]["vector_progress"], 62)
        self.assertEqual(data[0]["vector_stage"], "embedding")

    def test_list_pages_embedding_stats_failure(self) -> None:
        """GET /api/pages gracefully handles embedding stats failure."""
        state = PageSyncState(
            page_id=PAGE_ID,
            page_title="Test Page",
            page_url="https://notion.so/page",
            last_edited_time="2026-08-10T10:00:00Z",
            chunk_count=5,
            image_count=2,
            status="synced",
            last_synced_time="2026-08-10T12:00:00Z",
        )
        self.state_store.list_all.return_value = [state]
        self.store.get_page_embedding_stats.side_effect = RuntimeError("Milvus down")

        resp = self.client.get("/api/pages")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["zero_vector_chunks"], 7)

    def test_page_detail_invalid_id(self) -> None:
        """GET /api/pages/{id} rejects malformed page IDs."""
        resp = self.client.get("/api/pages/not-a-page-id")
        self.assertEqual(resp.status_code, 400)

    def test_page_detail_not_found(self) -> None:
        """GET /api/pages/{id} returns 404 for unknown page."""
        self.state_store.get.return_value = None
        resp = self.client.get(f"/api/pages/{MISSING_PAGE_ID}")
        self.assertEqual(resp.status_code, 404)

    def test_page_detail_with_chunks(self) -> None:
        """GET /api/pages/{id} returns full detail."""
        state = PageSyncState(
            page_id=PAGE_ID,
            page_title="Test Page",
            page_url="https://notion.so/page",
            last_edited_time="2026-08-10T10:00:00Z",
            chunk_count=2,
            image_count=0,
            status="synced",
            last_synced_time="2026-08-10T12:00:00Z",
            raw_markdown="# Hello\n\nWorld",
        )
        self.state_store.get.return_value = state
        self.store.get_page_embedding_stats.return_value = {
            "total": 2, "nonzero": 2, "zero": 0, "dim": 2048, "sample_first5": [0.1, 0.2]
        }

        hit1 = SearchHit(
            id=1, chunk_text="Hello world chunk", score=0.95,
            metadata=ChunkMetadata(
                page_id=PAGE_ID, page_title="Test Page",
                page_url="https://notion.so/page", header_path="# Hello",
                header_level=1, last_edited_time="2026-08-10T10:00:00Z",
                chunk_index=0, chunk_type=ChunkType.TEXT,
            ),
        )
        hit2 = SearchHit(
            id=2,
            chunk_text="[Image: image-20210717182324592.png?X-Amz-Algorithm=AWS4-HMAC-SHA256] Context",
            score=0.90,
            metadata=ChunkMetadata(
                page_id=PAGE_ID, page_title="Test Page",
                page_url="https://notion.so/page", header_path="## Section",
                header_level=2, last_edited_time="2026-08-10T10:00:00Z",
                chunk_index=1, chunk_type=ChunkType.IMAGE,
                image_url="/images/test/page/image.png",
            ),
        )
        self.store.get_page_chunks_with_vectors.return_value = [(hit1, True), (hit2, True)]

        resp = self.client.get(f"/api/pages/{PAGE_ID}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["page_id"], PAGE_ID)
        self.assertEqual(data["raw_markdown"], "# Hello\n\nWorld")
        self.assertEqual(len(data["chunks"]), 2)
        self.assertEqual(data["chunks"][0]["chunk_index"], 0)
        self.assertEqual(data["chunks"][0]["vector_nonzero"], True)
        self.assertEqual(data["chunks"][1]["chunk_type"], "image")
        self.assertEqual(data["chunks"][1]["image_url"], "/images/test/page/image.png")
        self.assertEqual(data["embedding_stats"]["nonzero"], 2)

    def test_page_detail_chunks_failure(self) -> None:
        """GET /api/pages/{id} gracefully handles chunk query failure."""
        state = PageSyncState(
            page_id=PAGE_ID, page_title="Test Page",
            page_url="https://notion.so/page",
            last_edited_time="2026-08-10T10:00:00Z",
            chunk_count=0, image_count=0, status="synced",
            last_synced_time="2026-08-10T12:00:00Z", raw_markdown="content",
        )
        self.state_store.get.return_value = state
        self.store.get_page_embedding_stats.return_value = {
            "total": 0, "nonzero": 0, "zero": 0, "dim": 2048, "sample_first5": []
        }
        self.store.get_page_chunks_with_vectors.side_effect = RuntimeError("Milvus down")

        resp = self.client.get(f"/api/pages/{PAGE_ID}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["chunks"], [])

    def test_sync_single_page(self) -> None:
        """POST /api/pages/{id}/sync triggers re-sync."""
        from rag_notion_kb.models import SyncResult
        self.sync_service.sync.return_value = (SyncResult(updated=1), None)

        resp = self.client.post(f"/api/pages/{PAGE_ID}/sync")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["updated"], 1)
        self.sync_service.sync.assert_called_once_with(root_page_ids=[PAGE_ID])

    def test_sync_single_page_error(self) -> None:
        """POST /api/pages/{id}/sync returns 500 on error."""
        self.sync_service.sync.side_effect = RuntimeError("Sync failed")
        resp = self.client.post(f"/api/pages/{PAGE_ID}/sync")
        self.assertEqual(resp.status_code, 500)

    def test_static_index_served(self) -> None:
        """GET / returns the index.html."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("RAG Notion KB", resp.text)

    def test_spa_fallback_serves_non_api_routes(self) -> None:
        """Unknown non-API paths return the Vue entry point."""
        resp = self.client.get("/search-debug")
        self.assertEqual(resp.status_code, 200)
        self.assertIn('id="app"', resp.text)

    def test_spa_fallback_does_not_mask_api_routes(self) -> None:
        """Unknown API paths remain 404 responses."""
        resp = self.client.get("/api/not-a-real-route")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
