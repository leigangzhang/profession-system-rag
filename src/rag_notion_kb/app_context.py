from __future__ import annotations

import logging
from pathlib import Path
from types import TracebackType
from typing import Self

from rag_notion_kb.config import Settings
from rag_notion_kb.embedding.qwen_vl import EmbeddingService
from rag_notion_kb.embedding.reranker import RerankerService
from rag_notion_kb.notion.client import NotionClient
from rag_notion_kb.processing.chunking import MarkdownProcessor
from rag_notion_kb.processing.images import ImageExtractor
from rag_notion_kb.services.search_service import SearchService
from rag_notion_kb.services.summarization import SummarizationService
from rag_notion_kb.services.sync_progress import SyncProgressTracker
from rag_notion_kb.services.sync_service import SyncService
from rag_notion_kb.services.vectorize_worker import VectorizeWorker
from rag_notion_kb.storage.milvus_store import MilvusStore
from rag_notion_kb.storage.root_store import NotionRootStore
from rag_notion_kb.storage.search_history_store import SearchHistoryStore
from rag_notion_kb.storage.sync_state import SyncStateStore
from rag_notion_kb.storage.tree_cache import NotionTreeCache

logger = logging.getLogger(__name__)


class AppContext:
    """Holds all runtime dependencies and manages their lifecycle.

    ``AppContext`` is the single place where ``Settings`` and every long-lived
    service/store are assembled.  It can be used as a synchronous or
    asynchronous context manager; on exit it closes every resource that exposes
    a ``close()`` method, and (in async mode) stops the background
    ``VectorizeWorker``.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings if settings is not None else Settings()
        self._data_dir = Path(self.settings.storage.data_dir).expanduser()
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._closed = False

        # Notion + processing
        self.notion_client = NotionClient(token=self.settings.notion.token)
        self.processor = MarkdownProcessor(self.settings.chunking)
        self.image_extractor = ImageExtractor(
            context_window=self.settings.chunking.image_context_window
        )

        # Embedding / reranker
        self.embedding = EmbeddingService(self.settings.embedding)
        self.reranker = RerankerService(self.settings.reranker)
        self.summarizer: SummarizationService | None = None
        summarization_config = getattr(self.settings, "summarization", None)
        api_key = getattr(summarization_config, "api_key", "")
        if isinstance(api_key, str) and api_key.strip():
            self.summarizer = SummarizationService(summarization_config)

        # Storage
        db_path = self._data_dir / "sync_state.db"
        self.store = MilvusStore(uri=str(self._data_dir / "milvus.db"))
        self.state_store = SyncStateStore(db_path=db_path)
        self.root_store = NotionRootStore(db_path=db_path)
        self.history_store = SearchHistoryStore(db_path=db_path)
        self.tree_cache = NotionTreeCache(db_path=db_path)

        # Services
        self.sync_service = SyncService(
            notion_client=self.notion_client,
            processor=self.processor,
            image_extractor=self.image_extractor,
            embedding=self.embedding,
            store=self.store,
            state_store=self.state_store,
            config=self.settings,
        )
        self.search_service = SearchService(
            embedding=self.embedding,
            reranker=self.reranker,
            store=self.store,
            state_store=self.state_store,
            config=self.settings,
            history_store=self.history_store,
            summarizer=self.summarizer,
        )
        self.progress_tracker = SyncProgressTracker(ttl_seconds=300, db_path=db_path)
        self.worker = VectorizeWorker(
            sync_service=self.sync_service,
            state_store=self.state_store,
            max_concurrent=self.settings.vectorize.max_concurrent,
            poll_interval_seconds=self.settings.vectorize.poll_interval_seconds,
        )

    @property
    def _closeables(self) -> list[object]:
        """Resources that expose a synchronous ``close()`` method."""
        return [
            self.reranker,
            self.embedding,
            self.summarizer,
            self.store,
            self.state_store,
            self.root_store,
            self.history_store,
            self.tree_cache,
            self.progress_tracker,
            self.notion_client,
        ]

    def close(self) -> None:
        """Close all resources synchronously.

        The ``VectorizeWorker`` is not touched here because its ``stop()`` is
        async; use :meth:`aclose` when running in an async context where the
        worker may have been started.
        """
        if self._closed:
            return
        self._closed = True

        if self.worker is not None and getattr(self.worker, "_running", False):
            logger.warning(
                "AppContext.close() called while VectorizeWorker is still running; "
                "use aclose() for async lifecycle management"
            )

        for resource in self._closeables:
            try:
                close_method = getattr(resource, "close", None)
                if close_method is not None:
                    close_method()
            except Exception:
                logger.exception("Failed to close %s", type(resource).__name__)

    async def aclose(self) -> None:
        """Close all resources asynchronously.

        Stops the background worker first, then closes every sync closeable.
        """
        if self._closed:
            return

        if self.worker is not None and getattr(self.worker, "_running", False):
            try:
                await self.worker.stop()
            except Exception:
                logger.exception("Failed to stop VectorizeWorker")

        self.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.aclose()
