from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from rag_notion_kb.exceptions import StorageError
from rag_notion_kb.models import NotionRoot
from rag_notion_kb.storage.root_store import NotionRootStore


class TestNotionRootStore:
    """Unit tests for NotionRootStore CRUD operations."""

    @pytest.fixture
    def store(self) -> NotionRootStore:
        """Create a temporary NotionRootStore backed by an in-memory SQLite DB."""
        # Use a temp file so we test real SQLite behavior
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        store = NotionRootStore(db_path=Path(tmp.name))
        yield store
        store.close()
        Path(tmp.name).unlink(missing_ok=True)

    @staticmethod
    def _make_root(root_id: str = "a1b2c3d4", title: str = "Test Root") -> NotionRoot:
        return NotionRoot(
            root_id=root_id,
            page_title=title,
            page_url=f"https://www.notion.so/{root_id}",
            added_time="2026-08-12T10:00:00Z",
            is_active=True,
        )

    def test_add_and_get(self, store: NotionRootStore) -> None:
        root = self._make_root()
        store.add(root)
        fetched = store.get(root.root_id)
        assert fetched is not None
        assert fetched.root_id == root.root_id
        assert fetched.page_title == root.page_title
        assert fetched.page_url == root.page_url
        assert fetched.added_time == root.added_time
        assert fetched.is_active is True

    def test_add_duplicate_raises(self, store: NotionRootStore) -> None:
        root = self._make_root()
        store.add(root)
        with pytest.raises(StorageError, match="already exists"):
            store.add(root)

    def test_get_nonexistent_returns_none(self, store: NotionRootStore) -> None:
        assert store.get("nonexistent-id") is None

    def test_list_all(self, store: NotionRootStore) -> None:
        r1 = self._make_root("root-1", "Root 1")
        r2 = self._make_root("root-2", "Root 2")
        store.add(r1)
        store.add(r2)
        roots = store.list_all()
        assert len(roots) == 2
        # Both roots present
        root_ids = {r.root_id for r in roots}
        assert "root-1" in root_ids
        assert "root-2" in root_ids

    def test_remove(self, store: NotionRootStore) -> None:
        root = self._make_root()
        store.add(root)
        assert store.get(root.root_id) is not None
        store.remove(root.root_id)
        assert store.get(root.root_id) is None

    def test_remove_nonexistent_no_error(self, store: NotionRootStore) -> None:
        # Removing a non-existent root should not raise
        store.remove("nonexistent-id")

    def test_list_active_ids(self, store: NotionRootStore) -> None:
        r1 = self._make_root("active-1", "Active 1")
        r2 = NotionRoot(
            root_id="inactive-1",
            page_title="Inactive 1",
            page_url="https://www.notion.so/inactive-1",
            added_time="2026-08-12T10:00:00Z",
            is_active=False,
        )
        store.add(r1)
        store.add(r2)
        active = store.list_active_ids()
        assert active == ["active-1"]

    def test_empty_store(self, store: NotionRootStore) -> None:
        assert store.list_all() == []
        assert store.list_active_ids() == []
