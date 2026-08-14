from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

from rag_notion_kb.exceptions import StorageError
from rag_notion_kb.models import NotionRoot
from rag_notion_kb.storage.sqlite_utils import QueryResult

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS notion_roots (
    root_id TEXT PRIMARY KEY,
    page_title TEXT NOT NULL,
    page_url TEXT NOT NULL,
    added_time TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);
"""


class NotionRootStore:
    """Persistent SQLite store for Notion root page configuration."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._conn.execute("PRAGMA busy_timeout = 10000")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()

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

    def _ensure_schema(self) -> None:
        try:
            self._executescript(_DDL)
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to create notion_roots schema: {exc}") from exc

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, root: NotionRoot) -> None:
        """Insert a new Notion root configuration."""
        try:
            self._execute(
                "INSERT INTO notion_roots (root_id, page_title, page_url, added_time, is_active) "
                "VALUES (?, ?, ?, ?, ?)",
                (root.root_id, root.page_title, root.page_url, root.added_time, int(root.is_active)),
                commit=True,
            )
        except sqlite3.IntegrityError as exc:
            raise StorageError(f"Root {root.root_id} already exists") from exc
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to add root {root.root_id}: {exc}") from exc

    def remove(self, root_id: str) -> None:
        """Remove a Notion root configuration (does NOT cascade-delete synced data)."""
        try:
            self._execute(
                "DELETE FROM notion_roots WHERE root_id = ?", (root_id,),
                commit=True,
            )
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to remove root {root_id}: {exc}") from exc

    def list_all(self) -> list[NotionRoot]:
        """Return all configured Notion roots."""
        try:
            rows = self._execute("SELECT * FROM notion_roots ORDER BY added_time DESC").fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to list roots: {exc}") from exc
        return [self._row_to_root(row) for row in rows]

    def get(self, root_id: str) -> NotionRoot | None:
        """Return a single root configuration, or None if not found."""
        try:
            row = self._execute(
                "SELECT * FROM notion_roots WHERE root_id = ?", (root_id,)
            ).fetchone()
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to get root {root_id}: {exc}") from exc
        if row is None:
            return None
        return self._row_to_root(row)

    def list_active_ids(self) -> list[str]:
        """Return page IDs of all active roots (for use as sync targets)."""
        try:
            rows = self._execute(
                "SELECT root_id FROM notion_roots WHERE is_active = 1"
            ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to list active root IDs: {exc}") from exc
        return [row["root_id"] for row in rows]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_root(row: sqlite3.Row) -> NotionRoot:
        return NotionRoot(
            root_id=row["root_id"],
            page_title=row["page_title"],
            page_url=row["page_url"],
            added_time=row["added_time"],
            is_active=bool(row["is_active"]),
        )
