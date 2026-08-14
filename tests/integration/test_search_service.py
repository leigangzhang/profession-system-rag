from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rag_notion_kb.config import Settings
from rag_notion_kb.exceptions import RetrievalError
from rag_notion_kb.models import (
    Chunk,
    ChunkMetadata,
    ChunkType,
    PageMetadata,
    PageSyncState,
)
from rag_notion_kb.services.search_service import SearchService
from rag_notion_kb.storage.milvus_store import MilvusStore
from rag_notion_kb.storage.sync_state import SyncStateStore


def _make_service(deps: dict) -> SearchService:
    return SearchService(
        embedding=deps["embedding"],
        reranker=deps["reranker"],
        store=deps["store"],
        state_store=deps["state_store"],
        config=deps["config"],
    )


def _make_chunk(page_id: str, title: str, text: str, chunk_index: int) -> Chunk:
    return Chunk(
        text=text,
        metadata=ChunkMetadata(
            page_id=page_id,
            page_title=title,
            page_url=f"https://notion.so/{page_id}",
            header_path=f"# {title}",
            header_level=1,
            last_edited_time="2026-08-10T10:00:00Z",
            chunk_index=chunk_index,
            chunk_type=ChunkType.TEXT,
            image_url=None,
        ),
    )


def _zero_vec() -> list[float]:
    return [0.0] * 2048


def _unit_vec(dim: int) -> list[float]:
    vec = _zero_vec()
    vec[dim] = 1.0
    return vec


@pytest.fixture
def deps(tmp_path: Path) -> dict:
    store = MilvusStore(uri=tmp_path / "test.db", dim=2048)
    store.init_collection()
    state_store = SyncStateStore(db_path=tmp_path / "sync.db")
    config = Settings()
    return {
        "embedding": MagicMock(),
        "reranker": MagicMock(),
        "store": store,
        "state_store": state_store,
        "config": config,
    }


def _seed_kb(deps: dict) -> None:
    """Populate the vector store and state store with two pages."""
    store = deps["store"]
    state_store = deps["state_store"]

    chunks_p1 = [
        _make_chunk("p1", "Vector DB", "Vector databases store embeddings for retrieval.", 0),
    ]
    chunks_p2 = [
        _make_chunk("p2", "Relational DB", "Relational databases use tables and SQL.", 0),
    ]

    store.upsert_page("p1", chunks_p1, [], [_unit_vec(0)])
    store.upsert_page("p2", chunks_p2, [], [_unit_vec(1)])

    for page_id, title in [("p1", "Vector DB"), ("p2", "Relational DB")]:
        state_store.upsert(
            PageSyncState(
                page_id=page_id,
                page_title=title,
                page_url=f"https://notion.so/{page_id}",
                last_edited_time="2026-08-10T10:00:00Z",
                chunk_count=1,
                image_count=0,
                status="synced",
                last_synced_time="2026-08-10T10:00:00Z",
            )
        )


def test_search_returns_ranked_results(deps: dict) -> None:
    _seed_kb(deps)

    # Query vector aligns with p1's embedding.
    deps["embedding"].embed.return_value = [_unit_vec(0)]
    deps["reranker"].rerank.return_value = [(0, 0.95), (1, 0.30)]

    service = _make_service(deps)
    results = service.search("vector database", top_k=2, min_similarity=0.0)

    assert len(results) == 2
    assert results[0].source.page_id == "p1"
    assert "Vector DB" in results[0].source.page_title
    deps["embedding"].embed.assert_called_once()


def test_search_with_page_filter(deps: dict) -> None:
    _seed_kb(deps)

    deps["embedding"].embed.return_value = [_unit_vec(0)]
    deps["reranker"].rerank.return_value = [(0, 0.95)]

    service = _make_service(deps)
    results = service.search(
        "vector database",
        top_k=2,
        filters={"page_ids": ["p2"]},
        min_similarity=0.0,
    )

    assert all(r.source.page_id == "p2" for r in results)


def test_reranker_failure_falls_back_to_rrf(deps: dict) -> None:
    _seed_kb(deps)

    deps["embedding"].embed.return_value = [_unit_vec(0)]
    deps["reranker"].rerank.side_effect = RetrievalError("reranker unavailable")

    service = _make_service(deps)
    results = service.search("vector database", top_k=2, min_similarity=0.0)

    assert len(results) == 2
    # p1 should still rank first because dense vector matches query.
    assert results[0].source.page_id == "p1"


def test_stats_returns_counts(deps: dict) -> None:
    _seed_kb(deps)

    service = _make_service(deps)
    stats = service.stats()

    assert stats["total_chunks"] == 2
    assert stats["total_pages"] == 2
    assert stats["synced_pages"] == 2
    assert stats["failed_pages"] == 0
    assert stats["last_synced_time"] == "2026-08-10T10:00:00Z"


def test_get_page_detail_reconstructs_text(deps: dict) -> None:
    _seed_kb(deps)

    service = _make_service(deps)
    detail = service.get_page_detail("p1")

    assert detail is not None
    text, metadata = detail
    assert "Vector databases store embeddings" in text
    assert metadata.page_id == "p1"


def test_get_page_detail_missing_page(deps: dict) -> None:
    service = _make_service(deps)
    assert service.get_page_detail("missing") is None
