from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from rag_notion_kb.app_context import AppContext
from rag_notion_kb.config import Settings
from rag_notion_kb.models import SearchSource
from rag_notion_kb.services.search_service import SearchService
from rag_notion_kb.services.sync_service import SyncService

logger = logging.getLogger(__name__)

ContextMode = Literal["none", "parent", "h2"]
SearchMode = Literal["hybrid", "dense", "sparse"]

_SEARCH_MODE_WEIGHTS: dict[str, tuple[float, float]] = {
    "hybrid": (0.5, 0.5),
    "dense": (1.0, 0.0),
    "sparse": (0.0, 1.0),
}


class RagSearchFilters(BaseModel):
    """Typed metadata filters supported by the local RAG index."""

    model_config = ConfigDict(extra="forbid")

    page_ids: list[str] = Field(
        default_factory=list,
        description="Notion page IDs to restrict the search to.",
    )
    header_level: int | list[int] | None = Field(
        default=None,
        description=(
            "Restrict matches to one heading level (1-6) or a list of levels."
        ),
    )
    chunk_type: list[Literal["text", "table", "code", "image"]] = Field(
        default_factory=list,
        description="Only return matching chunk types.",
    )
    page_title: list[str] = Field(
        default_factory=list,
        description="Only return chunks from pages whose title is in this list.",
    )
    edited_after: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description=(
            "Only return chunks edited after this date. Use YYYY-MM-DD."
        ),
    )


