from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from rag_notion_kb.exceptions import StorageError
from rag_notion_kb.models import PageSyncState
from rag_notion_kb.storage.sqlite_utils import QueryResult

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS page_sync_state (
    page_id TEXT PRIMARY KEY,
    page_title TEXT NOT NULL,
    page_url TEXT NOT NULL,
    last_edited_time TEXT NOT NULL,
    parent_id TEXT,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    image_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK(status IN ('fetched','synced','failed','skipped')),
    error_message TEXT,
    last_synced_time TEXT NOT NULL,
    raw_markdown TEXT DEFAULT '',
    vector_enabled INTEGER NOT NULL DEFAULT 0,
    vector_status TEXT NOT NULL DEFAULT 'pending' CHECK(vector_status IN ('pending','indexing','indexed','failed')),
    vector_error_message TEXT,
    vector_progress INTEGER NOT NULL DEFAULT 0,
    vector_stage TEXT DEFAULT '',
    last_vectorized_time TEXT,
    content_hash TEXT DEFAULT '',
    chunking_hash TEXT DEFAULT '',
    embedding_hash TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_parent_id ON page_sync_state(parent_id);
CREATE INDEX IF NOT EXISTS idx_last_edited_time ON page_sync_state(last_edited_time);
"""


class SyncStateStore:
    """Persistent SQLite store for page-level synchronization state."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        # Enable WAL mode for better concurrent read performance.
        self._conn.execute("PRAGMA busy_timeout = 10000")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Thread-safe helpers
    # ------------------------------------------------------------------

    def _execute(self, sql: str, params: tuple = (), *, commit: bool = False):
        """Execute and fully fetch SQL under the instance lock for thread safety."""
        with self._lock:
            cursor = self._conn.execute(sql, params)
            rows = cursor.fetchall()
            result = QueryResult(
                rows=rows,
                rowcount=cursor.rowcount,
                lastrowid=cursor.lastrowid,
            )
            if commit:
                self._conn.commit()
            return result

    def _executescript(self, sql: str) -> None:
        """Execute a multi-statement script under the instance lock."""
        with self._lock:
            self._conn.executescript(sql)
            self._conn.commit()

    def close(self) -> None:
        """Close the underlying database connection."""
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        try:
            self._executescript(_DDL)
            self._migrate_schema()
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to create sync state schema: {exc}") from exc

    def _migrate_schema(self) -> None:
        """Add columns that may be missing from older schema versions."""
        existing = {
            row[1]
            for row in self._execute("PRAGMA table_info(page_sync_state)").fetchall()
        }
        migrations = [
            ("vector_enabled", "INTEGER NOT NULL DEFAULT 0"),
            ("vector_status", "TEXT NOT NULL DEFAULT 'pending'"),
            ("vector_error_message", "TEXT"),
            ("vector_progress", "INTEGER NOT NULL DEFAULT 0"),
            ("vector_stage", "TEXT DEFAULT ''"),
            ("last_vectorized_time", "TEXT"),
            ("content_hash", "TEXT DEFAULT ''"),
            ("chunking_hash", "TEXT DEFAULT ''"),
            ("embedding_hash", "TEXT DEFAULT ''"),
        ]
        for col_name, col_def in migrations:
            if col_name not in existing:
                try:
                    self._execute(
                        f"ALTER TABLE page_sync_state ADD COLUMN {col_name} {col_def}"
                    )
                    logger.info("Migrated schema: added column %s", col_name)
                except sqlite3.Error as exc:
                    raise StorageError(
                        f"Schema migration failed for {col_name}: {exc}"
                    ) from exc
        # Migrate existing synced pages: vector_status defaults to 'indexed' (already in Milvus)
        self._execute(
            "UPDATE page_sync_state SET vector_status = 'indexed' "
            "WHERE status = 'synced' AND vector_status = 'pending'",
            commit=True,
        )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def get(self, page_id: str) -> PageSyncState | None:
        """Return the sync state for a single page, or None if absent."""
        try:
            row = self._execute(
                "SELECT * FROM page_sync_state WHERE page_id = ?", (page_id,)
            ).fetchone()
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to get state for {page_id}: {exc}") from exc

        if row is None:
            return None
        return self._row_to_state(row)

    def get_many(self, page_ids: list[str]) -> dict[str, PageSyncState]:
        """Return states for multiple pages in one SQLite query."""
        unique_ids = list(dict.fromkeys(page_ids))
        if not unique_ids:
            return {}
        placeholders = ", ".join("?" for _ in unique_ids)
        try:
            rows = self._execute(
                f"SELECT * FROM page_sync_state WHERE page_id IN ({placeholders})",
                tuple(unique_ids),
            ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to get sync states for {len(unique_ids)} pages: {exc}") from exc
        return {row["page_id"]: self._row_to_state(row) for row in rows}

    def upsert(self, state: PageSyncState) -> None:
        """Insert or replace the sync state for a page."""
        try:
            self._execute(
                """
                INSERT INTO page_sync_state (
                    page_id, page_title, page_url, last_edited_time, parent_id,
                    chunk_count, image_count, status, error_message, last_synced_time,
                    raw_markdown,
                    vector_enabled, vector_status, vector_error_message, vector_progress,
                    vector_stage, content_hash, chunking_hash, embedding_hash
                ) VALUES (
                    :page_id, :page_title, :page_url, :last_edited_time, :parent_id,
                    :chunk_count, :image_count, :status, :error_message, :last_synced_time,
                    :raw_markdown,
                    :vector_enabled, :vector_status, :vector_error_message, :vector_progress,
                    :vector_stage, :content_hash, :chunking_hash, :embedding_hash
                )
                ON CONFLICT(page_id) DO UPDATE SET
                    page_title=excluded.page_title,
                    page_url=excluded.page_url,
                    last_edited_time=excluded.last_edited_time,
                    parent_id=excluded.parent_id,
                    chunk_count=excluded.chunk_count,
                    image_count=excluded.image_count,
                    status=excluded.status,
                    error_message=excluded.error_message,
                    last_synced_time=excluded.last_synced_time,
                    raw_markdown=excluded.raw_markdown,
                    vector_enabled=excluded.vector_enabled,
                    vector_status=excluded.vector_status,
                    vector_error_message=excluded.vector_error_message,
                    vector_progress=excluded.vector_progress,
                    vector_stage=excluded.vector_stage,
                    content_hash=excluded.content_hash,
                    chunking_hash=excluded.chunking_hash,
                    embedding_hash=excluded.embedding_hash
                """,
                state.model_dump(),
                commit=True,
            )
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to upsert state for {state.page_id}: {exc}") from exc

    def list_all(self) -> list[PageSyncState]:
        """Return all tracked page sync states."""
        try:
            rows = self._execute("SELECT * FROM page_sync_state").fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to list sync states: {exc}") from exc
        return [self._row_to_state(row) for row in rows]

    def update_raw_markdown(self, page_id: str, raw_markdown: str) -> None:
        """Update only the raw Markdown content for a page."""
        try:
            self._execute(
                "UPDATE page_sync_state SET raw_markdown = ? WHERE page_id = ?",
                (raw_markdown, page_id),
                commit=True,
            )
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to update raw markdown for {page_id}: {exc}") from exc

    def delete(self, page_id: str) -> None:
        """Remove a page's sync state from the store."""
        try:
            self._execute(
                "DELETE FROM page_sync_state WHERE page_id = ?", (page_id,),
                commit=True,
            )
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to delete state for {page_id}: {exc}") from exc

    def get_last_sync_time(self) -> str | None:
        """Return the most recent last_synced_time across all pages, or None."""
        try:
            row = self._execute(
                "SELECT MAX(last_synced_time) FROM page_sync_state"
            ).fetchone()
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to read last sync time: {exc}") from exc
        return row[0] if row and row[0] else None

    def get_pending_vectorize(self) -> list[PageSyncState]:
        """Return pages where vector_enabled=1 and vector_status='pending'."""
        try:
            rows = self._execute(
                "SELECT * FROM page_sync_state "
                "WHERE vector_enabled = 1 AND vector_status = 'pending'"
            ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to list pending vectorize pages: {exc}") from exc
        return [self._row_to_state(row) for row in rows]

    def update_vector_status(
        self,
        page_id: str,
        vector_status: str,
        vector_error_message: str | None = None,
        vector_progress: int = 0,
        vector_stage: str | None = None,
        chunk_count: int | None = None,
        image_count: int | None = None,
    ) -> None:
        """Update vectorization state for a page without touching other columns."""
        sets = ["vector_status = ?", "vector_error_message = ?", "vector_progress = ?"]
        params: list = [vector_status, vector_error_message, vector_progress]
        if vector_stage is not None:
            sets.append("vector_stage = ?")
            params.append(vector_stage)
        if chunk_count is not None:
            sets.append("chunk_count = ?")
            params.append(chunk_count)
        if image_count is not None:
            sets.append("image_count = ?")
            params.append(image_count)
        if vector_status == "indexed":
            sets.append("last_vectorized_time = ?")
            params.append(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        params.append(page_id)
        try:
            self._execute(
                f"UPDATE page_sync_state SET {', '.join(sets)} WHERE page_id = ?",
                tuple(params),
                commit=True,
            )
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to update vector status for {page_id}: {exc}") from exc

    def mark_vectorized(
        self,
        page_id: str,
        chunk_count: int,
        image_count: int,
        content_hash: str,
        chunking_hash: str,
        embedding_hash: str,
    ) -> None:
        """Atomically persist indexed state and all pipeline hashes."""
        try:
            with self._lock:
                self._conn.execute(
                    """
                    UPDATE page_sync_state SET
                        vector_status = 'indexed',
                        vector_error_message = NULL,
                        vector_progress = 100,
                        vector_stage = 'done',
                        chunk_count = ?,
                        image_count = ?,
                        content_hash = ?,
                        chunking_hash = ?,
                        embedding_hash = ?,
                        last_vectorized_time = ?
                    WHERE page_id = ?
                    """,
                    (
                        chunk_count,
                        image_count,
                        content_hash,
                        chunking_hash,
                        embedding_hash,
                        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        page_id,
                    ),
                )
                self._conn.commit()
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to mark vectorized state for {page_id}: {exc}") from exc

    def claim_vectorize(self, page_id: str) -> bool:
        """Atomically claim a pending page for vectorization.

        Returns True when the page was pending and has been marked indexing.
        This prevents concurrent workers from processing the same page.
        """
        try:
            with self._lock:
                cursor = self._conn.execute(
                    "UPDATE page_sync_state SET vector_status = 'indexing' "
                    ", vector_stage = 'preparing' "
                    "WHERE page_id = ? AND vector_enabled = 1 AND vector_status = 'pending'",
                    (page_id,),
                )
                self._conn.commit()
                return cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to claim vectorize for {page_id}: {exc}") from exc

    def reset_stale_vectorize(self) -> int:
        """Reset pages left in indexing by a crashed worker back to pending."""
        try:
            with self._lock:
                cursor = self._conn.execute(
                    "UPDATE page_sync_state SET vector_status = 'pending' "
                    ", vector_stage = 'pending' "
                    "WHERE vector_status = 'indexing'"
                )
                self._conn.commit()
                return cursor.rowcount
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to reset stale vectorize pages: {exc}") from exc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_state(self, row: sqlite3.Row) -> PageSyncState:
        return PageSyncState.model_validate(dict(row))
