from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rag_notion_kb.config import Settings
from rag_notion_kb.exceptions import RetrievalError, StorageError
from rag_notion_kb.models import (
    ChunkMetadata,
    ChunkType,
    ContextExpandMode,
    DebugSearchRequest,
    SearchHit,
    SearchSource,
)
from rag_notion_kb.services.search_service import SearchService
from rag_notion_kb.storage.search_history_store import SearchHistoryStore


def _hit(
    hit_id: int,
    chunk_text: str,
    score: float,
    header_level: int = 2,
) -> SearchHit:
    return SearchHit(
        id=hit_id,
        chunk_text=chunk_text,
        score=score,
        metadata=ChunkMetadata(
            page_id=f"page-{hit_id}",
            page_title=f"Title {hit_id}",
            page_url=f"https://notion.so/page-{hit_id}",
            header_path="# Root > ## Section",
            header_level=header_level,
            last_edited_time="2026-08-10T10:00:00Z",
            chunk_index=hit_id,
            chunk_type=ChunkType.TEXT,
        ),
    )


def _service(
    tmp_path: Path,
    *,
    dense_hits: list[SearchHit],
    sparse_hits: list[SearchHit],
    reranked: list[tuple[int, float]] | None = None,
    rerank_error: bool = False,
) -> tuple[SearchService, SearchHistoryStore, MagicMock]:
    history_store = SearchHistoryStore(tmp_path / "history.db")
    embedding = MagicMock()
    embedding.embed.return_value = [[1.0]]
    reranker = MagicMock()
    if rerank_error:
        reranker.rerank.side_effect = RetrievalError("reranker unavailable")
    elif reranked is not None:
        reranker.rerank.return_value = reranked

    store = MagicMock()
    store.search_dense.return_value = dense_hits
    store.search_sparse.return_value = sparse_hits
    store.get_page_chunks.return_value = []
    state_store = MagicMock()
    service = SearchService(
        embedding=embedding,
        reranker=reranker,
        store=store,
        state_store=state_store,
        config=Settings(),
        history_store=history_store,
    )
    return service, history_store, reranker


def test_debug_search_exposes_stage_scores(tmp_path: Path) -> None:
    dense = [_hit(1, "overlap", 0.8), _hit(2, "filtered", 0.2)]
    sparse = [_hit(3, "sparse only", 0.7), _hit(1, "overlap", 0.6)]
    service, history_store, reranker = _service(
        tmp_path,
        dense_hits=dense,
        sparse_hits=sparse,
        reranked=[(1, 0.95), (0, 0.40)],
    )

    response = service.debug_search(
        DebugSearchRequest(
            query="test",
            dense_weight=0.3,
            sparse_weight=0.7,
            min_similarity=0.3,
            top_k=2,
            rerank_model="test-model",
        )
    )

    assert response.error is None
    assert response.total_dense == 2
    assert response.total_sparse == 2
    assert response.total_after_filter == 2
    assert response.total_after_fusion == 2
    assert response.total_after_rerank == 2
    assert [hit.page_id for hit in response.results] == ["page-1", "page-3"]
    assert response.results[0].scores.dense_score == pytest.approx(0.8)
    assert response.results[0].scores.sparse_score == pytest.approx(0.6)
    assert response.results[0].scores.rrf_score == pytest.approx(0.66)
    assert response.results[0].scores.rerank_score == pytest.approx(0.95)
    assert response.results[0].scores.final_score == pytest.approx(0.95)
    assert response.results[1].scores.dense_score is None
    assert response.results[1].scores.sparse_score == pytest.approx(0.7)
    assert response.results[1].scores.rrf_score == pytest.approx(0.7)
    reranker.rerank.assert_called_once()
    assert reranker.rerank.call_args.kwargs["model"] == "test-model"

    history = history_store.list_recent(source=SearchSource.DEBUG)
    assert len(history) == 1
    assert history[0].query == "test"
    assert history[0].result_summary["total_results"] == 2
    assert history[0].result_summary["min_score"] == pytest.approx(0.4)
    assert history[0].result_summary["max_score"] == pytest.approx(0.95)
    assert history[0].snapshot is not None
    assert len(history[0].snapshot["results"]) == 2


def test_debug_search_rerank_failure_falls_back_to_fusion(tmp_path: Path) -> None:
    service, _, _ = _service(
        tmp_path,
        dense_hits=[_hit(1, "one", 0.8)],
        sparse_hits=[_hit(2, "two", 0.7)],
        rerank_error=True,
    )

    response = service.debug_search(
        DebugSearchRequest(query="test", dense_weight=0.5, sparse_weight=0.5)
    )

    assert response.error is None
    assert response.results[0].scores.rerank_score is None
    assert response.results[0].scores.final_score == pytest.approx(0.8)


