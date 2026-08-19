from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import uvicorn

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from rag_notion_kb.app_context import AppContext
from rag_notion_kb.config import Settings
from rag_notion_kb.exceptions import ConfigError, RagKbError
from rag_notion_kb.logging_setup import setup_logging
from rag_notion_kb.mcp_server import MCPServer
from rag_notion_kb.models import SearchSource
from rag_notion_kb.storage.milvus_store import validate_page_id
from rag_notion_kb.web.web_server import create_app

logger = logging.getLogger(__name__)

app = typer.Typer(help="RAG Notion KB — sync, search, and serve your Notion knowledge base.")
console = Console()


def _mask_key(key: str) -> str:
    """Mask an API key, showing only first 4 and last 4 characters."""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


def _build_settings() -> Settings:
    """Load and return Settings from environment and YAML config."""
    try:
        return Settings()
    except Exception as exc:
        console.print(f"[red]Failed to load settings: {exc}[/red]")
        raise typer.Exit(code=1)


@app.command()
def sync(
    root: str | None = typer.Option(None, help="Comma-separated root page IDs (falls back to config)"),
    full: bool = typer.Option(False, "--full", help="Force full re-index of all pages"),
    vectorize: bool = typer.Option(False, "--vectorize", help="Also vectorize all enabled pages after fetch"),
) -> None:
    """Synchronize Notion pages into the vector store (fetch only by default)."""
    root_ids: list[str] | None = None
    if root:
        root_ids = [r.strip() for r in root.split(",") if r.strip()]

    settings = _build_settings()

    try:
        with AppContext(settings=settings) as ctx:
            # Resolve root IDs: --root > SQLite NotionRootStore > config.yaml
            if not root_ids:
                try:
                    root_ids = ctx.root_store.list_active_ids()
                except Exception:
                    root_ids = []
                if not root_ids:
                    root_ids = ctx.settings.notion.root_page_ids
            if not root_ids:
                console.print(
                    "[red]No root page IDs provided. Use --root, add roots via Web UI, "
                    "or configure notion.root_page_ids.[/red]"
                )
                raise typer.Exit(code=1)

            fetch_result, vec_result = ctx.sync_service.sync(
                root_page_ids=root_ids, force_full=full, vectorize=vectorize
            )

            table = Table(title="Sync Summary (Fetch)", show_header=True, header_style="bold green")
            table.add_column("Added", style="cyan", justify="right")
            table.add_column("Updated", style="yellow", justify="right")
            table.add_column("Removed", style="magenta", justify="right")
            table.add_column("Skipped", style="dim", justify="right")
            table.add_column("Failed", style="red", justify="right")
            table.add_row(
                str(fetch_result.added),
                str(fetch_result.updated),
                str(fetch_result.removed),
                str(fetch_result.skipped),
                str(fetch_result.failed),
            )
            console.print(table)

            if vec_result is not None:
                console.print()
                vec_table = Table(title="Vectorize Summary", show_header=True, header_style="bold blue")
                vec_table.add_column("Indexed", style="green", justify="right")
                vec_table.add_column("Failed", style="red", justify="right")
                vec_table.add_column("Skipped", style="dim", justify="right")
                vec_table.add_row(str(vec_result.indexed), str(vec_result.failed), str(vec_result.skipped))
                console.print(vec_table)

            if fetch_result.zero_vector_chunks > 0:
                console.print()
                degraded_table = Table(
                    title=f"[yellow]Degraded Pages ({fetch_result.zero_vector_chunks} zero-vector chunks across {len(fetch_result.degraded_page_ids)} pages)[/yellow]",
                    show_header=True,
                    header_style="yellow",
                )
                degraded_table.add_column("#", style="dim", justify="right")
                degraded_table.add_column("Page ID", style="yellow")
                for idx, pid in enumerate(fetch_result.degraded_page_ids, start=1):
                    degraded_table.add_row(str(idx), pid)
                console.print(degraded_table)
                console.print(
                    "[dim]Tip: re-run [bold]rag-kb sync --full[/bold] to retry these pages.[/dim]"
                )
    except ConfigError as exc:
        console.print(f"[red]Configuration error: {exc}[/red]")
        raise typer.Exit(code=1)
    except RagKbError as exc:
        console.print(f"[red]Sync failed: {exc}[/red]")
        raise typer.Exit(code=1)


