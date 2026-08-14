from __future__ import annotations

import calendar
import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from rag_notion_kb.models import SyncResult, SyncTask, SyncTaskStatus

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS sync_progress (
    sync_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    total_pages INTEGER NOT NULL,
    completed_pages INTEGER NOT NULL DEFAULT 0,
    current_page_id TEXT,
    current_page_title TEXT,
    results_json TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_sync_progress_finished_at
ON sync_progress(finished_at);
"""


class SyncProgressTracker:
    """Thread-safe sync task tracker with optional SQLite persistence.

    Without *db_path* it keeps the previous in-memory behavior. With
    ``db_path``, the in-memory cache is the hot path and every state change is
    also written through to SQLite; a new instance after a process restart can
    therefore resume reporting previously running or completed tasks.
    """

    def __init__(
        self,
        ttl_seconds: int = 300,
        db_path: Path | str | None = None,
    ) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._tasks: dict[str, SyncTask] = {}
        self._conn: sqlite3.Connection | None = None
        if db_path is not None:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA busy_timeout = 10000")
            self._conn.execute("PRAGMA synchronous = NORMAL")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._ensure_schema()

    # ------------------------------------------------------------------
    # Task lifecycle
    # ------------------------------------------------------------------

    def create_task(self, total_pages: int) -> str:
        """Create a new sync task and return its *sync_id*."""
        sync_id = uuid.uuid4().hex
        now = self._now()
        task = SyncTask(
            sync_id=sync_id,
            status=SyncTaskStatus.RUNNING,
            total_pages=total_pages,
            completed_pages=0,
            started_at=now,
        )
        with self._lock:
            self._cleanup_expired()
            self._tasks[sync_id] = task
            self._persist_locked(task)
        return sync_id

    def update(self, sync_id: str, page_id: str, page_title: str, completed: int) -> None:
        """Update progress for a running task."""
        with self._lock:
            task = self._get_locked(sync_id)
            if task is None:
                return
            task.completed_pages = completed
            task.current_page_id = page_id
            task.current_page_title = page_title
            self._persist_locked(task)

    def complete(self, sync_id: str, results: SyncResult) -> None:
        """Mark a task as completed with final results."""
        with self._lock:
            task = self._get_locked(sync_id)
            if task is None:
                return
            task.status = SyncTaskStatus.COMPLETED
            task.completed_pages = task.total_pages
            task.current_page_id = None
            task.current_page_title = None
            task.results = results
            task.finished_at = self._now()
            self._persist_locked(task)

    def fail(self, sync_id: str, error: str) -> None:
        """Mark a task as failed."""
        with self._lock:
            task = self._get_locked(sync_id)
            if task is None:
                return
            task.status = SyncTaskStatus.FAILED
            task.error_message = error
            task.finished_at = self._now()
            self._persist_locked(task)

    def get(self, sync_id: str) -> SyncTask | None:
        """Return the current state of a sync task, or None."""
        with self._lock:
            return self._get_locked(sync_id)

    def close(self) -> None:
        """Close the optional persistent SQLite connection."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------------
    # Callback factory
    # ------------------------------------------------------------------

    def make_callback(self, sync_id: str) -> Callable[[str, str, int, int], None]:
        """Create a ``progress_callback`` bound to a specific *sync_id*.

        The returned callable matches the signature:
            callback(current_page_id, current_page_title, completed, total)
        """
        def _callback(
            page_id: str,
            page_title: str,
            completed: int,
            total: int,
        ) -> None:
            self.update(sync_id, page_id, page_title, completed)
        return _callback

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        assert self._conn is not None
        try:
            self._conn.executescript(_DDL)
            self._conn.commit()
        except sqlite3.Error:
            logger.exception("Failed to initialize sync progress schema")

    def _get_locked(self, sync_id: str) -> SyncTask | None:
        """Return a task from cache or persistence. Caller must hold the lock."""
        task = self._tasks.get(sync_id)
        if task is not None:
            return task
        if self._conn is None:
            return None
        try:
            row = self._conn.execute(
                "SELECT * FROM sync_progress WHERE sync_id = ?", (sync_id,)
            ).fetchone()
        except sqlite3.Error:
            logger.exception("Failed to read sync progress task %s", sync_id)
            return None
        if row is None:
            return None
        task = self._row_to_task(row)
        self._tasks[sync_id] = task
        return task

    def _persist_locked(self, task: SyncTask) -> None:
        """Write one task through to SQLite. Caller must hold the lock."""
        if self._conn is None:
            return
        try:
            self._conn.execute(
                """
                INSERT INTO sync_progress (
                    sync_id, status, total_pages, completed_pages,
                    current_page_id, current_page_title, results_json,
                    error_message, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sync_id) DO UPDATE SET
                    status=excluded.status,
                    total_pages=excluded.total_pages,
                    completed_pages=excluded.completed_pages,
                    current_page_id=excluded.current_page_id,
                    current_page_title=excluded.current_page_title,
                    results_json=excluded.results_json,
                    error_message=excluded.error_message,
                    started_at=excluded.started_at,
                    finished_at=excluded.finished_at
                """,
                (
                    task.sync_id,
                    task.status.value,
                    task.total_pages,
                    task.completed_pages,
                    task.current_page_id,
                    task.current_page_title,
                    task.results.model_dump_json() if task.results is not None else None,
                    task.error_message,
                    task.started_at,
                    task.finished_at,
                ),
            )
            self._conn.commit()
        except sqlite3.Error:
            logger.exception("Failed to persist sync progress task %s", task.sync_id)

    def _cleanup_expired(self) -> None:
        """Remove finished tasks whose TTL has elapsed. Caller must hold the lock."""
        cutoff = time.time() - self._ttl
        expired = [
            sync_id
            for sync_id, task in self._tasks.items()
            if task.finished_at is not None
            and self._parse_time(task.finished_at) < cutoff
        ]
        for sync_id in expired:
            del self._tasks[sync_id]

        if self._conn is None:
            return
        cutoff_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff))
        try:
            self._conn.execute(
                "DELETE FROM sync_progress "
                "WHERE finished_at IS NOT NULL AND finished_at < ?",
                (cutoff_iso,),
            )
            self._conn.commit()
        except sqlite3.Error:
            logger.exception("Failed to clean up expired sync progress tasks")

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> SyncTask:
        results = None
        if row["results_json"]:
            results = SyncResult.model_validate_json(row["results_json"])
        return SyncTask(
            sync_id=row["sync_id"],
            status=SyncTaskStatus(row["status"]),
            total_pages=row["total_pages"],
            completed_pages=row["completed_pages"],
            current_page_id=row["current_page_id"],
            current_page_title=row["current_page_title"],
            results=results,
            error_message=row["error_message"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    @staticmethod
    def _parse_time(ts: str) -> float:
        """Parse ISO 8601-ish timestamp to Unix epoch float."""
        try:
            return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))
        except ValueError:
            return 0.0
