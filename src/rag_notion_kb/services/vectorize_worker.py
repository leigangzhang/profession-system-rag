from __future__ import annotations

import asyncio
import logging
from typing import Any

from rag_notion_kb.services.sync_service import SyncService
from rag_notion_kb.storage.sync_state import SyncStateStore

logger = logging.getLogger(__name__)


class VectorizeWorker:
    """Background worker that vectorizes pages whose toggle is ON.

    Polls the state store for pages with ``vector_enabled=True`` and
    ``vector_status='pending'``, then runs the chunk → embed → Milvus
    pipeline with controlled concurrency.
    """

    def __init__(
        self,
        sync_service: SyncService,
        state_store: SyncStateStore,
        max_concurrent: int = 3,
        poll_interval_seconds: int = 2,
    ) -> None:
        self._sync_service = sync_service
        self._state_store = state_store
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._poll_interval = poll_interval_seconds
        self._running = False
        self._task: asyncio.Task[Any] | None = None
        self._active_page_ids: set[str] = set()

    async def start(self) -> None:
        """Launch the background poll-and-process loop."""
        if self._running:
            return
        reset_count = self._state_store.reset_stale_vectorize()
        if reset_count:
            logger.info("Reset %d stale indexing page(s) to pending", reset_count)
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "VectorizeWorker started (max_concurrent=%d, poll_interval=%ds)",
            self._semaphore._value, self._poll_interval,
        )

    async def stop(self) -> None:
        """Gracefully shut down the worker loop.

        Already-running pages are allowed to finish.
        """
        if not self._running:
            return
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("VectorizeWorker stopped")

    async def enqueue(self, page_id: str) -> None:
        """Manually trigger vectorization for a single page.

        The page's ``vector_status`` is set to ``pending`` before enqueuing.
        """
        state = self._state_store.get(page_id)
        if state is None:
            logger.warning("enqueue: page %s not found in state store", page_id)
            return
        if not state.vector_enabled:
            logger.info("enqueue: page %s vector_enabled=False, enabling", page_id)
            # Enable the toggle and mark as pending
            from rag_notion_kb.models import PageSyncState
            updated = state.model_copy(update={
                "vector_enabled": True,
                "vector_status": "pending",
                "vector_error_message": None,
                "vector_progress": 0,
                "vector_stage": "pending",
            })
            self._state_store.upsert(updated)
        elif state.vector_status != "pending":
            self._state_store.update_vector_status(
                page_id,
                vector_status="pending",
                vector_error_message=None,
                vector_progress=0,
                vector_stage="pending",
            )
        logger.info("enqueue: page %s set to pending for vectorization", page_id)

    async def _poll_loop(self) -> None:
        """Main loop: poll for pending pages and dispatch them."""
        while self._running:
            try:
                pending = self._state_store.get_pending_vectorize()
                if pending:
                    logger.debug("VectorizeWorker: found %d pending page(s)", len(pending))
                for state in pending:
                    if not self._running:
                        break
                    if state.page_id in self._active_page_ids:
                        continue
                    if not self._state_store.claim_vectorize(state.page_id):
                        continue
                    self._active_page_ids.add(state.page_id)
                    await self._semaphore.acquire()
                    asyncio.create_task(self._vectorize_one(state))
            except Exception:
                logger.exception("VectorizeWorker poll loop error")
            await asyncio.sleep(self._poll_interval)

    async def _vectorize_one(self, state: Any) -> None:
        """Run vectorization for one page, then release the semaphore.

        All heavy work is offloaded to a thread so that the async event
        loop is not blocked.
        """
        page_id = state.page_id
        try:
            logger.info("VectorizeWorker: starting page %s", page_id)
            await asyncio.to_thread(self._sync_service.vectorize_state, state)
            logger.info("VectorizeWorker: finished page %s", page_id)
        except Exception:
            logger.exception("VectorizeWorker: page %s failed", page_id)
            try:
                self._state_store.update_vector_status(
                    page_id,
                    vector_status="failed",
                    vector_error_message="Vectorization failed unexpectedly",
                    vector_stage="failed",
                )
            except Exception:
                logger.exception("VectorizeWorker: failed to record failure status for %s", page_id)
        finally:
            self._active_page_ids.discard(page_id)
            self._semaphore.release()