@app.command()
def status() -> None:
    """Show knowledge base statistics."""
    settings = _build_settings()

    try:
        with AppContext(settings=settings) as ctx:
            stats = ctx.search_service.stats()

            table = Table(title="Knowledge Base Status", show_header=True, header_style="bold green")
            table.add_column("Metric", style="cyan")
            table.add_column("Value", style="white")
            table.add_row("Total Chunks", str(stats["total_chunks"]))
            table.add_row("Total Pages", str(stats["total_pages"]))
            table.add_row("Synced Pages", str(stats["synced_pages"]))
            table.add_row("Failed Pages", str(stats["failed_pages"]))
            table.add_row("Last Sync Time", str(stats["last_synced_time"] or "N/A"))
            console.print(table)
    except RagKbError as exc:
        console.print(f"[red]Failed to retrieve status: {exc}[/red]")
        raise typer.Exit(code=1)


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    top_k: int = typer.Option(5, "--top-k", min=1, max=100, help="Number of results"),
    expand_to_level: int = typer.Option(2, "--expand-level", min=1, max=6, help="Context expansion level"),
    max_tokens: int = typer.Option(4000, "--max-tokens", min=100, max=8000, help="Maximum tokens per result"),
    page_ids: str | None = typer.Option(None, "--page-ids", help="Comma-separated Notion page IDs"),
    header_level: int | None = typer.Option(None, "--header-level", min=1, max=6, help="Filter by heading level"),
    dense_weight: float = typer.Option(0.5, "--dense-weight", min=0.0, max=1.0, help="Dense weight"),
    sparse_weight: float = typer.Option(0.5, "--sparse-weight", min=0.0, max=1.0, help="Sparse weight"),
    min_similarity: float = typer.Option(0.4, "--min-similarity", min=0.0, max=1.0, help="Minimum Dense similarity"),
    rerank_model: str = typer.Option("qwen3-vl-rerank", "--rerank-model", help="ReRank model"),
    context_mode: str = typer.Option("h2", "--context-mode", help="none, parent, or h2"),
    chunk_type: str | None = typer.Option(None, "--chunk-type", help="Comma-separated chunk types"),
    page_title: str | None = typer.Option(None, "--page-title", help="Filter by page title"),
    skip_rerank: bool = typer.Option(False, "--skip-rerank", help="Skip ReRank"),
) -> None:
    """Search the knowledge base and record the query in search history."""
    settings = _build_settings()

    filters: dict[str, Any] = {}
    if page_ids:
        filters["page_ids"] = [item.strip() for item in page_ids.split(",") if item.strip()]
    if header_level is not None:
        filters["header_level"] = header_level
    if chunk_type:
        filters["chunk_type"] = [item.strip() for item in chunk_type.split(",") if item.strip()]
    if page_title:
        filters["page_title"] = [page_title]

    try:
        with AppContext(settings=settings) as ctx:
            results = ctx.search_service.search(
                query=query,
                top_k=top_k,
                expand_to_level=expand_to_level,
                max_tokens=max_tokens,
                filters=filters or None,
                history_source=SearchSource.CLI,
                rerank=not skip_rerank,
                dense_weight=dense_weight,
                sparse_weight=sparse_weight,
                min_similarity=min_similarity,
                rerank_model=rerank_model,
                context_mode=context_mode,
            )
    except RagKbError as exc:
        console.print(f"[red]Search failed: {exc}[/red]")
        raise typer.Exit(code=1)

    if not results:
        console.print("[yellow]No results found.[/yellow]")
        return

    for index, result in enumerate(results, start=1):
        title = (
            f"{index}. {result.source.page_title} "
            f"({result.score:.4f})"
        )
        console.print(
            Panel(
                result.text,
                title=title,
                subtitle=result.source.header_path,
                border_style="cyan",
            )
        )