class MCPServer:
    """MCP stdio server exposing RAG tools to MCP-compatible hosts."""

    @classmethod
    def from_context(cls, ctx: AppContext) -> "MCPServer":
        """Create an MCP server from a fully wired AppContext."""
        return cls(
            sync_service=ctx.sync_service,
            search_service=ctx.search_service,
            config=ctx.settings,
        )

    def __init__(
        self,
        sync_service: SyncService,
        search_service: SearchService,
        config: Settings,
    ) -> None:
        self.sync_service = sync_service
        self.search_service = search_service
        self.config = config
        self._server = FastMCP("rag_notion_kb")
        self._register_tools()

    @staticmethod
    def _merge_filters(
        filters: RagSearchFilters | None,
        page_id: str | None,
    ) -> dict[str, Any] | None:
        merged: dict[str, Any] = {}
        if filters is not None:
            for field, value in filters.model_dump(exclude_none=True).items():
                if isinstance(value, list):
                    if value:
                        merged[field] = value
                elif value is not None:
                    merged[field] = value

        if page_id:
            page_ids = merged.get("page_ids", [])
            if not isinstance(page_ids, list):
                page_ids = [page_ids]
            if page_id not in page_ids:
                merged["page_ids"] = [*page_ids, page_id]

        return merged or None

    @staticmethod
    def _render_result(
        result: Any,
        index: int,
        max_highlight_length: int,
    ) -> list[str]:
        source = result.source
        lines = [
            f"### 结果 {index}",
            "",
            f"- **页面**：[{source.page_title}]({source.page_url})",
            f"- **页面 ID**：`{source.page_id}`",
            f"- **位置**：{source.header_path or '未分类'}",
            f"- **分块类型**：{source.chunk_type.value}",
            f"- **相关度**：{result.score:.4f}",
        ]
        if max_highlight_length > 0 and result.matched_snippet:
            highlight = result.matched_snippet[:max_highlight_length]
            lines.extend(["", f"> 原文匹配：{highlight}"])
        lines.extend(["", result.text, "", "---", ""])
        return lines

    def _register_tools(self) -> None:
        server = self._server

        @server.tool(  # type: ignore[unused-call]
            annotations=ToolAnnotations(
                readOnlyHint=True,
                openWorldHint=True,
            )
        )
        async def rag_search(
            query: Annotated[
                str,
                Field(
                    min_length=1,
                    description=(
                        "Semantic search question. Keep it to one question or "
                        "topic per call for the best recall."
                    ),
                ),
            ],
            page_size: Annotated[
                int,
                Field(
                    default=10,
                    ge=1,
                    le=25,
                    description=(
                        "Maximum number of passages to return. Lower values "
                        "keep the response smaller."
                    ),
                ),
            ] = 10,
            max_highlight_length: Annotated[
                int,
                Field(
                    default=200,
                    ge=0,
                    le=2000,
                    description=(
                        "Maximum characters for each original-match highlight. "
                        "Use 0 to omit highlights."
                    ),
                ),
            ] = 200,
            search_mode: Annotated[
                SearchMode,
                Field(
                    description=(
                        "Search backend: hybrid (recommended), dense "
                        "(semantic only), or sparse (keyword only)."
                    ),
                ),
            ] = "hybrid",
            filters: Annotated[
                RagSearchFilters | None,
                Field(
                    description=(
                        "Optional metadata filters. Supported fields: "
                        "page_ids, header_level, chunk_type, page_title, and "
                        "edited_after."
                    ),
                ),
            ] = None,
            page_id: Annotated[
                str | None,
                Field(
                    description=(
                        "Optional Notion page ID or UUID to search within. "
                        "Use filters.page_ids when searching multiple pages."
                    ),
                ),
            ] = None,
            context_mode: Annotated[
                ContextMode,
                Field(
                    description=(
                        "How much surrounding content to return with each "
                        "match: none, parent heading, or up to the h2 section."
                    ),
                ),
            ] = "h2",
            max_tokens: Annotated[
                int,
                Field(
                    default=4000,
                    ge=100,
                    le=8000,
                    description="Maximum tokens for each returned passage.",
                ),
            ] = 4000,
            rerank: Annotated[
                bool,
                Field(
                    description=(
                        "Use the reranker to improve result ordering. Keep "
                        "enabled for answer-quality tasks."
                    ),
                ),
            ] = True,
            min_similarity: Annotated[
                float,
                Field(
                    default=0.4,
                    ge=0.0,
                    le=1.0,
                    description=(
                        "Minimum dense vector similarity. Lower it only when "
                        "results are too few."
                    ),
                ),
            ] = 0.4,
            rerank_model: Annotated[
                str,
                Field(
                    description=(
                        "ReRank model. The default model is suitable for "
                        "almost all requests."
                    ),
                ),
            ] = "qwen3-vl-rerank",
        ) -> list[TextContent]:
            """Search the local Notion knowledge base and return ranked passages with citations.

            This is the primary discovery tool. Use it when you need passages
            to read, quote, verify, expand, or fetch in full. Make separate
            calls for separate questions or topics. Omit metadata filters for
            broad questions; add them only when the request names a page,
            heading level, chunk type, page title, or date.

            ``rag_page_detail`` is available when you want the complete page
            after finding a promising passage.
            """
            dense_weight, sparse_weight = _SEARCH_MODE_WEIGHTS[search_mode]
            merged_filters = self._merge_filters(filters, page_id)
            results = self.search_service.search(
                query=query,
                top_k=page_size,
                max_tokens=max_tokens,
                filters=merged_filters,
                history_source=SearchSource.MCP,
                rerank=rerank,
                dense_weight=dense_weight,
                sparse_weight=sparse_weight,
                min_similarity=min_similarity,
                rerank_model=rerank_model,
                context_mode=context_mode,
            )

            if not results:
                return [
                    TextContent(
                        type="text",
                        text=(
                            "_未找到相关结果。_ 可以尝试缩短或改写 query、去掉 "
                            "filters，或先用 `rag_stats` 确认知识库已经同步。"
                        ),
                    )
                ]

            lines = [f"## 检索结果：{query}", f"共 {len(results)} 条结果。", ""]
            for index, result in enumerate(results, start=1):
                lines.extend(
                    self._render_result(result, index, max_highlight_length)
                )
            return [TextContent(type="text", text="\n".join(lines))]

        @server.tool()  # type: ignore[unused-call]
        async def rag_sync(
            root: Annotated[
                str | None,
                Field(
                    description=(
                        "Comma-separated Notion root page IDs. Omit to use the "
                        "roots from configuration."
                    ),
                ),
            ] = None,
            full: Annotated[
                bool,
                Field(
                    description=(
                        "Force a full re-sync. This can be slow and may call "
                        "embedding APIs many times."
                    ),
                ),
            ] = False,
        ) -> list[TextContent]:
            """Synchronize configured Notion pages into the local knowledge base.

            This is a mutating, potentially long-running operation. Prefer an
            incremental sync and use ``full=True`` only when the user explicitly
            asks for a complete refresh.
            """
            root_ids: list[str] | None = None
            if root:
                root_ids = [item.strip() for item in root.split(",") if item.strip()]

            result, _ = self.sync_service.sync(
                root_page_ids=root_ids,
                force_full=full,
            )
            summary = (
                f"同步完成 — 新增：{result.added}，更新：{result.updated}，"
                f"删除：{result.removed}，跳过：{result.skipped}，失败：{result.failed}"
            )
            return [TextContent(type="text", text=summary)]

        @server.tool(  # type: ignore[unused-call]
            annotations=ToolAnnotations(readOnlyHint=True)
        )
        async def rag_stats() -> list[TextContent]:
            """Return the current local knowledge base health and sync summary.

            Use this before diagnosing empty or stale search results, or when
            the user asks about the size and status of the knowledge base.
            """
            stats = self.search_service.stats()
            lines = [
                f"- 总 chunks 数：{stats['total_chunks']}",
                f"- 总页面数：{stats['total_pages']}",
                f"- 已同步页面：{stats['synced_pages']}",
                f"- 失败页面：{stats['failed_pages']}",
                f"- 最近同步时间：{stats['last_synced_time'] or 'N/A'}",
            ]
            return [TextContent(type="text", text="\n".join(lines))]

        @server.tool(  # type: ignore[unused-call]
            annotations=ToolAnnotations(readOnlyHint=True)
        )
        async def rag_page_detail(
            page_id: Annotated[
                str,
                Field(
                    min_length=1,
                    description=(
                        "Notion page ID from a rag_search result. This is the "
                        "UUID, not the page title."
                    ),
                ),
            ],
        ) -> list[TextContent]:
            """Fetch and return the complete local copy of one Notion page.

            Use this after ``rag_search`` when the matching passage is not
            enough and you need the full page content.
            """
            detail = self.search_service.get_page_detail(page_id)
            if detail is None:
                return [
                    TextContent(
                        type="text",
                        text=f"页面 {page_id} 未找到。请确认页面已同步。",
                    )
                ]

            text, metadata = detail
            source_link = f"[{metadata.page_title}]({metadata.page_url})"
            lines = [
                f"## {metadata.page_title}",
                f"来源：{source_link}",
                f"页面 ID：`{page_id}`",
                f"最后编辑时间：{metadata.last_edited_time}",
                "",
                text,
            ]
            return [TextContent(type="text", text="\n".join(lines))]

    async def run_async(self) -> None:
        """Start the MCP server using stdio transport (async)."""
        logger.info("Starting MCP server rag_notion_kb")
        await self._server.run_stdio_async()

    def run(self) -> None:
        """Start the MCP server (synchronous wrapper)."""
        try:
            asyncio.run(self.run_async())
        except KeyboardInterrupt:
            logger.info("MCP server stopped")
        except Exception:
            logger.exception("MCP server fatal error")
            raise