def test_debug_search_empty_query_is_noop(tmp_path: Path) -> None:
    service, history_store, _ = _service(
        tmp_path,
        dense_hits=[],
        sparse_hits=[],
        reranked=[],
    )

    response = service.debug_search(DebugSearchRequest(query="   "))

    assert response.results == []
    assert response.latency_ms >= 0
    assert history_store.list_recent() == []
    service.embedding.embed.assert_not_called()


def test_context_modes_map_to_expansion_levels() -> None:
    assert SearchService._expand_level(ContextExpandMode.NONE, 3) == 100
    assert SearchService._expand_level(ContextExpandMode.PARENT, 3) == 2
    assert SearchService._expand_level(ContextExpandMode.H2, 5) == 2


def test_candidate_limit_uses_one_point_three_redundancy() -> None:
    assert SearchService._candidate_limit(1) == 2
    assert SearchService._candidate_limit(10) == 13
    assert SearchService._candidate_limit(100) == 130


def test_debug_search_expands_before_dedup(tmp_path: Path) -> None:
    dense = [_hit(1, "first", 0.9), _hit(2, "second", 0.8), _hit(3, "third", 0.7)]
    service, history_store, reranker = _service(
        tmp_path,
        dense_hits=dense,
        sparse_hits=[],
        reranked=[(0, 0.95), (1, 0.90), (2, 0.85)],
    )
    service.expander.expand = MagicMock(
        side_effect=["expanded-same", "expanded-same", "expanded-unique"]
    )

    response = service.debug_search(
        DebugSearchRequest(
            query="dedup",
            dense_weight=1.0,
            sparse_weight=0.0,
            min_similarity=0.0,
            top_k=2,
            context_mode=ContextExpandMode.H2,
        )
    )

    assert response.total_dense == 3
    assert response.total_after_rerank == 3
    assert [hit.page_id for hit in response.results] == ["page-1", "page-3"]
    assert [hit.expanded_text for hit in response.results] == [
        "expanded-same",
        "expanded-unique",
    ]
    assert [hit.rank for hit in response.results] == [1, 2]
    service.store.search_dense.assert_called_once()
    assert service.store.search_dense.call_args.kwargs["limit"] == 3


def test_production_search_dedups_expanded_text(tmp_path: Path) -> None:
    dense = [_hit(1, "first", 0.9), _hit(2, "second", 0.8), _hit(3, "third", 0.7)]
    service, history_store, _ = _service(
        tmp_path,
        dense_hits=dense,
        sparse_hits=[],
        reranked=[(0, 0.95), (1, 0.90), (2, 0.85)],
    )
    service.expander.expand = MagicMock(
        side_effect=["expanded-same", "expanded-same", "expanded-unique"]
    )

    results = service.search(
        "dedup",
        top_k=2,
        expand_to_level=2,
        history_source=SearchSource.CLI,
    )

    assert [result.source.page_id for result in results] == ["page-1", "page-3"]
    assert [result.text for result in results] == [
        "expanded-same",
        "expanded-unique",
    ]
    history = history_store.list_recent(source=SearchSource.CLI)
    assert len(history) == 1
    assert history[0].snapshot["total_dense"] == 3
    assert history[0].snapshot["total_sparse"] == 0
    assert history[0].snapshot["total_after_filter"] == 3
    assert history[0].snapshot["total_after_fusion"] == 3
    assert history[0].snapshot["total_after_rerank"] == 3
    assert history[0].snapshot["results"][0]["scores"]["dense_score"] == pytest.approx(0.9)
    assert history[0].snapshot["results"][0]["scores"]["rerank_score"] == pytest.approx(0.95)
    assert history[0].snapshot["results"][1]["scores"]["dense_score"] == pytest.approx(0.7)
    assert history[0].snapshot["results"][1]["scores"]["rerank_score"] == pytest.approx(0.85)
    assert history[0].result_summary["min_score"] >= 0
    assert history[0].result_summary["max_score"] <= 1


def test_production_sparse_only_history_has_nonnegative_score_range(tmp_path: Path) -> None:
    sparse = [_hit(1, "worst", -8.6), _hit(2, "best", -5.0)]
    service, history_store, _ = _service(
        tmp_path,
        dense_hits=[],
        sparse_hits=sparse,
        rerank_error=True,
    )

    service.search(
        "keyword",
        top_k=2,
        expand_to_level=2,
        history_source=SearchSource.CLI,
    )

    history = history_store.list_recent(source=SearchSource.CLI)
    assert history[0].result_summary["min_score"] == pytest.approx(0.0)
    assert history[0].result_summary["max_score"] == pytest.approx(1.0)


