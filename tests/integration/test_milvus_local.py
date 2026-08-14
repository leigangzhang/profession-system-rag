from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
import pytest
from pymilvus import MilvusClient

from rag_notion_kb.storage.schema import COLLECTION_SCHEMA, DENSE_DIM


def _random_page_id() -> str:
    return uuid.uuid4().hex[:16]


pytestmark = pytest.mark.requires_milvus_local


class TestMilvusLiteLocal:
    """Real Milvus Lite integration test: create, insert, search, delete."""

    @pytest.fixture(scope="class")
    def client(self) -> MilvusClient:
        tmp_dir = tempfile.mkdtemp(prefix="milvus_lite_")
        db_path = Path(tmp_dir) / "test_milvus.db"
        milvus_client: MilvusClient | None = None
        try:
            milvus_client = MilvusClient(uri=str(db_path))
            yield milvus_client
        finally:
            if milvus_client is not None:
                milvus_client.close()
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @pytest.fixture(scope="class")
    def collection_name(self) -> str:
        return "test_rag_kb_chunks"

    def test_create_collection(self, client: MilvusClient, collection_name: str) -> None:
        if client.has_collection(collection_name):
            client.drop_collection(collection_name)

        client.create_collection(
            collection_name=collection_name,
            schema=COLLECTION_SCHEMA,
            dimension=DENSE_DIM,
        )

        assert client.has_collection(collection_name)
        info = client.describe_collection(collection_name)
        fields = {f["name"] for f in info["fields"]}
        assert "id" in fields
        assert "chunk_text" in fields
        assert "dense_vector" in fields
        assert "sparse_vector" in fields
        assert "page_id" in fields

        # Validate BM25 function registered.
        functions = info.get("functions", [])
        assert any(f["name"] == "chunk_text_bm25_emb" for f in functions)

        # Validate dense vector dimension.
        dense_field = next(f for f in info["fields"] if f["name"] == "dense_vector")
        assert int(dense_field["params"]["dim"]) == DENSE_DIM

    def test_insert_and_dense_search(
        self, client: MilvusClient, collection_name: str
    ) -> None:
        page_id = _random_page_id()
        rows = [
            {
                "chunk_text": "Milvus Lite is a lightweight vector database.",
                "dense_vector": [1.0] + [0.0] * (DENSE_DIM - 1),
                "page_id": page_id,
                "page_title": "Vector DB",
                "page_url": "https://example.com/vector-db",
                "header_path": "# Vector DB",
                "header_level": 1,
                "last_edited_time": "2026-08-11T00:00:00Z",
                "chunk_index": 0,
                "chunk_type": "text",
                "image_url": None,
            },
            {
                "chunk_text": "BM25 is a sparse retrieval algorithm.",
                "dense_vector": [0.0] * DENSE_DIM,
                "page_id": page_id,
                "page_title": "Vector DB",
                "page_url": "https://example.com/vector-db",
                "header_path": "# Vector DB",
                "header_level": 1,
                "last_edited_time": "2026-08-11T00:00:00Z",
                "chunk_index": 1,
                "chunk_type": "text",
                "image_url": None,
            },
        ]
        insert_result = client.insert(collection_name, data=rows)
        assert insert_result["insert_count"] == 2

        client.flush(collection_name)
        client.load_collection(collection_name)

        # Dense search.
        dense_results = client.search(
            collection_name=collection_name,
            data=[[1.0] + [0.0] * (DENSE_DIM - 1)],
            anns_field="dense_vector",
            limit=1,
            output_fields=["chunk_text", "page_id", "chunk_index"],
            search_params={"metric_type": "COSINE"},
        )
        assert len(dense_results) == 1
        assert len(dense_results[0]) == 1
        hit = dense_results[0][0]
        assert hit["entity"]["chunk_text"].startswith("Milvus Lite")
        assert hit["entity"]["page_id"] == page_id

    def test_sparse_search(
        self, client: MilvusClient, collection_name: str
    ) -> None:
        client.load_collection(collection_name)
        sparse_results = client.search(
            collection_name=collection_name,
            data=["BM25 sparse retrieval algorithm"],
            anns_field="sparse_vector",
            limit=2,
            output_fields=["chunk_text", "page_id", "chunk_index"],
        )
        assert len(sparse_results) == 1
        assert len(sparse_results[0]) >= 1
        texts = {h["entity"]["chunk_text"] for h in sparse_results[0]}
        assert any("BM25" in t for t in texts)

    def test_delete_and_cleanup(
        self, client: MilvusClient, collection_name: str
    ) -> None:
        client.load_collection(collection_name)
        stats_before = client.get_collection_stats(collection_name)
        row_count_before = int(stats_before["row_count"])

        delete_result = client.delete(
            collection_name=collection_name,
            filter="chunk_type == 'text'",
        )
        assert len(delete_result) == row_count_before

        client.drop_collection(collection_name)
        assert not client.has_collection(collection_name)
