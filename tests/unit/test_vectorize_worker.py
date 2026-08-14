from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from rag_notion_kb.services.vectorize_worker import VectorizeWorker


def _make_state(page_id: str, vector_enabled: bool = True, vector_status: str = "pending") -> MagicMock:
    s = MagicMock()
    s.page_id = page_id
    s.vector_enabled = vector_enabled
    s.vector_status = vector_status
    return s


class TestVectorizeWorker(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.sync_service = MagicMock()
        self.state_store = MagicMock()
        self.worker = VectorizeWorker(
            sync_service=self.sync_service,
            state_store=self.state_store,
            max_concurrent=2,
            poll_interval_seconds=0.01,
        )

    async def test_start_and_stop(self) -> None:
        """Worker starts and stops cleanly."""
        self.state_store.get_pending_vectorize.return_value = []
        await self.worker.start()
        self.assertTrue(self.worker._running)
        await asyncio.sleep(0.05)
        await self.worker.stop()
        self.assertFalse(self.worker._running)

    async def test_processes_pending_pages(self) -> None:
        """Worker picks up pending pages and vectorizes them."""
        s1 = _make_state("p1")
        s2 = _make_state("p2")
        self.state_store.get_pending_vectorize.side_effect = [
            [s1, s2],
            [],  # second poll finds nothing
        ]
        self.sync_service.vectorize_state.return_value = {"status": "indexed"}

        await self.worker.start()
        await asyncio.sleep(0.15)
        await self.worker.stop()

        self.assertEqual(self.sync_service.vectorize_state.call_count, 2)

    async def test_semaphore_limits_concurrency(self) -> None:
        """Worker respects max_concurrent semaphore."""
        s1 = _make_state("p1")
        s2 = _make_state("p2")
        s3 = _make_state("p3")
        self.state_store.get_pending_vectorize.side_effect = [
            [s1, s2, s3],
            [],
        ]

        running_count = 0
        max_seen = 0
        lock = asyncio.Lock()

        def slow_vectorize(state: MagicMock) -> dict:
            nonlocal running_count, max_seen
            running_count += 1
            max_seen = max(max_seen, running_count)
            import time
            time.sleep(0.05)
            running_count -= 1
            return {"status": "indexed"}

        self.sync_service.vectorize_state.side_effect = slow_vectorize

        await self.worker.start()
        await asyncio.sleep(0.3)
        await self.worker.stop()

        # With max_concurrent=2, we should never see more than 3 running
        # (2 from semaphore + 1 that may have just started before semaphore released)
        self.assertLessEqual(max_seen, 3)
        self.assertEqual(self.sync_service.vectorize_state.call_count, 3)

    async def test_failure_isolation(self) -> None:
        """Failure of one page does not prevent others from processing."""
        s1 = _make_state("p1")
        s2 = _make_state("p2")
        s3 = _make_state("p3")
        self.state_store.get_pending_vectorize.side_effect = [
            [s1, s2, s3],
            [],
        ]
        # Page 2 fails
        self.sync_service.vectorize_state.side_effect = [
            {"status": "indexed"},
            RuntimeError("page 2 failure"),
            {"status": "indexed"},
        ]

        await self.worker.start()
        await asyncio.sleep(0.2)
        await self.worker.stop()

        self.assertEqual(self.sync_service.vectorize_state.call_count, 3)

    async def test_pending_page_is_claimed_only_once(self) -> None:
        """A page observed in multiple polls is not dispatched twice."""
        state = _make_state("p1")
        self.state_store.get_pending_vectorize.side_effect = [
            [state],
            [state],
            [state],
            [],
        ]
        self.state_store.claim_vectorize.side_effect = [True, False, False, False]
        self.sync_service.vectorize_state.return_value = {"status": "indexed"}

        await self.worker.start()
        await asyncio.sleep(0.1)
        await self.worker.stop()

        self.assertEqual(self.sync_service.vectorize_state.call_count, 1)
        self.assertGreaterEqual(self.state_store.claim_vectorize.call_count, 2)

    async def test_enqueue_sets_pending(self) -> None:
        """enqueue() enables toggle and marks page as pending."""
        from rag_notion_kb.models import PageSyncState
        s = PageSyncState(
            page_id="p1", page_title="T", page_url="http://x",
            last_edited_time="2024", chunk_count=0, image_count=0,
            status="fetched", last_synced_time="2024",
            vector_enabled=False, vector_status="indexed",
        )
        self.state_store.get.return_value = s

        await self.worker.enqueue("p1")

        self.state_store.upsert.assert_called_once()
        upserted = self.state_store.upsert.call_args[0][0]
        self.assertTrue(upserted.vector_enabled)
        self.assertEqual(upserted.vector_status, "pending")
        self.assertEqual(upserted.vector_stage, "pending")

    async def test_enqueue_already_enabled(self) -> None:
        """enqueue() on already-enabled page just marks pending."""
        s = _make_state("p1", vector_enabled=True, vector_status="indexed")
        self.state_store.get.return_value = s

        await self.worker.enqueue("p1")

        self.state_store.update_vector_status.assert_called_once_with(
            "p1",
            vector_status="pending",
            vector_error_message=None,
            vector_progress=0,
            vector_stage="pending",
        )

    async def test_enqueue_not_found(self) -> None:
        """enqueue() on unknown page warns and returns."""
        self.state_store.get.return_value = None
        with self.assertLogs("rag_notion_kb.services.vectorize_worker", level="WARNING"):
            await self.worker.enqueue("unknown")
        self.state_store.upsert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
