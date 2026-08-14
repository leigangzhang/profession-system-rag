from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

from rag_notion_kb.exceptions import StorageError
from rag_notion_kb.models import PageMetadata, TreePageNode
from rag_notion_kb.storage.sqlite_utils import QueryResult

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS page_tree_cache (
    root_id TEXT NOT NULL,
    page_id TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    last_edited_time TEXT NOT NULL,
    parent_id TEXT,
    cached_at TEXT NOT NULL,
    PRIMARY KEY (root_id, page_id)
);
CREATE INDEX IF NOT EXISTS idx_tree_cache_root ON page_tree_cache(root_id);
"""


class NotionTreeCache:
    """Cached page tree for Notion roots, stored in SQLite.

    Populated by ``enumerate_pages`` (lightweight, metadata-only) and read
    instantly for the tree view. Cache lives in the same DB as *root_store*.
    """

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
        with self._lock:
            self._conn.executescript(sql)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _ensure_schema(self) -> None:
        try:
            self._executescript(_DDL)
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to create tree cache schema: {exc}") from exc

    # ------------------------------------------------------------------
    # Cache operations
    # ------------------------------------------------------------------

    def replace_root(self, root_id: str, pages: list[PageMetadata]) -> None:
        """Clear cache for *root_id* and repopulate from *pages* (flat list)."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            with self._lock:
                self._conn.execute("DELETE FROM page_tree_cache WHERE root_id = ?", (root_id,))
                self._conn.executemany(
                    "INSERT INTO page_tree_cache (root_id, page_id, title, url, last_edited_time, parent_id, cached_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (root_id, p.page_id, p.title, p.url, p.last_edited_time, p.parent_id, now)
                        for p in pages
                    ],
                )
                self._conn.commit()
            logger.info("Tree cache for root %s updated: %d pages", root_id, len(pages))
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to update tree cache for {root_id}: {exc}") from exc

    def get_tree(self, root_id: str) -> tuple[list[TreePageNode], str | None]:
        """Return cached tree and *cached_at* timestamp, or empty + None if no cache."""
        try:
            rows = self._execute(
                "SELECT * FROM page_tree_cache WHERE root_id = ?", (root_id,)
            ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to read tree cache for {root_id}: {exc}") from exc

        if not rows:
            return [], None

        cached_at = rows[0]["cached_at"]
        # Build id → node map
        node_map: dict[str, TreePageNode] = {}
        for row in rows:
            node_map[row["page_id"]] = TreePageNode(
                page_id=row["page_id"],
                title=row["title"],
                url=row["url"],
                last_edited_time=row["last_edited_time"],
                children=[],
            )

        # Wire up children
        root_nodes: list[TreePageNode] = []
        for row in rows:
            node = node_map[row["page_id"]]
            if row["parent_id"] and row["parent_id"] in node_map:
                node_map[row["parent_id"]].children.append(node)
            else:
                root_nodes.append(node)

        return root_nodes, cached_at

    def has_cache(self, root_id: str) -> bool:
        """Return True if cache exists for *root_id*."""
        try:
            row = self._execute(
                "SELECT 1 FROM page_tree_cache WHERE root_id = ? LIMIT 1", (root_id,)
            ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    def list_page_ids(self, root_id: str) -> list[str]:
        """Return all page IDs stored for *root_id*."""
        try:
            rows = self._execute(
                "SELECT page_id FROM page_tree_cache WHERE root_id = ?", (root_id,)
            ).fetchall()
            return [row["page_id"] for row in rows]
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to list pages for root {root_id}: {exc}") from exc

    def get_page_root(self, page_id: str) -> str | None:
        """Return the root_id that owns *page_id*, or None if unknown."""
        try:
            row = self._execute(
                "SELECT root_id FROM page_tree_cache WHERE page_id = ? LIMIT 1",
                (page_id,),
            ).fetchone()
            return row["root_id"] if row else None
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to find root for {page_id}: {exc}") from exc

    def invalidate(self, root_id: str) -> None:
        """Remove cached tree for *root_id*."""
        try:
            self._execute("DELETE FROM page_tree_cache WHERE root_id = ?", (root_id,), commit=True)
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to invalidate tree cache for {root_id}: {exc}") from exc
