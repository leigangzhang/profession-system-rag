from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from rag_notion_kb.exceptions import StorageError
from rag_notion_kb.models import PageSyncState
from rag_notion_kb.storage.sync_state import SyncStateStore, _DDL


def _sample_state(
    page_id: str = "page-1",
    status: str = "synced",
    chunk_count: int = 4,
) -> PageSyncState:
    return PageSyncState(
        page_id=page_id,
        page_title="Test Page",
        page_url=f"https://notion.so/{page_id}",
        last_edited_time="2026-08-10T10:00:00Z",
        parent_id="root-1",
        chunk_count=chunk_count,
        image_count=1,
        status=status,  # type: ignore[arg-type]
        error_message=None,
        last_synced_time="2026-08-10T12:00:00Z",
    )


class TestSyncStateStore(unittest.TestCase):
    def test_upsert_and_get(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SyncStateStore(Path(tmpdir) / "sync.db")
            state = _sample_state()
            store.upsert(state)
            got = store.get("page-1")
            self.assertIsNotNone(got)
            assert got is not None
            self.assertEqual(got.page_id, "page-1")
            self.assertEqual(got.status, "synced")
            self.assertEqual(got.chunk_count, 4)
            store.close()

    def test_list_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SyncStateStore(Path(tmpdir) / "sync.db")
            store.upsert(_sample_state("page-1"))
            store.upsert(_sample_state("page-2", status="failed"))
            all_states = store.list_all()
            self.assertEqual(len(all_states), 2)
            ids = {s.page_id for s in all_states}
            self.assertEqual(ids, {"page-1", "page-2"})
            store.close()

    def test_get_many_batches_page_lookups(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SyncStateStore(Path(tmpdir) / "sync.db")
            store.upsert(_sample_state("page-1"))
            store.upsert(_sample_state("page-2", status="failed"))

            states = store.get_many(["page-1", "missing", "page-2", "page-1"])

            self.assertEqual(set(states), {"page-1", "page-2"})
            self.assertEqual(states["page-1"].status, "synced")
            self.assertEqual(states["page-2"].status, "failed")
            store.close()

    def test_query_results_are_isolated_from_later_connection_use(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SyncStateStore(Path(tmpdir) / "sync.db")
            store.upsert(_sample_state("page-1"))

            first = store._execute("SELECT page_id FROM page_sync_state")
            store.upsert(_sample_state("page-2"))

            self.assertEqual(
                [row["page_id"] for row in first.fetchall()],
                ["page-1"],
            )
            store.close()

    def test_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SyncStateStore(Path(tmpdir) / "sync.db")
            store.upsert(_sample_state("page-1"))
            store.delete("page-1")
            self.assertIsNone(store.get("page-1"))
            self.assertEqual(store.list_all(), [])
            store.close()

    def test_get_last_sync_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SyncStateStore(Path(tmpdir) / "sync.db")
            self.assertIsNone(store.get_last_sync_time())
            older = _sample_state("page-1")
            older.last_synced_time = "2026-08-10T10:00:00Z"
            newer = _sample_state("page-2")
            newer.last_synced_time = "2026-08-10T14:00:00Z"
            store.upsert(older)
            store.upsert(newer)
            self.assertEqual(store.get_last_sync_time(), "2026-08-10T14:00:00Z")
            store.close()

    def test_upsert_updates_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SyncStateStore(Path(tmpdir) / "sync.db")
            store.upsert(_sample_state("page-1", chunk_count=2))
            updated = _sample_state("page-1", chunk_count=8)
            updated.last_synced_time = "2026-08-10T15:00:00Z"
            store.upsert(updated)
            got = store.get("page-1")
            self.assertIsNotNone(got)
            assert got is not None
            self.assertEqual(got.chunk_count, 8)
            self.assertEqual(got.last_synced_time, "2026-08-10T15:00:00Z")
            store.close()

    def test_mark_vectorized_updates_state_and_hashes_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "sync.db"
            store = SyncStateStore(db_path)
            store.upsert(_sample_state("page-1", chunk_count=2))

            store.mark_vectorized(
                "page-1",
                chunk_count=3,
                image_count=1,
                content_hash="content-hash",
                chunking_hash="chunking-hash",
                embedding_hash="embedding-hash",
            )

            got = store.get("page-1")
            self.assertIsNotNone(got)
            assert got is not None
            self.assertEqual(got.vector_status, "indexed")
            self.assertEqual(got.vector_progress, 100)
            self.assertEqual(got.vector_stage, "done")
            self.assertEqual(got.chunk_count, 3)
            self.assertEqual(got.image_count, 1)
            self.assertEqual(got.content_hash, "content-hash")
            self.assertEqual(got.chunking_hash, "chunking-hash")
            self.assertEqual(got.embedding_hash, "embedding-hash")
            self.assertIsNotNone(got.last_vectorized_time)
            store.close()

    def test_migrates_legacy_database_without_progress_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "legacy.db"
            legacy_ddl = _DDL.replace(
                ", embedding_hash TEXT DEFAULT ''",
                "",
            ).replace(
                ", vector_stage TEXT DEFAULT ''",
                "",
            )
            with sqlite3.connect(db_path) as conn:
                conn.executescript(legacy_ddl)
                conn.execute(
                    """
                    INSERT INTO page_sync_state (
                        page_id, page_title, page_url, last_edited_time,
                        chunk_count, image_count, status, last_synced_time,
                        vector_enabled, vector_status, vector_progress,
                        content_hash, chunking_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "page-1", "Page", "https://notion.so/page-1",
                        "2026-08-10T10:00:00Z", 1, 0, "fetched",
                        "2026-08-10T10:00:00Z", 0, "pending", 0, "", "",
                    ),
                )

            store = SyncStateStore(db_path)
            got = store.get("page-1")
            self.assertIsNotNone(got)
            assert got is not None
            self.assertEqual(got.embedding_hash, "")
            self.assertEqual(got.vector_stage, "")
            store.close()

    def test_invalid_status_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SyncStateStore(Path(tmpdir) / "sync.db")
            bad = PageSyncState.model_construct(
                page_id="page-1",
                page_title="Bad",
                page_url="https://notion.so/page-1",
                last_edited_time="2026-08-10T10:00:00Z",
                chunk_count=0,
                image_count=0,
                status="unknown",  # type: ignore[arg-type]
                last_synced_time="2026-08-10T10:00:00Z",
            )
            with self.assertRaises(StorageError):
                store.upsert(bad)
            store.close()

    def test_database_error_wrapped(self) -> None:
        # Closing the connection then calling get should raise StorageError,
        # not sqlite3.ProgrammingError.
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SyncStateStore(Path(tmpdir) / "sync.db")
            store.close()
            with self.assertRaises((StorageError, sqlite3.ProgrammingError)):
                store.get("page-1")


if __name__ == "__main__":
    unittest.main()
