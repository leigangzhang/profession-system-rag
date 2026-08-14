"""E2E tests for the MCP server `MCPServer`.

Tests cover all four tools: rag_search, rag_sync, rag_stats, rag_page_detail.
Uses mock SyncService and SearchService to control test data.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from rag_notion_kb.config import Settings
from rag_notion_kb.mcp_server import MCPServer
from rag_notion_kb.models import (
    ChunkMetadata,
    ChunkType,
    SearchResult,
    SyncResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def mock_sync_service() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_search_service() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mcp_server(mock_sync_service: MagicMock, mock_search_service: MagicMock, settings: Settings) -> MCPServer:
    return MCPServer(
        sync_service=mock_sync_service,
        search_service=mock_search_service,
        config=settings,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_metadata(
    page_id: str = "page-1",
    page_title: str = "Test Page",
    page_url: str = "https://notion.so/page-1",
    header_path: str = "# Section 1",
    header_level: int = 1,
) -> ChunkMetadata:
    return ChunkMetadata(
        page_id=page_id,
        page_title=page_title,
        page_url=page_url,
        header_path=header_path,
        header_level=header_level,
        last_edited_time="2026-08-10T10:00:00Z",
        chunk_index=0,
        chunk_type=ChunkType.TEXT,
    )


def _make_search_result(
    text: str = "Some content",
    score: float = 0.95,
    page_id: str = "page-1",
    page_title: str = "Test Page",
    matched_snippet: str | None = "exact match snippet",
) -> SearchResult:
    return SearchResult(
        text=text,
        score=score,
        source=_make_metadata(page_id=page_id, page_title=page_title),
        matched_snippet=matched_snippet,
    )


async def _call_tool(mcp_server: MCPServer, name: str, arguments: dict) -> str:
    """Call a tool by name and return the first text content."""
    content, _meta = await mcp_server._server.call_tool(name, arguments)
    return content[0].text


# ---------------------------------------------------------------------------
# rag_search tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rag_search_returns_results(mcp_server: MCPServer, mock_search_service: MagicMock) -> None:
    """Verify markdown output contains page titles, scores, and header paths."""
    mock_search_service.search.return_value = [
        _make_search_result(text="First result content", score=0.95, page_title="Alpha"),
        _make_search_result(
            text="Second result content",
            score=0.80,
            page_title="Beta",
            matched_snippet=None,
        ),
    ]

    result = await _call_tool(mcp_server, "rag_search", {"query": "test query"})

    assert "检索结果：test query" in result
    assert "Alpha" in result
    assert "Beta" in result
    assert "0.9500" in result
    assert "0.8000" in result
    assert "Section 1" in result  # header_path default
    assert "页面 ID" in result
    assert "text" in result  # chunk type
    assert "exact match snippet" in result  # matched_snippet appears


@pytest.mark.asyncio
async def test_rag_search_empty_results(mcp_server: MCPServer, mock_search_service: MagicMock) -> None:
    """Returns the 'no results' message when search yields nothing."""
    mock_search_service.search.return_value = []

    result = await _call_tool(mcp_server, "rag_search", {"query": "nothing"})

    assert "_未找到相关结果。_" in result


@pytest.mark.asyncio
async def test_rag_search_passes_page_ids_filter(
    mcp_server: MCPServer, mock_search_service: MagicMock
) -> None:
    """filters.page_ids is forwarded into the filters dict."""
    mock_search_service.search.return_value = [_make_search_result()]

    await _call_tool(
        mcp_server,
        "rag_search",
        {"query": "q", "filters": {"page_ids": ["page-a", "page-b"]}},
    )

    call_kwargs = mock_search_service.search.call_args.kwargs
    assert call_kwargs["filters"] == {"page_ids": ["page-a", "page-b"]}


@pytest.mark.asyncio
async def test_rag_search_passes_header_level_filter(
    mcp_server: MCPServer, mock_search_service: MagicMock
) -> None:
    """filters.header_level is forwarded into the filters dict."""
    mock_search_service.search.return_value = [_make_search_result()]

    await _call_tool(
        mcp_server,
        "rag_search",
        {"query": "q", "filters": {"header_level": 3}},
    )

    call_kwargs = mock_search_service.search.call_args.kwargs
    assert call_kwargs["filters"] == {"header_level": 3}


@pytest.mark.asyncio
async def test_rag_search_missing_optional_params_uses_defaults(
    mcp_server: MCPServer, mock_search_service: MagicMock
) -> None:
    """When optional params are omitted, Claude-friendly defaults flow."""
    mock_search_service.search.return_value = [_make_search_result()]

    await _call_tool(mcp_server, "rag_search", {"query": "just query"})

    call_kwargs = mock_search_service.search.call_args.kwargs
    assert call_kwargs["filters"] is None
    assert call_kwargs["top_k"] == 10
    assert call_kwargs["max_tokens"] == 4000
    assert call_kwargs["dense_weight"] == 0.5
    assert call_kwargs["sparse_weight"] == 0.5
    assert call_kwargs["min_similarity"] == 0.4
    assert call_kwargs["rerank"] is True
    assert call_kwargs["rerank_model"] == "qwen3-vl-rerank"
    assert call_kwargs["context_mode"] == "h2"


@pytest.mark.asyncio
async def test_rag_search_forwards_web_tuning_parameters(
    mcp_server: MCPServer, mock_search_service: MagicMock
) -> None:
    mock_search_service.search.return_value = [_make_search_result()]

    await _call_tool(
        mcp_server,
        "rag_search",
        {
            "query": "q",
            "search_mode": "sparse",
            "min_similarity": 0.4,
            "context_mode": "h2",
            "rerank": False,
        },
    )

    call_kwargs = mock_search_service.search.call_args.kwargs
    assert call_kwargs["dense_weight"] == 0.0
    assert call_kwargs["sparse_weight"] == 1.0
    assert call_kwargs["min_similarity"] == 0.4
    assert call_kwargs["context_mode"] == "h2"
    assert call_kwargs["rerank"] is False


@pytest.mark.asyncio
async def test_rag_search_merges_page_id_shortcut_with_filters(
    mcp_server: MCPServer, mock_search_service: MagicMock
) -> None:
    """page_id is merged with filters.page_ids without duplicate IDs."""
    mock_search_service.search.return_value = [_make_search_result()]

    await _call_tool(
        mcp_server,
        "rag_search",
        {
            "query": "q",
            "page_id": "page-a",
            "filters": {"page_ids": ["page-a", "page-b"]},
        },
    )

    call_kwargs = mock_search_service.search.call_args.kwargs
    assert call_kwargs["filters"] == {"page_ids": ["page-a", "page-b"]}


@pytest.mark.asyncio
async def test_rag_search_passes_typed_metadata_filters(
    mcp_server: MCPServer, mock_search_service: MagicMock
) -> None:
    """All typed metadata fields are forwarded unchanged."""
    mock_search_service.search.return_value = [_make_search_result()]

    await _call_tool(
        mcp_server,
        "rag_search",
        {
            "query": "q",
            "filters": {
                "page_title": ["Alpha"],
                "chunk_type": ["text", "table"],
                "edited_after": "2026-08-01",
            },
        },
    )

    call_kwargs = mock_search_service.search.call_args.kwargs
    assert call_kwargs["filters"] == {
        "page_title": ["Alpha"],
        "chunk_type": ["text", "table"],
        "edited_after": "2026-08-01",
    }


@pytest.mark.asyncio
async def test_rag_search_page_size_maps_to_top_k(
    mcp_server: MCPServer, mock_search_service: MagicMock
) -> None:
    """page_size is used as the backend top_k."""
    mock_search_service.search.return_value = [_make_search_result()]

    await _call_tool(
        mcp_server,
        "rag_search",
        {"query": "q", "page_size": 7},
    )

    assert mock_search_service.search.call_args.kwargs["top_k"] == 7


@pytest.mark.asyncio
async def test_rag_search_highlight_length_is_applied(
    mcp_server: MCPServer, mock_search_service: MagicMock
) -> None:
    """max_highlight_length controls snippet inclusion and truncation."""
    mock_search_service.search.return_value = [
        _make_search_result(matched_snippet="0123456789")
    ]

    truncated = await _call_tool(
        mcp_server,
        "rag_search",
        {"query": "q", "max_highlight_length": 5},
    )
    assert "01234" in truncated
    assert "0123456789" not in truncated

    omitted = await _call_tool(
        mcp_server,
        "rag_search",
        {"query": "q", "max_highlight_length": 0},
    )
    assert "原文匹配" not in omitted


@pytest.mark.asyncio
async def test_rag_search_exposes_claude_friendly_schema(
    mcp_server: MCPServer,
) -> None:
    """The public MCP tool schema exposes guidance, defaults, and typed filters."""
    tools = await mcp_server._server.list_tools()
    tool = next(item for item in tools if item.name == "rag_search")
    schema = tool.inputSchema
    properties = schema["properties"]
    filters = schema["$defs"]["RagSearchFilters"]["properties"]

    assert schema["required"] == ["query"]
    assert "page_size" in properties
    assert "max_highlight_length" in properties
    assert "search_mode" in properties
    assert properties["page_size"]["default"] == 10
    assert properties["page_size"]["maximum"] == 25
    assert properties["search_mode"]["enum"] == ["hybrid", "dense", "sparse"]
    assert set(filters) == {
        "page_ids",
        "header_level",
        "chunk_type",
        "page_title",
        "edited_after",
    }
    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.openWorldHint is True
    assert "ranked passages with citations" in tool.description


# ---------------------------------------------------------------------------
# rag_sync tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rag_sync_returns_summary(mcp_server: MCPServer, mock_sync_service: MagicMock) -> None:
    """Sync returns SyncResult with counts → verify summary string format."""
    mock_sync_service.sync.return_value = (
        SyncResult(added=3, updated=2, removed=1, skipped=4, failed=0),
        None,
    )

    result = await _call_tool(mcp_server, "rag_sync", {})

    assert "新增：3" in result
    assert "更新：2" in result
    assert "删除：1" in result
    assert "跳过：4" in result
    assert "失败：0" in result


@pytest.mark.asyncio
async def test_rag_sync_passes_root_comma_separated(
    mcp_server: MCPServer, mock_sync_service: MagicMock
) -> None:
    """root parameter (comma-separated IDs) → root_page_ids parsed correctly."""
    mock_sync_service.sync.return_value = (SyncResult(), None)

    await _call_tool(mcp_server, "rag_sync", {"root": "id1, id2 ,id3"})

    call_kwargs = mock_sync_service.sync.call_args.kwargs
    assert call_kwargs["root_page_ids"] == ["id1", "id2", "id3"]


@pytest.mark.asyncio
async def test_rag_sync_passes_full_true(mcp_server: MCPServer, mock_sync_service: MagicMock) -> None:
    """When full=True, force_full is True in the sync call."""
    mock_sync_service.sync.return_value = (SyncResult(), None)

    await _call_tool(mcp_server, "rag_sync", {"full": True})

    call_kwargs = mock_sync_service.sync.call_args.kwargs
    assert call_kwargs["force_full"] is True


# ---------------------------------------------------------------------------
# rag_stats tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rag_stats_includes_all_fields(
    mcp_server: MCPServer, mock_search_service: MagicMock
) -> None:
    """Verify output includes total_chunks, total_pages, synced_pages, failed_pages, last_synced_time."""
    mock_search_service.stats.return_value = {
        "total_chunks": 150,
        "total_pages": 12,
        "synced_pages": 10,
        "failed_pages": 2,
        "last_synced_time": "2026-08-10T08:00:00Z",
    }

    result = await _call_tool(mcp_server, "rag_stats", {})

    assert "总 chunks 数：150" in result
    assert "总页面数：12" in result
    assert "已同步页面：10" in result
    assert "失败页面：2" in result
    assert "最近同步时间：2026-08-10T08:00:00Z" in result


# ---------------------------------------------------------------------------
# rag_page_detail tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rag_page_detail_valid_page_id(
    mcp_server: MCPServer, mock_search_service: MagicMock
) -> None:
    """Valid page_id returns text and metadata with page title and url."""
    meta = _make_metadata(
        page_id="page-42",
        page_title="My Awesome Page",
        page_url="https://notion.so/page-42",
    )
    mock_search_service.get_page_detail.return_value = ("Full page text here.", meta)

    result = await _call_tool(mcp_server, "rag_page_detail", {"page_id": "page-42"})

    assert "My Awesome Page" in result
    assert "https://notion.so/page-42" in result
    assert "Full page text here." in result
    assert "2026-08-10T10:00:00Z" in result


@pytest.mark.asyncio
async def test_rag_page_detail_invalid_page_id(
    mcp_server: MCPServer, mock_search_service: MagicMock
) -> None:
    """Invalid page_id returns the not-found message."""
    mock_search_service.get_page_detail.return_value = None

    result = await _call_tool(mcp_server, "rag_page_detail", {"page_id": "nonexistent"})

    assert "页面 nonexistent 未找到。" in result