@app.command()
def inspect(
    page_id: str = typer.Argument(..., help="Notion page ID to inspect"),
) -> None:
    """Inspect chunking and embedding results for a specific page."""
    settings = _build_settings()

    try:
        with AppContext(settings=settings) as ctx:
            if not validate_page_id(page_id):
                console.print(f"[red]Invalid page ID: {page_id}[/red]")
                raise typer.Exit(code=1)

            state = ctx.state_store.get(page_id)
            page_title = state.page_title if state is not None else page_id

            # Chunk results
            chunks = ctx.store.get_page_chunks(page_id)
            if not chunks:
                if state is None:
                    console.print(f"[yellow]Page {page_id} not found in sync state or vector store.[/yellow]")
                else:
                    console.print(
                        f"[yellow]Page {page_id} exists in sync state but has no vectors "
                        f"(status: {state.status}).[/yellow]"
                    )
                raise typer.Exit(code=1)

            # Embedding stats
            emb_stats = ctx.store.get_page_embedding_stats(page_id)

            # Summary panel
            sample_str = (
                f"[{', '.join(f'{v:.4f}' for v in emb_stats['sample_first5'])}...]"
                if emb_stats["sample_first5"]
                else "[dim]no vectors[/dim]"
            )
            summary = (
                f"[bold]Chunks:[/bold] {len(chunks)}  "
                f"[bold]Embeddings:[/bold] {emb_stats['nonzero']}/{emb_stats['total']} non-zero "
                f"([red]{emb_stats['zero']} zero[/red])  "
                f"[bold]Dim:[/bold] {emb_stats['dim']}  "
                f"[bold]Sample:[/bold] {sample_str}"
            )
            console.print()
            console.print(Panel(summary, title=f"[bold]Page: {page_title}[/bold]", border_style="cyan"))
            console.print()

            # Chunk details table
            table = Table(
                title="Chunk Details",
                show_header=True,
                header_style="bold green",
            )
            table.add_column("#", style="dim", justify="right", width=4)
            table.add_column("Type", style="cyan", width=6)
            table.add_column("Header Path", style="yellow", width=40)
            table.add_column("Text Preview", style="white", width=60)

            for chunk in chunks:
                text_preview = chunk.chunk_text[:120].replace("\n", " ").replace("\r", "")
                if len(chunk.chunk_text) > 120:
                    text_preview += "…"
                table.add_row(
                    str(chunk.metadata.chunk_index),
                    chunk.metadata.chunk_type.value,
                    chunk.metadata.header_path,
                    text_preview,
                )

            console.print(table)

            if emb_stats["zero"] > 0:
                console.print()
                console.print(
                    f"[yellow]Warning: {emb_stats['zero']} chunks have zero vectors "
                    f"(dense search disabled for these).[/yellow]"
                )
    except Exception as exc:
        console.print(f"[red]Inspect failed: {exc}[/red]")
        raise typer.Exit(code=1)


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", help="Host to bind the server to"),
    port: int = typer.Option(58000, help="Port to listen on"),
    open_browser: bool = typer.Option(False, "--open-browser", help="Open browser on start"),
    max_concurrent: int | None = typer.Option(None, "--max-concurrent", help="Max concurrent vectorize tasks (default: from config)"),
) -> None:
    """Start the Web UI for inspecting chunks and embeddings."""
    try:
        settings = _build_settings()
    except Exception as exc:
        console.print(f"[red]Failed to load settings: {exc}[/red]")
        raise typer.Exit(code=1)

    if open_browser:
        import webbrowser
        import threading

        def _open() -> None:
            import time
            time.sleep(1)
            webbrowser.open(f"http://{host}:{port}")
        threading.Thread(target=_open, daemon=True).start()

    try:
        with AppContext(settings=settings) as ctx:
            if max_concurrent is not None:
                from rag_notion_kb.services.vectorize_worker import VectorizeWorker
                ctx.worker = VectorizeWorker(
                    sync_service=ctx.sync_service,
                    state_store=ctx.state_store,
                    max_concurrent=max_concurrent,
                    poll_interval_seconds=ctx.settings.vectorize.poll_interval_seconds,
                )

            web_app = create_app(ctx)
            console.print(f"[green]Starting Web UI at http://{host}:{port}[/green]")
            uvicorn.run(web_app, host=host, port=port, log_level="info")
    except ConfigError as exc:
        console.print(f"[red]Configuration error: {exc}[/red]")
        raise typer.Exit(code=1)
    except RagKbError as exc:
        console.print(f"[red]Server initialization failed: {exc}[/red]")
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        console.print("[dim]Server stopped.[/dim]")
    except Exception as exc:
        console.print(f"[red]Unexpected error: {exc}[/red]")
        raise typer.Exit(code=1)


@app.command()
def serve() -> None:
    """Start the MCP server on stdio."""
    settings = _build_settings()
    setup_logging(
        level=settings.logging.level,
        fmt=settings.logging.format,
        stream=sys.stderr,
    )

    try:
        with AppContext(settings=settings) as ctx:
            mcp_server = MCPServer.from_context(ctx)
            mcp_server.run()
    except ConfigError as exc:
        console.print(f"[red]Configuration error: {exc}[/red]")
        raise typer.Exit(code=1)
    except RagKbError as exc:
        console.print(f"[red]Server initialization failed: {exc}[/red]")
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        console.print("[dim]Server stopped.[/dim]")
    except Exception as exc:
        console.print(f"[red]Unexpected error: {exc}[/red]")
        raise typer.Exit(code=1)


