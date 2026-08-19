from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from rag_notion_kb.app_context import AppContext
from rag_notion_kb.config import Settings
from rag_notion_kb.exceptions import SummarizationError
from rag_notion_kb.models import (
    ChunkDetail,
    DebugSearchRequest,
    EmbeddingStats,
    NotionRoot,
    PageDetail,
    PageSummary,
    SearchHistory,
    SearchSource,
    SummarizeRequest,
    SummarizeResponse,
    SyncResult,
    TreePageNode,
)
from rag_notion_kb.processing.images import clean_image_alt
from rag_notion_kb.services.search_service import SearchService
from rag_notion_kb.services.sync_progress import SyncProgressTracker
from rag_notion_kb.services.sync_service import SyncService
from rag_notion_kb.services.vectorize_worker import VectorizeWorker
from rag_notion_kb.storage.milvus_store import MilvusStore, validate_page_id
from rag_notion_kb.storage.root_store import NotionRootStore
from rag_notion_kb.storage.search_history_store import SearchHistoryStore
from rag_notion_kb.storage.sync_state import SyncStateStore
from rag_notion_kb.storage.tree_cache import NotionTreeCache
from rag_notion_kb.utils.image_cache import local_image_url, pack_image_url, unpack_image_url

logger = logging.getLogger(__name__)

def _require_page_id(page_id: str) -> None:
    """Raise a 400 response when *page_id* is not a safe Notion page ID."""
    if not validate_page_id(page_id):
        raise HTTPException(status_code=400, detail=f"Invalid page ID: {page_id}")


def _count_nodes(nodes: list[TreePageNode]):
    """Generator that yields every node in the tree (depth-first). Used for counting."""
    for node in nodes:
        yield node
        yield from _count_nodes(node.children)


def _rewrite_markdown_images(page_id: str, markdown: str, config: Settings) -> str:
    """Replace cached image URLs in Markdown with local static paths."""
    images_dir = Path(config.storage.data_dir).expanduser() / "images" / page_id
    if not images_dir.exists():
        return markdown

    def _replace(match: re.Match) -> str:
        alt = match.group(1)
        url = match.group(2)
        display_url, remote_url = unpack_image_url(url)
        if display_url.startswith("/images/"):
            candidate = images_dir / Path(display_url.split("?", 1)[0]).name
            if candidate.exists():
                local_url = f"/images/{page_id}/{candidate.name}"
                return f"![{clean_image_alt(alt)}]({pack_image_url(local_url, remote_url)})"
            return match.group(0)
        name = hashlib.sha256(url.encode()).hexdigest()[:16]
        existing = list(images_dir.glob(f"{name}.*"))
        if existing:
            local_url = f"/images/{page_id}/{existing[0].name}"
            return f"![{clean_image_alt(alt)}]({pack_image_url(local_url, remote_url)})"
        return match.group(0)

    return re.sub(r"!\[(.*?)\]\((.*?)\)", _replace, markdown, flags=re.DOTALL)