def test_production_search_can_skip_rerank(tmp_path: Path) -> None:
    dense = [_hit(1, "first", 0.9), _hit(2, "second", 0.8)]
    service, history_store, reranker = _service(
        tmp_path,
        dense_hits=dense,
        sparse_hits=[],
        reranked=[(0, 0.95), (1, 0.90)],
    )

    service.search(
        "keyword",
        top_k=2,
        expand_to_level=2,
        history_source=SearchSource.CLI,
        rerank=False,
    )

    reranker.rerank.assert_not_called()
    history = history_store.list_recent(source=SearchSource.CLI)
    assert history[0].snapshot["total_after_rerank"] == 0
    assert all(
        hit["scores"]["rerank_score"] is None
        for hit in history[0].snapshot["results"]
    )


def test_debug_search_failure_is_recorded(tmp_path: Path) -> None:
    service, history_store, _ = _service(
        tmp_path,
        dense_hits=[],
        sparse_hits=[],
        reranked=[],
    )
    service.store.search_dense.side_effect = StorageError("dense unavailable")

    response = service.debug_search(DebugSearchRequest(query="failing query"))

    assert response.error == "dense unavailable"
    history = history_store.list_recent(source=SearchSource.DEBUG)
    assert len(history) == 1
    assert history[0].result_summary["success"] is False
    assert history[0].result_summary["error"] == "dense unavailable"


def test_production_search_failure_records_history_then_raises(tmp_path: Path) -> None:
    service, history_store, _ = _service(
        tmp_path,
        dense_hits=[],
        sparse_hits=[],
        reranked=[],
    )
    service.store.search_dense.side_effect = StorageError("dense unavailable")

    with pytest.raises(RetrievalError):
        service.search(
            "failing query",
            history_source=SearchSource.CLI,
        )

    history = history_store.list_recent(source=SearchSource.CLI)
    assert len(history) == 1
    assert history[0].result_summary["success"] is False
    assert history[0].result_summary["error"] == "dense unavailable"


def test_dense_zero_weight_uses_only_sparse_hits(tmp_path: Path) -> None:
    service, _, _ = _service(
        tmp_path,
        dense_hits=[_hit(1, "dense only", 0.8)],
        sparse_hits=[_hit(2, "keyword match", -2.5)],
        reranked=[(0, 0.9)],
    )

    response = service.debug_search(
        DebugSearchRequest(
            query="keyword",
            dense_weight=0.0,
            sparse_weight=1.0,
            min_similarity=0.5,
            top_k=1,
            context_mode=ContextExpandMode.NONE,
        )
    )

    assert response.total_dense == 0
    assert response.total_sparse == 1
    assert [hit.page_id for hit in response.results] == ["page-2"]
    assert response.results[0].scores.dense_score is None
    assert response.results[0].scores.sparse_score == pytest.approx(1.0)
    assert response.results[0].scores.rrf_score == pytest.approx(1.0)


def test_sparse_negative_scores_are_normalized(tmp_path: Path) -> None:
    dense = []
    sparse = [_hit(1, "worst", -8.6), _hit(2, "best", -5.0)]
    service, _, _ = _service(
        tmp_path,
        dense_hits=dense,
        sparse_hits=sparse,
        reranked=[(0, 0.7), (1, 0.9)],
    )

    response = service.debug_search(
        DebugSearchRequest(
            query="keyword",
            dense_weight=0.0,
            sparse_weight=1.0,
            min_similarity=0.5,
            top_k=2,
            context_mode=ContextExpandMode.NONE,
        )
    )

    sparse_scores = {hit.page_id: hit.scores.sparse_score for hit in response.results}
    assert sparse_scores["page-1"] == pytest.approx(1.0)
    assert sparse_scores["page-2"] == pytest.approx(0.0)
    rrf_scores = {hit.page_id: hit.scores.rrf_score for hit in response.results}
    assert rrf_scores["page-1"] == pytest.approx(1.0)
    assert rrf_scores["page-2"] == pytest.approx(0.0)


def test_identical_sparse_scores_use_ranked_normalization(tmp_path: Path) -> None:
    dense = []
    sparse = [_hit(1, "first", -8.6), _hit(2, "second", -8.6), _hit(3, "third", -8.6)]
    service, _, _ = _service(
        tmp_path,
        dense_hits=dense,
        sparse_hits=sparse,
        reranked=[(0, 0.7), (1, 0.8), (2, 0.9)],
    )

    response = service.debug_search(
        DebugSearchRequest(
            query="keyword",
            dense_weight=0.0,
            sparse_weight=1.0,
            top_k=3,
            context_mode=ContextExpandMode.NONE,
        )
    )

    assert [hit.scores.sparse_score for hit in response.results] == pytest.approx(
        [1.0, 0.5, 0.0]
    )
