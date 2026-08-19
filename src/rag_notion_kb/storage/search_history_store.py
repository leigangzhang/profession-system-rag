from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rag_notion_kb.exceptions import StorageError
from rag_notion_kb.models import SearchHistory, SearchSource
from rag_notion_kb.storage.sqlite_utils import QueryResult

logger = logging.getLogger(__name__)

_MAX_HISTORY_RECORDS = 500
_HISTORY_RETENTION_DAYS = 30

_DDL = """
CREATE TABLE IF NOT EXISTS search_history (
    history_id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('debug','mcp','cli')),
    params TEXT NOT NULL,
    result_summary TEXT NOT NULL,
    snapshot TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_search_history_created_at
    ON search_history(created_at);
CREATE INDEX IF NOT EXISTS idx_search_history_source
    ON search_history(source);
"""


class SearchHistoryStore:
    """Persistent SQLite store for searches from the Web, MCP, and CLI."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: list[Future[None]] = []
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="search-history",
        )
        self._conn.execute("PRAGMA busy_timeout = 10000")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()

    def _execute(self, sql: str, params: tuple = (), *, commit: bool = False) -> QueryResult:
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
        """Close the underlying database connection."""
        self._flush_pending()
        self._executor.shutdown(wait=True)
        with self._lock:
            self._conn.close()

    def _ensure_schema(self) -> None:
        try:
            self._executescript(_DDL)
            self._migrate_schema()
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to create search_history schema: {exc}") from exc

    def _migrate_schema(self) -> None:
        """Add the snapshot column to databases created by older versions."""
        existing = {
            row[1]
            for row in self._execute("PRAGMA table_info(search_history)").fetchall()
        }
        if "snapshot" not in existing:
            try:
                self._execute(
                    "ALTER TABLE search_history ADD COLUMN snapshot TEXT",
                    commit=True,
                )
            except sqlite3.Error as exc:
                raise StorageError(f"Search history snapshot migration failed: {exc}") from exc

    def add(self, record: SearchHistory) -> None:
        """Insert one search history record."""
        params = self._dump_json(record.params)
        summary = self._dump_json(record.result_summary)
        snapshot = self._dump_json(record.snapshot) if record.snapshot is not None else None
        try:
            self._execute(
                """
                INSERT INTO search_history (
                    history_id, query, source, params, result_summary, snapshot, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.history_id,
                    record.query,
                    record.source.value,
                    params,
                    summary,
                    snapshot,
                    record.created_at,
                ),
                commit=True,
            )
        except sqlite3.IntegrityError as exc:
            raise StorageError(f"Search history {record.history_id} already exists") from exc
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to add search history {record.history_id}: {exc}") from exc
        self._enforce_retention()

    def add_async(self, record: SearchHistory) -> None:
        """Queue one record for background insertion.

        Retrieval callers can return before the write completes. Reads flush the
        queue first, and ``close()`` waits for all pending records.
        """
        future = self._executor.submit(self.add, record)
        with self._pending_lock:
            self._pending.append(future)

    def list_recent(
        self,
        limit: int = 50,
        source: SearchSource | str | None = None,
    ) -> list[SearchHistory]:
        """Return recent search records, optionally restricted to one source."""
        if limit < 1:
            raise StorageError("Search history limit must be positive")
        self._flush_pending()

        if source is None:
            sql = "SELECT * FROM search_history ORDER BY created_at DESC LIMIT ?"
            params: tuple = (limit,)
        else:
            source_value = source.value if isinstance(source, SearchSource) else source
            sql = (
                "SELECT * FROM search_history WHERE source = ? "
                "ORDER BY created_at DESC LIMIT ?"
            )
            params = (source_value, limit)

        try:
            rows = self._execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to list search history: {exc}") from exc
        return [self._row_to_history(row) for row in rows]

    def stats(self) -> dict[str, object]:
        """Return aggregate retrieval-health metrics for retained history."""
        self._flush_pending()
        try:
            rows = self._execute(
                "SELECT source, result_summary, snapshot FROM search_history"
            ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to read search history stats: {exc}") from exc

        total = len(rows)
        by_source = {
            SearchSource.DEBUG.value: 0,
            SearchSource.MCP.value: 0,
            SearchSource.CLI.value: 0,
        }
        success_count = 0
        zero_result_count = 0
        top_scores: list[float] = []
        latencies: list[float] = []
        llm_durations: list[float] = []
        llm_ratios: list[float] = []

        for row in rows:
            source = SearchSource(row["source"])
            by_source[source.value] += 1
            try:
                summary = json.loads(row["result_summary"])
            except (TypeError, ValueError) as exc:
                raise StorageError("Invalid JSON in search history stats") from exc

            success = bool(summary.get("success", True))
            result_count = int(summary.get("total_results", 0) or 0)
            if success:
                success_count += 1
                if result_count == 0:
                    zero_result_count += 1
                if result_count > 0:
                    top_scores.append(float(summary.get("top_score", 0.0) or 0.0))

            latency = summary.get("latency_ms")
            if latency is not None:
                latencies.append(max(0.0, float(latency)))

            if row["snapshot"]:
                try:
                    snapshot = json.loads(row["snapshot"])
                except (TypeError, ValueError):
                    snapshot = None
                if isinstance(snapshot, dict):
                    duration = snapshot.get("summary_duration_ms")
                    ratio = snapshot.get("summary_compression_ratio")
                    if isinstance(duration, (int, float)):
                        llm_durations.append(max(0.0, float(duration)))
                    if isinstance(ratio, (int, float)):
                        llm_ratios.append(max(0.0, min(1.0, float(ratio))))

        p99 = self._percentile(top_scores, 99)
        p90 = self._percentile(top_scores, 90)
        p60 = self._percentile(top_scores, 60)
        success_rate = success_count / total if total else 0.0
        zero_result_rate = zero_result_count / total if total else 0.0
        quality_score = (
            p90 * 0.8 + (1.0 - zero_result_rate) * 0.2
            if total
            else 0.0
        )

        return {
            "total_queries": total,
            "by_source": by_source,
            "success_rate": round(success_rate, 4),
            "zero_result_rate": round(zero_result_rate, 4),
            "top_score_p99": round(p99, 4),
            "top_score_p90": round(p90, 4),
            "top_score_p60": round(p60, 4),
            "average_latency_ms": round(sum(latencies) / len(latencies), 1)
            if latencies
            else 0.0,
            "llm_summary_avg_duration_ms": round(
                sum(llm_durations) / len(llm_durations), 1
            )
            if llm_durations
            else 0.0,
            "llm_summary_avg_compression_ratio": round(
                sum(llm_ratios) / len(llm_ratios), 4
            )
            if llm_ratios
            else 0.0,
            "quality_score": round(quality_score, 4),
        }

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = max(0, min(len(ordered) - 1, math.ceil(percentile / 100 * len(ordered)) - 1))
        return ordered[index]

    def get(self, history_id: str) -> SearchHistory | None:
        """Return one history record, or None when the ID is unknown."""
        self._flush_pending()
        try:
            row = self._execute(
                "SELECT * FROM search_history WHERE history_id = ?",
                (history_id,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to get search history {history_id}: {exc}") from exc
        if row is None:
            return None
        return self._row_to_history(row)

    def update_snapshot(self, history_id: str, snapshot: dict[str, object]) -> bool:
        """Update the saved result snapshot for one history record."""
        self._flush_pending()
        try:
            result = self._execute(
                "UPDATE search_history SET snapshot = ? WHERE history_id = ?",
                (self._dump_json(snapshot), history_id),
                commit=True,
            )
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to update search history {history_id}: {exc}") from exc
        return result.rowcount > 0

    def delete(self, history_id: str) -> bool:
        """Delete one history record and report whether it existed."""
        self._flush_pending()
        try:
            result = self._execute(
                "DELETE FROM search_history WHERE history_id = ?",
                (history_id,),
                commit=True,
            )
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to delete search history {history_id}: {exc}") from exc
        return result.rowcount > 0

    def clear_all(self) -> int:
        """Delete every history record and return the removed row count."""
        self._flush_pending()
        try:
            result = self._execute("DELETE FROM search_history", commit=True)
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to clear search history: {exc}") from exc
        return result.rowcount

    def _enforce_retention(self) -> None:
        """Keep only the most recent 30 days and at most 500 snapshots."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=_HISTORY_RETENTION_DAYS)).isoformat()
        try:
            self._execute(
                "DELETE FROM search_history WHERE julianday(created_at) < julianday(?)",
                (cutoff,),
                commit=True,
            )
            self._execute(
                """
                DELETE FROM search_history
                WHERE history_id NOT IN (
                    SELECT history_id FROM search_history
                    ORDER BY created_at DESC, history_id DESC
                    LIMIT ?
                )
                """,
                (_MAX_HISTORY_RECORDS,),
                commit=True,
            )
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to enforce search history retention: {exc}") from exc

    def _flush_pending(self) -> None:
        """Wait for queued writes so reads observe the complete search history."""
        with self._pending_lock:
            pending = list(self._pending)
            self._pending = []
        for future in pending:
            try:
                future.result()
            except Exception:
                logger.exception("Background search history write failed")

    @staticmethod
    def _dump_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _row_to_history(row: sqlite3.Row) -> SearchHistory:
        try:
            params = json.loads(row["params"])
            summary = json.loads(row["result_summary"])
            snapshot = json.loads(row["snapshot"]) if row["snapshot"] else None
        except (TypeError, ValueError) as exc:
            raise StorageError(f"Invalid JSON in search history {row['history_id']}") from exc
        return SearchHistory(
            history_id=row["history_id"],
            query=row["query"],
            source=SearchSource(row["source"]),
            params=params,
            result_summary=summary,
            snapshot=snapshot,
            created_at=row["created_at"],
        )
