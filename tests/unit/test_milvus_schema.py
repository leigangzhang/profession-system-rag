from __future__ import annotations

import unittest

from rag_notion_kb.storage.schema import BM25_FUNCTION, COLLECTION_SCHEMA, FIELDS


class TestMilvusSchema(unittest.TestCase):
    def test_field_count(self) -> None:
        names = {f.name for f in FIELDS}
        expected = {
            "id",
            "chunk_text",
            "dense_vector",
            "sparse_vector",
            "page_id",
            "page_title",
            "page_url",
            "header_path",
            "header_level",
            "last_edited_time",
            "chunk_index",
            "chunk_type",
            "image_url",
        }
        self.assertEqual(names, expected)
        self.assertEqual(len(FIELDS), 13)

    def test_dense_vector_dim(self) -> None:
        dense = next(f for f in FIELDS if f.name == "dense_vector")
        self.assertEqual(dense.params.get("dim"), 2048)

    def test_chunk_text_analyzer_and_match(self) -> None:
        field = next(f for f in FIELDS if f.name == "chunk_text")
        self.assertTrue(field.params.get("enable_analyzer"))
        self.assertTrue(field.params.get("enable_match"))

    def test_bm25_function(self) -> None:
        self.assertEqual(BM25_FUNCTION.name, "chunk_text_bm25_emb")
        self.assertEqual(BM25_FUNCTION.input_field_names, ["chunk_text"])
        self.assertEqual(BM25_FUNCTION.output_field_names, ["sparse_vector"])

    def test_collection_has_function(self) -> None:
        self.assertEqual(len(COLLECTION_SCHEMA.functions), 1)


if __name__ == "__main__":
    unittest.main()
