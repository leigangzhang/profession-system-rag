from __future__ import annotations

import unittest

from rag_notion_kb.exceptions import (
    ConfigError,
    EmbeddingError,
    NotionApiError,
    RagKbError,
    RetrievalError,
    StorageError,
)


class TestExceptions(unittest.TestCase):
    def test_inheritance(self) -> None:
        self.assertIsInstance(ConfigError("x"), RagKbError)
        self.assertIsInstance(NotionApiError("x"), RagKbError)
        self.assertIsInstance(EmbeddingError("x"), RagKbError)
        self.assertIsInstance(StorageError("x"), RagKbError)
        self.assertIsInstance(RetrievalError("x"), RagKbError)

    def test_catch_base(self) -> None:
        try:
            raise NotionApiError("rate limited")
        except RagKbError as exc:
            self.assertEqual(str(exc), "rate limited")


if __name__ == "__main__":
    unittest.main()
