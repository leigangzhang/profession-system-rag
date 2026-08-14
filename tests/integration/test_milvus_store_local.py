from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

from rag_notion_kb.models import Chunk, ChunkMetadata, ChunkType, ImageDoc
from rag_notion_kb.storage.milvus_store import MilvusStore
from rag_notion_kb.storage.schema import DENSE_DIM

pytestmark = pytest.mark.requires_milvus_local


def _random_page_id() -> str:
    return uuid.uuid4().hex[:16]


def _make_meta(page_id: str, title: str, chunk_index: int, header_level: int = 1) -> ChunkMetadata:
    return ChunkMetadata(
        page_id=page_id,
        page_title=title,
        page_url=f"https://notion.so/{page_id}",
        header_path="# " + title if header_level == 1 else "# {} > ## section".format(title),
        header_level=header_level,
        last_edited_time="2026-08-11T12:00:00Z",
        chunk_index=chunk_index,
        chunk_type=ChunkType.TEXT,
    )


def _dense_vector(prefix: float) -> list[float]:
    """Return a 2048-dim vector dominated by *prefix* in the first coordinate."""
    return [prefix] + [0.0] * (DENSE_DIM - 1)


@pytest.fixture
def store() -> MilvusStore:
    tmp_dir = tempfile.mkdtemp(prefix="milvus_store_")
    db_path = Path(tmp_dir) / "milvus_store.db"
    store_instance: MilvusStore | None = None
    try:
        store_instance = MilvusStore(uri=db_path, collection_name="test_milvus_store_local")
        store_instance.init_collection()
        yield store_instance
    finally:
        if store_instance is not None:
            store_instance.client.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)


class TestMilvusStoreLocal:
    """Real Milvus Lite integration test through the MilvusStore facade."""

    def test_upsert_and_search_dense(self, store: MilvusStore) -> None:
        page_id = _random_page_id()
        chunks = [
            Chunk(
                text="MilvusStore wraps Milvus Lite for local vector storage.",
                metadata=_make_meta(page_id, "Local Vector DB", 0),
            ),
            Chunk(
                text="Dense retrieval uses cosine similarity on embedding vectors.",
                metadata=_make_meta(page_id, "Local Vector DB", 1),
            ),
        ]
        image_docs: list[ImageDoc] = []
        embeddings = [_dense_vector(1.0), _dense_vector(0.0)]

        inserted = store.upsert_page(page_id, chunks, image_docs, embeddings)
        assert inserted == 2

        query = _dense_vector(1.0)
        hits = store.search_dense(query, limit=1)
        assert len(hits) == 1
        assert hits[0].chunk_text.startswith("MilvusStore")
        assert hits[0].metadata.page_id == page_id

    def test_upsert_and_search_sparse(self, store: MilvusStore) -> None:
        page_id = _random_page_id()
        chunks = [
            Chunk(
                text="BM25 sparse retrieval scores lexically matching tokens.",
                metadata=_make_meta(page_id, "Sparse Retrieval", 0),
            ),
            Chunk(
                text="Dense retrieval captures semantic meaning with embeddings.",
                metadata=_make_meta(page_id, "Sparse Retrieval", 1),
            ),
        ]
        embeddings = [_dense_vector(0.0), _dense_vector(0.0)]

        store.upsert_page(page_id, chunks, [], embeddings)

        hits = store.search_sparse("BM25 sparse retrieval", limit=2)
        assert len(hits) >= 1
        assert any("BM25" in hit.chunk_text for hit in hits)

    def test_image_doc_round_trip(self, store: MilvusStore) -> None:
        page_id = _random_page_id()
        chunks: list[Chunk] = []
        image_docs = [
            ImageDoc(
                image_url="https://example.com/arch.png",
                alt="system architecture",
                context_text="The architecture diagram shows vector database components.",
                metadata=ChunkMetadata(
                    page_id=page_id,
                    page_title="Architecture",
                    page_url=f"https://notion.so/{page_id}",
                    header_path="# Architecture",
                    header_level=1,
                    last_edited_time="2026-08-11T12:00:00Z",
                    chunk_index=0,
                    chunk_type=ChunkType.IMAGE,
                    image_url="https://example.com/arch.png",
                ),
            ),
        ]
        embeddings = [_dense_vector(0.5)]

        store.upsert_page(page_id, chunks, image_docs, embeddings)

        hits = store.get_page_chunks(page_id)
        assert len(hits) == 1
        assert hits[0].metadata.chunk_type == ChunkType.IMAGE
        assert hits[0].metadata.image_url == "https://example.com/arch.png"
        assert "architecture" in hits[0].chunk_text

    def test_stats_and_delete_by_page_id(self, store: MilvusStore) -> None:
        page_id = _random_page_id()
        chunks = [
            Chunk(
                text="First chunk for stats and deletion test.",
                metadata=_make_meta(page_id, "Stats Test", 0),
            ),
            Chunk(
                text="Second chunk for stats and deletion test.",
                metadata=_make_meta(page_id, "Stats Test", 1),
            ),
        ]
        embeddings = [_dense_vector(0.0), _dense_vector(0.0)]

        store.upsert_page(page_id, chunks, [], embeddings)
        stats = store.stats()
        assert stats["total_chunks"] == 2

        deleted = store.delete_by_page_id(page_id)
        assert deleted == 2

        hits = store.get_page_chunks(page_id)
        assert len(hits) == 0

        stats_after = store.stats()
        assert stats_after["total_chunks"] == 0

    def test_filter_by_page_ids(self, store: MilvusStore) -> None:
        page_a = _random_page_id()
        page_b = _random_page_id()
        store.upsert_page(
            page_a,
            [Chunk(text="Page A content.", metadata=_make_meta(page_a, "Page A", 0))],
            [],
            [_dense_vector(1.0)],
        )
        store.upsert_page(
            page_b,
            [Chunk(text="Page B content.", metadata=_make_meta(page_b, "Page B", 0))],
            [],
            [_dense_vector(0.0)],
        )

        hits = store.search_dense(_dense_vector(1.0), limit=10, filters={"page_ids": [page_a]})
        assert len(hits) == 1
        assert hits[0].metadata.page_id == page_a
