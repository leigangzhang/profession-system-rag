from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from rag_notion_kb.models import SearchHistory, SearchSource
from rag_notion_kb.storage.search_history_store import SearchHistoryStore


def _record(
    history_id: str,
    query: str,
    source: SearchSource,
    created_at: str,
) -> SearchHistory:
    return SearchHistory(
        history_id=history_id,
        query=query,
        source=source,
        params={"query": query, "top_k": 10},
        result_summary={"total_results": 2, "top_score": 0.91},
        created_at=created_at,
    )


@pytest.fixture
def store(tmp_path: Path) -> SearchHistoryStore:
    instance = SearchHistoryStore(tmp_path / "history.db")
    yield instance
    instance.close()


def test_add_get_and_list(store: SearchHistoryStore) -> None:
    store.add(_record("1", "first", SearchSource.DEBUG, "2026-08-12T10:00:00Z"))
    store.add(_record("2", "second", SearchSource.MCP, "2026-08-12T11:00:00Z"))

    record = store.get("1")
    assert record is not None
    assert record.query == "first"
    assert record.source is SearchSource.DEBUG
    assert record.params == {"query": "first", "top_k": 10}

    assert [item.history_id for item in store.list_recent()] == ["2", "1"]
    assert [item.history_id for item in store.list_recent(source=SearchSource.MCP)] == ["2"]


def test_delete_and_clear(store: SearchHistoryStore) -> None:
    store.add(_record("1", "first", SearchSource.CLI, "2026-08-12T10:00:00Z"))
    store.add(_record("2", "second", SearchSource.DEBUG, "2026-08-12T11:00:00Z"))

    assert store.delete("1") is True
    assert store.delete("missing") is False
    assert store.get("1") is None
    assert store.clear_all() == 1
    assert store.list_recent() == []


def test_add_duplicate_raises(store: SearchHistoryStore) -> None:
    store.add(_record("1", "first", SearchSource.DEBUG, "2026-08-12T10:00:00Z"))
    with pytest.raises(Exception):
        store.add(_record("1", "duplicate", SearchSource.CLI, "2026-08-12T11:00:00Z"))


def test_async_add_is_visible_to_reads(store: SearchHistoryStore) -> None:
    store.add_async(_record("1", "queued", SearchSource.CLI, "2026-08-12T10:00:00Z"))
    assert store.list_recent()[0].history_id == "1"


def test_snapshot_roundtrip(store: SearchHistoryStore) -> None:
    record = _record("1", "snapshot", SearchSource.DEBUG, "2026-08-12T10:00:00Z")
    record.snapshot = {"query": "snapshot", "results": [{"rank": 1, "score": 0.9}]}
    store.add(record)

    loaded = store.get("1")
    assert loaded is not None
    assert loaded.snapshot == record.snapshot


def test_retention_drops_records_older_than_30_days(store: SearchHistoryStore) -> None:
    old_time = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    new_time = datetime.now(timezone.utc).isoformat()
    store.add(_record("old", "old", SearchSource.DEBUG, old_time))
    store.add(_record("new", "new", SearchSource.DEBUG, new_time))

    assert [item.history_id for item in store.list_recent()] == ["new"]


def test_retention_keeps_max_record_count(store: SearchHistoryStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("rag_notion_kb.storage.search_history_store._MAX_HISTORY_RECORDS", 3)
    start = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)
    for index in range(1, 5):
        created_at = (start + timedelta(minutes=index)).isoformat()
        store.add(_record(str(index), f"q{index}", SearchSource.CLI, created_at))

    assert [item.history_id for item in store.list_recent()] == ["4", "3", "2"]


def test_migrates_existing_table_without_snapshot(tmp_path: Path) -> None:
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE search_history (
            history_id TEXT PRIMARY KEY,
            query TEXT NOT NULL,
            source TEXT NOT NULL,
            params TEXT NOT NULL,
            result_summary TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.close()

    migrated = SearchHistoryStore(db_path)
    try:
        record = _record("1", "migrated", SearchSource.DEBUG, "2026-08-12T10:00:00Z")
        record.snapshot = {"query": "migrated"}
        migrated.add(record)
        assert migrated.get("1").snapshot == {"query": "migrated"}
    finally:
        migrated.close()


def test_stats_empty_history(store: SearchHistoryStore) -> None:
    stats = store.stats()
    assert stats == {
        "total_queries": 0,
        "by_source": {"debug": 0, "mcp": 0, "cli": 0},
        "success_rate": 0.0,
        "zero_result_rate": 0.0,
        "top_score_p99": 0.0,
        "top_score_p90": 0.0,
        "top_score_p60": 0.0,
        "average_latency_ms": 0.0,
        "quality_score": 0.0,
    }


def test_stats_aggregates_sources_scores_and_failures(store: SearchHistoryStore) -> None:
    records = [
        _record("1", "debug-hit", SearchSource.DEBUG, "2026-08-12T10:00:00Z"),
        _record("2", "mcp-zero", SearchSource.MCP, "2026-08-12T10:01:00Z"),
        _record("3", "cli-failed", SearchSource.CLI, "2026-08-12T10:02:00Z"),
        _record("4", "debug-hit", SearchSource.DEBUG, "2026-08-12T10:03:00Z"),
        _record("5", "debug-hit", SearchSource.DEBUG, "2026-08-12T10:04:00Z"),
    ]
    records[0].result_summary = {
        "total_results": 1,
        "top_score": 0.6,
        "latency_ms": 100,
        "success": True,
    }
    records[1].result_summary = {
        "total_results": 0,
        "top_score": 0.0,
        "latency_ms": 200,
        "success": True,
    }
    records[2].result_summary = {
        "total_results": 0,
        "top_score": 0.0,
        "latency_ms": 300,
        "success": False,
        "error": "failed",
    }
    records[3].result_summary = {
        "total_results": 1,
        "top_score": 0.9,
        "latency_ms": 400,
        "success": True,
    }
    records[4].result_summary = {
        "total_results": 1,
        "top_score": 0.8,
        "latency_ms": 500,
        "success": True,
    }
    for record in records:
        store.add(record)

    stats = store.stats()
    assert stats["total_queries"] == 5
    assert stats["by_source"] == {"debug": 3, "mcp": 1, "cli": 1}
    assert stats["success_rate"] == pytest.approx(0.8)
    assert stats["zero_result_rate"] == pytest.approx(0.2)
    assert stats["top_score_p99"] == pytest.approx(0.9)
    assert stats["top_score_p90"] == pytest.approx(0.9)
    assert stats["top_score_p60"] == pytest.approx(0.8)
    assert stats["average_latency_ms"] == pytest.approx(300.0)
    assert stats["quality_score"] == pytest.approx(0.88)


def test_stats_treats_legacy_successless_record_as_success(store: SearchHistoryStore) -> None:
    record = _record("1", "legacy", SearchSource.CLI, "2026-08-12T10:00:00Z")
    record.result_summary = {
        "total_results": 2,
        "top_score": 0.8,
        "latency_ms": 120,
    }
    store.add(record)

    stats = store.stats()
    assert stats["success_rate"] == 1.0
    assert stats["zero_result_rate"] == 0.0
    assert stats["top_score_p90"] == pytest.approx(0.8)
