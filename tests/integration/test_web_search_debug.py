from __future__ import annotations

import tempfile
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from rag_notion_kb.models import (
    DebugSearchRequest,
    DebugSearchResponse,
    SearchHistory,
    SearchSource,
    SummarizeResponse,
)
from rag_notion_kb.storage.search_history_store import SearchHistoryStore
from rag_notion_kb.web.web_server import create_app


def _debug_response(query: str) -> DebugSearchResponse:
    return DebugSearchResponse(
        query=query,
        params=DebugSearchRequest(query=query),
        total_dense=2,
        total_sparse=1,
        total_after_filter=2,
        total_after_fusion=2,
        total_after_rerank=2,
        results=[],
        latency_ms=12,
    )


def test_search_debug_and_history_api() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        config = MagicMock()
        config.storage.data_dir = tmpdir
        search_service = MagicMock()
        search_service.debug_search.side_effect = (
            lambda request, **kwargs: _debug_response(request.query)
        )
        history_store = SearchHistoryStore(tmpdir + "/history.db")

        app = create_app(
            search_service=search_service,
            store=MagicMock(),
            state_store=MagicMock(),
            config=config,
            history_store=history_store,
        )
        try:
            client = TestClient(app)

            debug_resp = client.post(
                "/api/search/debug",
                json={
                    "query": "first",
                    "dense_weight": 0.7,
                    "sparse_weight": 0.3,
                    "top_k": 5,
                    "min_similarity": 0.4,
                },
            )
            assert debug_resp.status_code == 200
            assert debug_resp.json()["query"] == "first"
            assert search_service.debug_search.call_args.kwargs == {}

            debug_history = SearchHistory(
                    history_id="h1",
                    query="first",
                    source=SearchSource.DEBUG,
                    params={"query": "first", "dense_weight": 0.7},
                    result_summary={"total_results": 1, "top_score": 0.9},
                    created_at="2026-08-12T10:00:00Z",
            )
            debug_history.snapshot = {"query": "first", "results": []}
            history_store.add(debug_history)
            mcp_history = SearchHistory(
                    history_id="h2",
                    query="second",
                    source=SearchSource.MCP,
                    params={"query": "second", "expand_to_level": 2},
                    result_summary={"total_results": 2, "top_score": 0.8},
                    created_at="2026-08-12T11:00:00Z",
            )
            mcp_history.snapshot = {"query": "second", "results": []}
            history_store.add(mcp_history)

            listed = client.get("/api/search/history")
            assert listed.status_code == 200
            assert [item["history_id"] for item in listed.json()] == ["h2", "h1"]
            assert "snapshot" not in listed.json()[1]

            filtered = client.get("/api/search/history?source=mcp")
            assert filtered.status_code == 200
            assert [item["history_id"] for item in filtered.json()] == ["h2"]

            stats = client.get("/api/search/history/stats")
            assert stats.status_code == 200
            assert stats.json()["total_queries"] == 2
            assert stats.json()["by_source"] == {"debug": 1, "mcp": 1, "cli": 0}
            assert stats.json()["success_rate"] == 1.0
            assert stats.json()["zero_result_rate"] == 0.0
            assert stats.json()["top_score_p90"] == 0.9

            single = client.get("/api/search/history/h1")
            assert single.status_code == 200
            assert single.json()["snapshot"]["query"] == "first"

            replay = client.post("/api/search/history/h2/replay")
            assert replay.status_code == 200
            assert replay.json()["query"] == "second"
            assert search_service.debug_search.call_count == 1

            deleted = client.delete("/api/search/history/h1")
            assert deleted.status_code == 200
            assert deleted.json() == {"deleted": True}

            cleared = client.delete("/api/search/history?clear=all")
            assert cleared.status_code == 200
            assert cleared.json()["count"] == 1

            missing = client.get("/api/search/history/h1")
            assert missing.status_code == 404
        finally:
            history_store.close()


def test_search_debug_forwards_summarize_and_returns_summary() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        config = MagicMock()
        config.storage.data_dir = tmpdir
        search_service = MagicMock()

        def _summarized_response(request: DebugSearchRequest, **kwargs: object) -> DebugSearchResponse:
            return DebugSearchResponse(
                query=request.query,
                params=request,
                total_dense=1,
                total_sparse=0,
                total_after_filter=1,
                total_after_fusion=1,
                total_after_rerank=0,
                results=[],
                latency_ms=12,
                summary="**summary**",
            )

        search_service.debug_search.side_effect = _summarized_response
        history_store = SearchHistoryStore(tmpdir + "/history.db")
        app = create_app(
            search_service=search_service,
            store=MagicMock(),
            state_store=MagicMock(),
            config=config,
            history_store=history_store,
        )
        try:
            client = TestClient(app)
            response = client.post(
                "/api/search/debug",
                json={"query": "q", "summarize": True},
            )
            assert response.status_code == 200
            assert response.json()["summary"] == "**summary**"
            forwarded = search_service.debug_search.call_args.args[0]
            assert forwarded.summarize is True
        finally:
            history_store.close()


def test_summarize_endpoint_updates_history_snapshot() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        config = MagicMock()
        config.storage.data_dir = tmpdir
        search_service = MagicMock()
        search_service.summarize_for_web.return_value = SummarizeResponse(
            summary="**summary**",
            char_count=11,
            token_count=3,
            model="deepseek-v4-flash",
            duration_ms=145,
            source_char_count=20,
            source_token_count=6,
            compression_ratio=0.5,
        )
        history_store = SearchHistoryStore(tmpdir + "/history.db")
        history_store.add(
            SearchHistory(
                history_id="h-summary",
                query="q",
                source=SearchSource.DEBUG,
                params={"query": "q"},
                result_summary={"total_results": 1},
                snapshot={
                    "query": "q",
                    "results": [],
                    "params": {"query": "q", "summarize": False},
                },
                created_at="2026-08-12T10:00:00Z",
            )
        )

        app = create_app(
            search_service=search_service,
            store=MagicMock(),
            state_store=MagicMock(),
            config=config,
            history_store=history_store,
        )
        try:
            client = TestClient(app)
            response = client.post(
                "/api/search/summarize",
                json={
                    "query": "q",
                    "passages": [{"text": "passage", "source": "Page A"}],
                    "history_id": "h-summary",
                },
            )
            assert response.status_code == 200
            assert response.json()["summary"] == "**summary**"
            updated = history_store.get("h-summary")
            assert updated is not None
            assert updated.snapshot["summary"] == "**summary**"
            assert updated.snapshot["summary_model"] == "deepseek-v4-flash"
            assert updated.snapshot["summary_token_count"] == 3
            assert updated.snapshot["summary_duration_ms"] == 145
            assert updated.snapshot["summary_compression_ratio"] == 0.5
            assert updated.snapshot["params"]["summarize"] is True
        finally:
            history_store.close()