def create_app(
    ctx: AppContext | None = None,
    *,
    sync_service: SyncService | None = None,
    search_service: SearchService | None = None,
    store: MilvusStore | None = None,
    state_store: SyncStateStore | None = None,
    config: Settings | None = None,
    worker: VectorizeWorker | None = None,
    root_store: NotionRootStore | None = None,
    progress_tracker: SyncProgressTracker | None = None,
    tree_cache: NotionTreeCache | None = None,
    history_store: SearchHistoryStore | None = None,
) -> FastAPI:
    """Factory that creates and wires a new FastAPI app.

    Accepts either a fully wired ``AppContext`` or the individual dependencies
    for backward compatibility. Values passed explicitly override those from
    ``ctx``.
    """
    if ctx is not None:
        sync_service = sync_service or ctx.sync_service
        search_service = search_service or ctx.search_service
        store = store or ctx.store
        state_store = state_store or ctx.state_store
        config = config or ctx.settings
        worker = worker or ctx.worker
        root_store = root_store or ctx.root_store
        progress_tracker = progress_tracker or ctx.progress_tracker
        tree_cache = tree_cache or ctx.tree_cache
        history_store = history_store or ctx.history_store

    if history_store is None and search_service is not None:
        history_store = getattr(search_service, "history_store", None)

    app = FastAPI(title="RAG Notion KB Web UI")

    @app.get("/health")
    async def health() -> dict[str, str | bool]:
        """Return liveness without invoking external services."""
        return {
            "status": "ok",
            "worker_running": bool(worker and worker._running),
        }

    @app.on_event("startup")
    async def startup_worker() -> None:
        if worker is not None:
            await worker.start()

    @app.on_event("shutdown")
    async def shutdown_worker() -> None:
        if worker is not None:
            await worker.stop()
        if ctx is not None:
            await ctx.aclose()
        elif store is not None:
            try:
                store.close()
            except Exception:
                pass
            if history_store is not None:
                try:
                    history_store.close()
                except Exception:
                    pass

    @app.get("/api/pages")
    async def list_pages() -> list[PageSummary]:
        """Return all synced pages with embedding stats."""
        pages: list[PageSummary] = []
        for state in state_store.list_all():
            if state.status == "skipped":
                continue
            try:
                estats = store.get_page_embedding_stats(state.page_id)
            except Exception:
                logger.warning(
                    "Failed to get embedding stats for %s", state.page_id, exc_info=True
                )
                estats = {
                    "total": state.chunk_count + state.image_count,
                    "nonzero": 0,
                    "zero": state.chunk_count + state.image_count,
                    "dim": 0,
                }
            root_id = tree_cache.get_page_root(state.page_id) if tree_cache is not None else None
            pages.append(
                PageSummary(
                    page_id=state.page_id,
                    page_title=state.page_title,
                    page_url=state.page_url,
                    chunk_count=state.chunk_count,
                    image_count=state.image_count,
                    status=state.status,
                    zero_vector_chunks=estats["zero"],
                    last_edited_time=state.last_edited_time,
                    last_synced_time=state.last_synced_time,
                    vector_enabled=state.vector_enabled,
                    vector_status=state.vector_status,
                    vector_progress=state.vector_progress,
                    vector_stage=state.vector_stage,
                    last_vectorized_time=state.last_vectorized_time,
                    doc_size_kb=round(len(state.raw_markdown or "") / 1024, 1),
                    root_id=root_id,
                )
            )
        return pages

    @app.get("/api/pages/{page_id}")
    async def page_detail(page_id: str) -> PageDetail:
        """Return full detail for a single page: markdown, chunks, embedding stats."""
        _require_page_id(page_id)
        state = state_store.get(page_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"Page {page_id} not found")

        try:
            estats_raw = store.get_page_embedding_stats(page_id)
        except Exception:
            logger.warning(
                "Failed to get embedding stats for %s", page_id, exc_info=True
            )
            estats_raw = {"total": 0, "nonzero": 0, "zero": 0, "dim": 0, "sample_first5": []}

        estats = EmbeddingStats(
            total=estats_raw["total"],
            nonzero=estats_raw["nonzero"],
            zero=estats_raw["zero"],
            dim=estats_raw.get("dim", 0),
            sample_first5=estats_raw.get("sample_first5", []),
        )

        chunks: list[ChunkDetail] = []
        try:
            hits_with_vectors = store.get_page_chunks_with_vectors(page_id)
        except Exception:
            logger.warning("Failed to get chunks for %s", page_id, exc_info=True)
            hits_with_vectors = []

        for hit, nonzero in hits_with_vectors:
            chunks.append(
                ChunkDetail(
                    chunk_index=hit.metadata.chunk_index,
                    chunk_type=hit.metadata.chunk_type.value,
                    header_path=hit.metadata.header_path,
                    header_level=hit.metadata.header_level,
                    text=hit.chunk_text,
                    vector_nonzero=nonzero,
                    image_url=local_image_url(hit.metadata.image_url),
                )
            )

        return PageDetail(
            page_id=state.page_id,
            page_title=state.page_title,
            page_url=state.page_url,
            status=state.status,
            last_synced_time=state.last_synced_time,
            raw_markdown=_rewrite_markdown_images(page_id, state.raw_markdown, config),
            chunks=chunks,
            embedding_stats=estats,
        )

    @app.post("/api/pages/{page_id}/sync")
    async def sync_single_page(page_id: str) -> SyncResult:
        """Re-sync a single page into the vector store."""
        _require_page_id(page_id)
        try:
            result, _ = sync_service.sync(root_page_ids=[page_id])
            return result
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/pages/{page_id}/vector-toggle")
    async def toggle_vector(page_id: str) -> dict:
        """Toggle vector_enabled for a page.

        Turning on: resets status to pending and enqueues for vectorization.
        Turning off: only flips the flag — data (status, Milvus vectors) is preserved.
        """
        _require_page_id(page_id)
        state = state_store.get(page_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"Page {page_id} not found")
        new_enabled = not state.vector_enabled
        if new_enabled:
            updated = state.model_copy(update={
                "vector_enabled": True,
                "vector_status": "pending",
                "vector_error_message": None,
                "vector_progress": 0,
            })
            state_store.upsert(updated)
            if worker is not None:
                await worker.enqueue(page_id)
        else:
            updated = state.model_copy(update={"vector_enabled": False})
            state_store.upsert(updated)
        return {"page_id": page_id, "vector_enabled": new_enabled}

    @app.post("/api/pages/{page_id}/vectorize")
    async def trigger_vectorize(page_id: str) -> dict:
        """Manually trigger vectorization for a single page."""
        _require_page_id(page_id)
        if worker is None:
            raise HTTPException(status_code=400, detail="VectorizeWorker is not running")
        await worker.enqueue(page_id)
        return {"page_id": page_id, "status": "enqueued"}

    @app.get("/api/worker/status")
    async def worker_status() -> dict:
        """Return the current state of the background worker."""
        return {
            "running": worker is not None and worker._running,
            "max_concurrent": worker._semaphore._value if worker else 0,
        }


    # ------------------------------------------------------------------
    # Root Management API
    # ------------------------------------------------------------------

    @app.get("/api/roots")
    async def list_roots() -> list[NotionRoot]:
        """Return all configured Notion root pages."""
        if root_store is None:
            raise HTTPException(status_code=500, detail="RootStore not initialized")
        return root_store.list_all()

    @app.post("/api/roots", status_code=201)
    async def add_root(body: dict) -> NotionRoot:
        """Add a new Notion root page by page_id."""
        if root_store is None:
            raise HTTPException(status_code=500, detail="RootStore not initialized")
        page_id = body.get("page_id", "").strip()
        if not page_id:
            raise HTTPException(status_code=400, detail="page_id is required")

        # Check for duplicates
        if root_store.get(page_id) is not None:
            raise HTTPException(status_code=409, detail="Root already exists")

        # Validate page exists in Notion
        try:
            meta = sync_service.notion_client.get_page_metadata(page_id)
        except Exception as exc:
            logger.warning("Failed to validate page %s: %s", page_id, exc)
            raise HTTPException(status_code=404, detail=f"Page not found in Notion: {page_id}") from exc

        from datetime import datetime, timezone
        root = NotionRoot(
            root_id=page_id,
            page_title=meta.title,
            page_url=meta.url,
            added_time=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            is_active=True,
        )
        root_store.add(root)

        # Auto-populate tree cache in background
        async def _cache_tree() -> None:
            try:
                def _load_and_cache() -> None:
                    pages = sync_service.notion_client.enumerate_pages([page_id])
                    if tree_cache is not None:
                        tree_cache.replace_root(page_id, pages)

                await asyncio.to_thread(_load_and_cache)
            except Exception:
                logger.exception("Background tree cache population failed for root %s", page_id)

        asyncio.create_task(_cache_tree())

        return root

    @app.delete("/api/roots/{root_id}")
    async def delete_root(root_id: str) -> dict:
        """Remove a Notion root page and all of its synced/cached data."""
        if root_store is None or tree_cache is None:
            raise HTTPException(status_code=500, detail="RootStore or TreeCache not initialized")
        if root_store.get(root_id) is None:
            raise HTTPException(status_code=404, detail=f"Root {root_id} not found")

        page_ids = tree_cache.list_page_ids(root_id)
        failed = []
        for pid in page_ids:
            try:
                store.delete_by_page_id(pid)
            except Exception:
                logger.warning("Failed to delete Milvus data for page %s", pid, exc_info=True)
                failed.append(pid)
            state_store.delete(pid)

        tree_cache.invalidate(root_id)
        root_store.remove(root_id)
        return {
            "root_id": root_id,
            "deleted": True,
            "pages_deleted": len(page_ids),
            "failures": failed,
        }

    @app.get("/api/roots/{root_id}/tree")
    async def get_page_tree(root_id: str) -> dict:
        """Return the cached page tree for a given root page, enriched with sync info."""
        if root_store is None or tree_cache is None:
            raise HTTPException(status_code=500, detail="RootStore or TreeCache not initialized")

        def _load_tree() -> dict:
            root = root_store.get(root_id)
            if root is None:
                raise HTTPException(status_code=404, detail=f"Root {root_id} not found")

            tree_nodes, cached_at = tree_cache.get_tree(root_id)
            states = state_store.get_many(tree_cache.list_page_ids(root_id))

            def enrich(node: TreePageNode) -> dict:
                d = node.model_dump()
                s = states.get(node.page_id)
                if s is not None:
                    d["sync_status"] = (
                        s.vector_status
                        if s.vector_status in ("indexed", "indexing", "failed")
                        else s.status
                    )
                    d["synced_at"] = s.last_synced_time
                else:
                    d["sync_status"] = None
                    d["synced_at"] = None
                d["children"] = [enrich(c) for c in node.children]
                return d

            tree_data = [enrich(n) for n in tree_nodes]
            return {
                "root_id": root_id,
                "root_title": root.page_title,
                "total_pages": sum(1 for _ in _count_nodes(tree_nodes)),
                "cached_at": cached_at,
                "tree": tree_data,
            }

        return await run_in_threadpool(_load_tree)

    @app.post("/api/roots/{root_id}/refresh")
    async def refresh_root_tree(root_id: str) -> dict:
        """Re-pull the page tree from Notion API and update cache."""
        if root_store is None or tree_cache is None:
            raise HTTPException(status_code=500, detail="RootStore or TreeCache not initialized")
        if root_store.get(root_id) is None:
            raise HTTPException(status_code=404, detail=f"Root {root_id} not found")

        def _refresh() -> tuple[list[TreePageNode], str | None]:
            try:
                pages = sync_service.notion_client.enumerate_pages([root_id])
            except Exception as exc:
                logger.warning("Failed to enumerate pages for root %s: %s", root_id, exc)
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            tree_cache.replace_root(root_id, pages)
            return tree_cache.get_tree(root_id)

        tree_nodes, cached_at = await run_in_threadpool(_refresh)
        total = sum(1 for _ in _count_nodes(tree_nodes))
        return {
            "root_id": root_id,
            "total_pages": total,
            "cached_at": cached_at,
        }

    # ------------------------------------------------------------------
    # Batch Sync API
    # ------------------------------------------------------------------

    @app.post("/api/sync", status_code=202)
    async def batch_sync(body: dict) -> dict:
        """Start a batch sync for the specified pages (runs in background)."""
        if progress_tracker is None:
            raise HTTPException(status_code=500, detail="ProgressTracker not initialized")
        page_ids: list[str] = body.get("page_ids", [])
        force_full: bool = body.get("force_full", False)
        if not page_ids:
            raise HTTPException(status_code=400, detail="page_ids is required and cannot be empty")

        total = len(page_ids)
        sync_id = progress_tracker.create_task(total_pages=total)
        cb = progress_tracker.make_callback(sync_id)

        async def _run_sync() -> None:
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                results = await loop.run_in_executor(
                    None,
                    lambda: sync_service.sync_fetch(page_ids=page_ids, force_full=force_full, progress_callback=cb)
                )
                progress_tracker.complete(sync_id, results)
            except Exception as exc:
                logger.exception("Batch sync %s failed", sync_id)
                progress_tracker.fail(sync_id, str(exc))

        import asyncio
        asyncio.create_task(_run_sync())

        return {"sync_id": sync_id, "status": "started", "total_pages": total}

    @app.get("/api/sync/{sync_id}/progress")
    async def sync_progress(sync_id: str) -> dict:
        """Return the current progress of a sync task."""
        if progress_tracker is None:
            raise HTTPException(status_code=500, detail="ProgressTracker not initialized")
        task = progress_tracker.get(sync_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Sync task {sync_id} not found")
        return task.model_dump()


    # ------------------------------------------------------------------
    # Search Debug & History API
    # ------------------------------------------------------------------

    @app.post("/api/search/debug")
    async def debug_search(req: DebugSearchRequest):
        """Execute a debug search with stage scores exposed."""
        if search_service is None:
            raise HTTPException(status_code=500, detail="SearchService not initialized")
        return await run_in_threadpool(search_service.debug_search, req)

    @app.post("/api/search/summarize")
    async def summarize_search(req: SummarizeRequest) -> SummarizeResponse:
        """Generate a summary from already-retrieved passages."""
        if search_service is None:
            raise HTTPException(status_code=500, detail="SearchService not initialized")
        try:
            result = await run_in_threadpool(
                search_service.summarize_for_web,
                req.query,
                req.passages,
            )
        except SummarizationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if req.history_id and history_store is not None:
            record = history_store.get(req.history_id)
            if record is not None and record.snapshot is not None:
                snapshot = dict(record.snapshot)
                snapshot["summary"] = result.summary
                snapshot["summary_error"] = None
                snapshot["summary_model"] = result.model
                snapshot["summary_char_count"] = result.char_count
                snapshot["summary_token_count"] = result.token_count
                snapshot["summary_duration_ms"] = result.duration_ms
                snapshot["summary_source_char_count"] = result.source_char_count
                snapshot["summary_source_token_count"] = result.source_token_count
                snapshot["summary_compression_ratio"] = result.compression_ratio
                if isinstance(snapshot.get("params"), dict):
                    snapshot["params"]["summarize"] = True
                history_store.update_snapshot(req.history_id, snapshot)
        return result

    @app.get("/api/search/history")
    async def list_search_history(
        limit: int = Query(50, ge=1, le=500),
        source: SearchSource | None = Query(None),
    ) -> list[dict]:
        """Return recent search history records."""
        if history_store is None:
            raise HTTPException(status_code=500, detail="SearchHistoryStore not initialized")
        return [
            record.model_dump(exclude={"snapshot"})
            for record in history_store.list_recent(limit=limit, source=source)
        ]

    @app.get("/api/search/history/stats")
    async def search_history_stats() -> dict:
        """Return aggregate retrieval-health metrics for search history."""
        if history_store is None:
            raise HTTPException(status_code=500, detail="SearchHistoryStore not initialized")
        return history_store.stats()

    @app.delete("/api/search/history")
    async def clear_search_history(clear: str | None = Query(None)) -> dict:
        """Clear all search history when ``clear=all`` is supplied."""
        if history_store is None:
            raise HTTPException(status_code=500, detail="SearchHistoryStore not initialized")
        if clear != "all":
            raise HTTPException(status_code=400, detail="clear=all is required")
        return {"cleared": True, "count": history_store.clear_all()}

    @app.get("/api/search/history/{history_id}")
    async def get_search_history(history_id: str) -> SearchHistory:
        """Return one history record, including its replay parameters."""
        if history_store is None:
            raise HTTPException(status_code=500, detail="SearchHistoryStore not initialized")
        record = history_store.get(history_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Search history {history_id} not found")
        return record

    @app.post("/api/search/history/{history_id}/replay")
    async def replay_search_history(history_id: str):
        """Return the saved result snapshot for a history record."""
        if history_store is None:
            raise HTTPException(status_code=500, detail="SearchHistoryStore not initialized")
        record = history_store.get(history_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Search history {history_id} not found")
        if record.snapshot is None:
            raise HTTPException(status_code=404, detail=f"Search history {history_id} has no snapshot")
        return record.snapshot

    @app.delete("/api/search/history/{history_id}")
    async def delete_search_history(history_id: str) -> dict:
        """Delete one history record."""
        if history_store is None:
            raise HTTPException(status_code=500, detail="SearchHistoryStore not initialized")
        if not history_store.delete(history_id):
            raise HTTPException(status_code=404, detail=f"Search history {history_id} not found")
        return {"deleted": True}




    # Mount static files
    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Mount local image cache for serving cached Notion images
    images_dir = Path(config.storage.data_dir).expanduser() / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/images", StaticFiles(directory=str(images_dir)), name="images")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(static_dir / "index.html"))

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str) -> FileResponse:
        """Serve the Vue SPA for unknown non-API paths.

        The route is registered after every API, static, and image mount so
        existing endpoints keep their exact behavior.
        """
        reserved = (
            full_path in {"api", "static", "images"}
            or full_path.startswith(("api/", "static/", "images/"))
        )
        if reserved:
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(str(static_dir / "index.html"))

    return app


# Module-level app: used by uvicorn string import ("rag_notion_kb.web_server:app").
# This is initialized lazily; the real wiring happens in cli.py's web command.
app: FastAPI | None = None
