from __future__ import annotations

import time

import pytest

from rag_notion_kb.models import SyncResult, SyncTaskStatus
from rag_notion_kb.services.sync_progress import SyncProgressTracker


class TestSyncProgressTracker:
    """Unit tests for SyncProgressTracker lifecycle."""

    @pytest.fixture
    def tracker(self) -> SyncProgressTracker:
        return SyncProgressTracker(ttl_seconds=1)  # Short TTL for testing cleanup

    def test_create_task(self, tracker: SyncProgressTracker) -> None:
        sync_id = tracker.create_task(total_pages=5)
        task = tracker.get(sync_id)
        assert task is not None
        assert task.status == SyncTaskStatus.RUNNING
        assert task.total_pages == 5
        assert task.completed_pages == 0
        assert task.current_page_id is None
        assert task.finished_at is None

    def test_update_progress(self, tracker: SyncProgressTracker) -> None:
        sync_id = tracker.create_task(total_pages=3)
        tracker.update(sync_id, "page-1", "Page One", 1)
        task = tracker.get(sync_id)
        assert task.completed_pages == 1
        assert task.current_page_id == "page-1"
        assert task.current_page_title == "Page One"

    def test_complete_task(self, tracker: SyncProgressTracker) -> None:
        sync_id = tracker.create_task(total_pages=2)
        results = SyncResult(added=1, updated=1, skipped=0, failed=0)
        tracker.complete(sync_id, results)
        task = tracker.get(sync_id)
        assert task.status == SyncTaskStatus.COMPLETED
        assert task.completed_pages == 2
        assert task.results is not None
        assert task.results.added == 1
        assert task.finished_at is not None

    def test_fail_task(self, tracker: SyncProgressTracker) -> None:
        sync_id = tracker.create_task(total_pages=1)
        tracker.fail(sync_id, "Notion API error")
        task = tracker.get(sync_id)
        assert task.status == SyncTaskStatus.FAILED
        assert task.error_message == "Notion API error"
        assert task.finished_at is not None

    def test_get_nonexistent(self, tracker: SyncProgressTracker) -> None:
        assert tracker.get("nonexistent-id") is None

    def test_callback_factory(self, tracker: SyncProgressTracker) -> None:
        sync_id = tracker.create_task(total_pages=3)
        cb = tracker.make_callback(sync_id)
        cb("page-a", "Page A", 1, 3)
        task = tracker.get(sync_id)
        assert task.completed_pages == 1
        assert task.current_page_id == "page-a"
        assert task.current_page_title == "Page A"

    def test_cleanup_expired(self, tracker: SyncProgressTracker) -> None:
        # Create a task, complete it, then create another task to trigger cleanup
        sid = tracker.create_task(total_pages=1)
        tracker.complete(sid, SyncResult())
        # Wait for TTL to expire (1 second in fixture)
        time.sleep(1.5)
        # Creating a new task triggers cleanup of expired tasks
        tracker.create_task(total_pages=1)
        # Old completed task should be cleaned up
        assert tracker.get(sid) is None

    def test_update_nonexistent_no_error(self, tracker: SyncProgressTracker) -> None:
        tracker.update("no-such-id", "p1", "T1", 1)  # Should not raise

    def test_complete_nonexistent_no_error(self, tracker: SyncProgressTracker) -> None:
        tracker.complete("no-such-id", SyncResult())  # Should not raise

    def test_multiple_tasks(self, tracker: SyncProgressTracker) -> None:
        s1 = tracker.create_task(total_pages=2)
        s2 = tracker.create_task(total_pages=3)
        assert tracker.get(s1) is not None
        assert tracker.get(s2) is not None
        assert s1 != s2

    def test_full_lifecycle(self, tracker: SyncProgressTracker) -> None:
        sid = tracker.create_task(total_pages=3)
        tracker.update(sid, "p1", "Page 1", 1)
        tracker.update(sid, "p2", "Page 2", 2)
        tracker.update(sid, "p3", "Page 3", 3)
        tracker.complete(sid, SyncResult(added=2, updated=1, skipped=0, failed=0))
        task = tracker.get(sid)
        assert task.status == SyncTaskStatus.COMPLETED
        assert task.completed_pages == 3
        assert task.current_page_id is None
        assert task.results.added == 2
        assert task.results.updated == 1
        assert task.error_message is None

    def test_task_survives_tracker_restart(self, tmp_path) -> None:
        db_path = tmp_path / "sync_progress.db"
        first = SyncProgressTracker(db_path=db_path)
        sid = first.create_task(total_pages=2)
        first.update(sid, "p1", "Page 1", 1)
        first.complete(sid, SyncResult(added=2))
        first.close()

        restarted = SyncProgressTracker(db_path=db_path)
        task = restarted.get(sid)
        restarted.close()

        assert task is not None
        assert task.status == SyncTaskStatus.COMPLETED
        assert task.completed_pages == 2
        assert task.current_page_id is None
        assert task.results is not None
        assert task.results.added == 2