@app.command()
def config() -> None:
    """Show current configuration with sensitive keys masked."""
    settings = _build_settings()

    def _render_embedding(cfg: Any) -> Table:
        t = Table(title="Embedding", show_header=False, title_style="bold blue")
        t.add_column("Key", style="cyan")
        t.add_column("Value", style="white")
        t.add_row("API Key", _mask_key(cfg.api_key))
        t.add_row("Base URL", cfg.base_url)
        t.add_row("Model", cfg.model)
        t.add_row("Dimensions", str(cfg.dimensions))
        t.add_row("Batch Size", str(cfg.batch_size))
        t.add_row("Max Retries", str(cfg.max_retries))
        return t

    def _render_reranker(cfg: Any) -> Table:
        t = Table(title="Reranker", show_header=False, title_style="bold blue")
        t.add_column("Key", style="cyan")
        t.add_column("Value", style="white")
        t.add_row("API Key", _mask_key(cfg.api_key))
        t.add_row("Base URL", cfg.base_url)
        t.add_row("Model", cfg.model)
        t.add_row("Max Retries", str(cfg.max_retries))
        return t

    def _render_storage(cfg: Any) -> Table:
        t = Table(title="Storage", show_header=False, title_style="bold blue")
        t.add_column("Key", style="cyan")
        t.add_column("Value", style="white")
        t.add_row("Data Dir", str(Path(cfg.data_dir).expanduser()))
        return t

    def _render_chunking(cfg: Any) -> Table:
        t = Table(title="Chunking", show_header=False, title_style="bold blue")
        t.add_column("Key", style="cyan")
        t.add_column("Value", style="white")
        t.add_row("Header Levels", ", ".join(str(l) for l in cfg.header_levels))
        t.add_row("Max Chunk Size", str(cfg.max_chunk_size))
        t.add_row("Preserve Tables", str(cfg.preserve_tables))
        t.add_row("Preserve Code Blocks", str(cfg.preserve_code_blocks))
        t.add_row("Image Context Window", str(cfg.image_context_window))
        t.add_row("Image Context Max Chars", str(cfg.image_context_max_chars))
        return t

    def _render_retrieval(cfg: Any) -> Table:
        t = Table(title="Retrieval", show_header=False, title_style="bold blue")
        t.add_column("Key", style="cyan")
        t.add_column("Value", style="white")
        t.add_row("Default Top K", str(cfg.default_top_k))
        t.add_row("Default Expand Level", str(cfg.default_expand_to_level))
        t.add_row("Default Max Tokens", str(cfg.default_max_tokens))
        t.add_row("Dense Limit", str(cfg.dense_limit))
        t.add_row("Sparse Limit", str(cfg.sparse_limit))
        t.add_row("RRF K", str(cfg.rrf_k))
        return t

    def _render_logging(cfg: Any) -> Table:
        t = Table(title="Logging", show_header=False, title_style="bold blue")
        t.add_column("Key", style="cyan")
        t.add_column("Value", style="white")
        t.add_row("Level", cfg.level)
        t.add_row("Format", cfg.format)
        return t

    panel = Panel.fit(
        f"[bold]Root Page IDs:[/bold] {', '.join(settings.notion.root_page_ids) if settings.notion.root_page_ids else '[dim](none configured)[/dim]'}",
        title="Notion",
        border_style="blue",
    )
    panel_notion_token = Table(title="", show_header=False)
    panel_notion_token.add_column("", style="cyan")
    panel_notion_token.add_column("", style="white")
    panel_notion_token.add_row("Token", _mask_key(settings.notion.token))

    console.print()
    console.print(Panel("Notion Configuration", style="bold magenta"))
    console.print(panel_notion_token)
    console.print(panel)
    console.print()
    console.print(_render_embedding(settings.embedding))
    console.print()
    console.print(_render_reranker(settings.reranker))
    console.print()
    console.print(_render_storage(settings.storage))
    console.print()
    console.print(_render_chunking(settings.chunking))
    console.print()
    console.print(_render_retrieval(settings.retrieval))
    console.print()
    console.print(_render_logging(settings.logging))


def main() -> None:
    """Entry point for the rag-kb CLI."""
    settings = _build_settings()
    setup_logging(level=settings.logging.level, fmt=settings.logging.format)
    app()


if __name__ == "__main__":
    main()
