from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from rag_notion_kb.exceptions import StorageError
from rag_notion_kb.storage.milvus_store import MilvusStore


class TestMilvusFilters(unittest.TestCase):
    def _store(self) -> MilvusStore:
        with patch("rag_notion_kb.storage.milvus_store.MilvusClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            return MilvusStore("/tmp/test.db")

    def test_empty_filters(self) -> None:
        store = self._store()
        self.assertEqual(store._build_filter_expr({}), "")
        self.assertEqual(store._build_filter_expr(None), "")

    def test_page_ids(self) -> None:
        store = self._store()
        expr = store._build_filter_expr({"page_ids": ["0123456789abcdef0123456789abcdef", "fedcba9876543210fedcba9876543210"]})
        self.assertEqual(expr, 'page_id in ["0123456789abcdef0123456789abcdef", "fedcba9876543210fedcba9876543210"]')

    def test_header_level(self) -> None:
        store = self._store()
        expr = store._build_filter_expr({"header_level": 2})
        self.assertEqual(expr, "header_level == 2")

    def test_edited_after(self) -> None:
        store = self._store()
        expr = store._build_filter_expr({"edited_after": "2026-08-01T00:00:00Z"})
        self.assertEqual(expr, 'last_edited_time > "2026-08-01T00:00:00Z"')

    def test_combined_filters(self) -> None:
        store = self._store()
        expr = store._build_filter_expr(
            {
                "page_ids": ["0123456789abcdef0123456789abcdef"],
                "header_level": 2,
                "edited_after": "2026-08-01T00:00:00Z",
            }
        )
        self.assertIn('page_id in ["0123456789abcdef0123456789abcdef"]', expr)
        self.assertIn("header_level == 2", expr)
        self.assertIn('last_edited_time > "2026-08-01T00:00:00Z"', expr)

    def test_short_ids_are_safe_filter_values(self) -> None:
        store = self._store()
        expr = store._build_filter_expr({"page_ids": ["p1", "page-2"]})
        self.assertEqual(expr, 'page_id in ["p1", "page-2"]')

    def test_rejects_filter_injection(self) -> None:
        store = self._store()
        with self.assertRaises(StorageError):
            store._build_filter_expr({"page_ids": ['p1" or page_id != "p1']})

    def test_rejects_invalid_edited_after(self) -> None:
        store = self._store()
        with self.assertRaises(StorageError):
            store._build_filter_expr({"edited_after": '2026-01-01" or "" == "'})

    def test_chunk_type_and_page_title_lists(self) -> None:
        store = self._store()
        expr = store._build_filter_expr(
            {
                "chunk_type": ["text", "table"],
                "page_title": ["产品手册", "Architecture"],
            }
        )
        self.assertIn('chunk_type in ["text", "table"]', expr)
        self.assertIn('page_title in ["产品手册", "Architecture"]', expr)

    def test_header_level_scalar_or_list(self) -> None:
        store = self._store()
        self.assertEqual(store._build_filter_expr({"header_level": 2}), "header_level == 2")
        self.assertEqual(
            store._build_filter_expr({"header_level": ["1", "2", "3"]}),
            "header_level in [1, 2, 3]",
        )

    def test_rejects_unknown_filter_field(self) -> None:
        store = self._store()
        with self.assertRaises(StorageError):
            store._build_filter_expr({"unsupported": ["x"]})

    def test_rejects_invalid_chunk_type(self) -> None:
        store = self._store()
        with self.assertRaises(StorageError):
            store._build_filter_expr({"chunk_type": ["text", "invalid"]})


if __name__ == "__main__":
    unittest.main()
