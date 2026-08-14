from __future__ import annotations

import logging
import math
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from rag_notion_kb.config import Settings
from rag_notion_kb.embedding.qwen_vl import EmbeddingService
from rag_notion_kb.embedding.reranker import RerankerService
from rag_notion_kb.exceptions import RetrievalError, StorageError
from rag_notion_kb.models import (
    ChunkMetadata,
    ChunkType,
    ContextExpandMode,
    DebugSearchHit,
    DebugSearchRequest,
    DebugSearchResponse,
    EmbeddingItem,
    RankCandidate,
    SearchHistory,
    SearchResult,
    SearchSource,
    StageScores,
)
from rag_notion_kb.retrieval.context_expand import ContextExpander
from rag_notion_kb.retrieval.hybrid_search import weighted_fusion
from rag_notion_kb.retrieval.truncate import TokenTruncator
from rag_notion_kb.storage.milvus_store import MilvusStore
from rag_notion_kb.storage.search_history_store import SearchHistoryStore
from rag_notion_kb.storage.sync_state import SyncStateStore
from rag_notion_kb.utils.image_cache import remote_image_url

logger = logging.getLogger(__name__)

_CANDIDATE_REDUNDANCY_FACTOR = 1.3


class SearchService:
    """Orchestrate the full retrieval pipeline: embed, search, fuse, rerank, expand."""

    def __init__(
        self,
        embedding: EmbeddingService,
        reranker: RerankerService,
        store: MilvusStore,
        state_store: SyncStateStore,
        config: Settings,
        history_store: SearchHistoryStore | None = None,
    ) -> None:
        self.embedding = embedding
        self.reranker = reranker
        self.store = store
        self.state_store = state_store
        self.config = config
        self.history_store = history_store
        self.expander = ContextExpander(store)
        self.truncator = TokenTruncator()

    def search(
        self,
        query: str,
        top_k: int | None = None,
        expand_to_level: int | None = None,
        max_tokens: int | None = None,
        filters: dict[str, Any] | None = None,
        history_source: SearchSource | None = None,
        rerank: bool = True,
        dense_weight: float = 0.5,
        sparse_weight: float = 0.5,
        min_similarity: float = 0.4,
        rerank_model: str = "qwen3-vl-rerank",
        context_mode: ContextExpandMode | str | None = ContextExpandMode.H2,
    ) -> list[SearchResult]:
        """Run the full retrieval pipeline for *query*.

        Args:
            query: User search query.
            top_k: Number of results to return. Defaults to config.retrieval.default_top_k.
            expand_to_level: Legacy heading expansion level. Used only when
                ``context_mode`` is not supplied.
            max_tokens: Maximum tokens per result text. Defaults to config default.
            filters: Generic metadata filters accepted by Milvus.
            history_source: When set, record this successful search in the
                configured history store.
            rerank: When False, skip the reranker and keep fusion order.
            dense_weight: Dense weight used by weighted fusion.
            sparse_weight: Sparse weight used by weighted fusion.
            min_similarity: Dense-only minimum cosine similarity.
            rerank_model: ReRank model; empty string disables reranking.
            context_mode: Context expansion mode. Defaults to Web's ``none``.

        Returns:
            Sorted list of SearchResult objects, highest relevance first.
        """
        retrieval_cfg = self.config.retrieval
        top_k = top_k if top_k is not None else retrieval_cfg.default_top_k
        max_tokens = max_tokens if max_tokens is not None else retrieval_cfg.default_max_tokens
        if context_mode is None:
            if expand_to_level is None:
                context_mode = ContextExpandMode.NONE
            elif expand_to_level >= 100:
                context_mode = ContextExpandMode.NONE
            elif expand_to_level <= 1:
                context_mode = ContextExpandMode.PARENT
            else:
                context_mode = ContextExpandMode.H2
        else:
            context_mode = ContextExpandMode(context_mode)

        total_weight = dense_weight + sparse_weight
        if total_weight > 0 and abs(total_weight - 1.0) > 1e-9:
            dense_weight /= total_weight
            sparse_weight /= total_weight

        normalized_filters: dict[str, list[str]] = {}
        for field, value in (filters or {}).items():
            values = value if isinstance(value, list) else [value]
            normalized_filters[field] = [str(item) for item in values]

        request = DebugSearchRequest(
            query=query,
            dense_weight=dense_weight,
            sparse_weight=sparse_weight,
            top_k=top_k,
            min_similarity=min_similarity,
            rerank_model=rerank_model if rerank else "",
            filters=normalized_filters,
            context_mode=context_mode,
            max_tokens=max_tokens,
        )
        response = self.debug_search(request, record_history=False)
        if response.error:
            if history_source is not None:
                self._record_history(
                    query=query,
                    source=history_source,
                    params=request.model_dump(mode="json"),
                    result_summary={
                        "total_results": 0,
                        "top_score": 0.0,
                        "min_score": 0.0,
                        "max_score": 0.0,
                        "latency_ms": response.latency_ms,
                        "dense_count": 0,
                        "sparse_count": 0,
                        "success": False,
                        "error": response.error,
                    },
                    snapshot=response.model_dump(mode="json"),
                )
            raise RetrievalError(response.error)

        results = [
            self._search_result_from_debug_hit(hit)
            for hit in response.results
        ]
        if history_source is not None:
            self._record_history(
                query=query,
                source=history_source,
                params=request.model_dump(mode="json"),
                result_summary={
                    "total_results": len(response.results),
                    "top_score": response.results[0].scores.final_score
                    if response.results
                    else 0.0,
                    "min_score": min(
                        hit.scores.final_score for hit in response.results
                    )
                    if response.results
                    else 0.0,
                    "max_score": max(
                        hit.scores.final_score for hit in response.results
                    )
                    if response.results
                    else 0.0,
                    "latency_ms": response.latency_ms,
                    "dense_count": response.total_dense,
                    "sparse_count": response.total_sparse,
                    "success": True,
                    "error": None,
                },
                snapshot=response.model_dump(mode="json"),
            )
        return results

    @staticmethod
    def _search_result_from_debug_hit(hit: DebugSearchHit) -> SearchResult:
        """Convert a debug hit back to the compact production result shape."""
        metadata = ChunkMetadata(
            page_id=hit.page_id,
            page_title=hit.page_title,
            page_url=hit.page_url,
            header_path=hit.header_path,
            header_level=hit.header_level,
            last_edited_time="",
            chunk_index=hit.chunk_index,
            chunk_type=ChunkType(hit.chunk_type),
            image_url=hit.image_url,
        )
        return SearchResult(
            text=hit.expanded_text,
            score=hit.scores.final_score,
            source=metadata,
            matched_snippet=hit.matched_snippet,
        )

    def debug_search(
        self,
        request: DebugSearchRequest,
        *,
        record_history: bool = True,
    ) -> DebugSearchResponse:
        """Run the retrieval pipeline and expose scores from every stage.

        This method deliberately mirrors production search but uses weighted
        fusion, a configurable similarity floor, and an optional rerank model.
        """
        started = time.perf_counter()

        def _empty_response(error: str | None = None) -> DebugSearchResponse:
            return DebugSearchResponse(
                query=request.query,
                params=request,
                total_dense=0,
                total_sparse=0,
                total_after_filter=0,
                total_after_fusion=0,
                total_after_rerank=0,
                results=[],
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=error,
            )

        if not request.query.strip():
            return _empty_response()

        try:
            query_vector = self._embed_query(request.query)
            candidate_limit = self._candidate_limit(request.top_k)
            raw_limit = candidate_limit

            dense_hits = self.store.search_dense(
                query_vector,
                limit=raw_limit,
                filters=request.filters,
            )
            sparse_hits = self.store.search_sparse(
                request.query,
                limit=raw_limit,
                filters=request.filters,
            )

            if request.dense_weight <= 0:
                dense_hits = []
            if request.sparse_weight <= 0:
                sparse_hits = []
            self._normalize_sparse_scores(sparse_hits)
            total_dense = len(dense_hits)
            total_sparse = len(sparse_hits)

            dense_hits = [
                hit for hit in dense_hits if hit.score >= request.min_similarity
            ]
            filtered_ids = {hit.id for hit in dense_hits} | {hit.id for hit in sparse_hits}

            dense_scores = {hit.id: hit.score for hit in dense_hits}
            sparse_scores = {hit.id: hit.score for hit in sparse_hits}
            fused = weighted_fusion(
                dense_hits,
                sparse_hits,
                request.dense_weight,
                request.sparse_weight,
            )
            fused_scores = {hit.id: hit.score for hit in fused}

            if request.rerank_model.strip():
                reranked, rerank_scores = self._rerank_with_scores(
                    request.query,
                    fused,
                    candidate_limit,
                    model=request.rerank_model,
                )
            else:
                reranked, rerank_scores = fused[:candidate_limit], {}

            results: list[DebugSearchHit] = []
            seen_texts: set[str] = set()
            for hit in reranked:
                if request.context_mode is ContextExpandMode.NONE:
                    expanded = hit.chunk_text
                else:
                    expanded = self.expander.expand(
                        hit,
                        self._expand_level(request.context_mode, hit.metadata.header_level),
                    )
                expanded_text = self.truncator.truncate(expanded, request.max_tokens)
                if expanded_text in seen_texts:
                    continue
                seen_texts.add(expanded_text)
                final_score = rerank_scores.get(hit.id, fused_scores[hit.id])
                hit.score = final_score
                results.append(
                    DebugSearchHit(
                        rank=len(results) + 1,
                        page_id=hit.metadata.page_id,
                        page_title=hit.metadata.page_title,
                        page_url=hit.metadata.page_url,
                        chunk_index=hit.metadata.chunk_index,
                        chunk_type=hit.metadata.chunk_type.value,
                        header_path=hit.metadata.header_path,
                        header_level=hit.metadata.header_level,
                        chunk_text=hit.chunk_text,
                        expanded_text=expanded_text,
                        scores=StageScores(
                            dense_score=dense_scores.get(hit.id),
                            sparse_score=sparse_scores.get(hit.id),
                            rrf_score=fused_scores.get(hit.id),
                            rerank_score=rerank_scores.get(hit.id),
                            final_score=final_score,
                        ),
                        matched_snippet=hit.chunk_text,
                        image_url=hit.metadata.image_url,
                    )
                )
                if len(results) >= request.top_k:
                    break

            response = DebugSearchResponse(
                query=request.query,
                params=request,
                total_dense=total_dense,
                total_sparse=total_sparse,
                total_after_filter=len(filtered_ids),
                total_after_fusion=len(fused),
                total_after_rerank=len(reranked) if request.rerank_model.strip() else 0,
                results=results,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except (RetrievalError, StorageError) as exc:
            logger.exception("Debug search failed")
            response = _empty_response(error=str(exc))

        if record_history:
            final_scores = [hit.scores.final_score for hit in response.results]
            self._record_history(
                query=request.query,
                source=SearchSource.DEBUG,
                params=request.model_dump(mode="json"),
                result_summary={
                    "total_results": len(response.results),
                    "top_score": response.results[0].scores.final_score
                    if response.results
                    else 0.0,
                    "min_score": min(final_scores) if final_scores else 0.0,
                    "max_score": max(final_scores) if final_scores else 0.0,
                    "latency_ms": response.latency_ms,
                    "dense_count": response.total_dense,
                    "sparse_count": response.total_sparse,
                    "success": response.error is None,
                    "error": response.error,
                },
                snapshot=response.model_dump(mode="json"),
            )
        return response

    def get_page_detail(self, page_id: str) -> tuple[str, Any] | None:
        """Return the full reconstructed text and metadata for a page.

        Because full Markdown is not stored independently, this concatenates all
        chunks for the page ordered by chunk_index.

        Args:
            page_id: Notion page ID.

        Returns:
            Tuple of (page text, metadata of first chunk) or None if no chunks.
        """
        chunks = self.store.get_page_chunks(page_id)
        if not chunks:
            return None
        chunks.sort(key=lambda c: c.metadata.chunk_index)
        text = "\n\n".join(chunk.chunk_text for chunk in chunks)
        return text, chunks[0].metadata

    def stats(self) -> dict[str, Any]:
        """Return aggregate statistics for the knowledge base."""
        store_stats = self.store.stats()
        pages = self.state_store.list_all()
        return {
            "total_chunks": store_stats.get("total_chunks", 0),
            "total_pages": len(pages),
            "synced_pages": sum(1 for p in pages if p.status == "synced"),
            "failed_pages": sum(1 for p in pages if p.status == "failed"),
            "last_synced_time": self.state_store.get_last_sync_time(),
        }

    def _embed_query(self, query: str) -> list[float]:
        items = [EmbeddingItem(type="text", text=query)]
        vectors = self.embedding.embed(items)
        if not vectors:
            raise RetrievalError("Failed to embed query")
        return vectors[0]

    def _rerank(
        self,
        query: str,
        fused: list[Any],
        candidate_limit: int,
    ) -> list[Any]:
        rerank_pool = fused[:candidate_limit]
        if not rerank_pool:
            return []

        candidates = [
            RankCandidate(
                text=hit.chunk_text,
                image_url=remote_image_url(hit.metadata.image_url),
            )
            for hit in rerank_pool
        ]
        try:
            ranked = self.reranker.rerank(query, candidates)
        except RetrievalError:
            logger.exception("Rerank failed; falling back to RRF order")
            return rerank_pool

        ordered: list[Any] = []
        for index, _ in ranked:
            if 0 <= index < len(rerank_pool):
                ordered.append(rerank_pool[index])
        return ordered if ordered else rerank_pool

    def _rerank_with_scores(
        self,
        query: str,
        fused: list[Any],
        candidate_limit: int,
        *,
        model: str | None = None,
    ) -> tuple[list[Any], dict[int, float]]:
        """Rerank a fusion pool and retain the relevance score per hit."""
        rerank_pool = fused[:candidate_limit]
        if not rerank_pool:
            return [], {}

        candidates = [
            RankCandidate(
                text=hit.chunk_text,
                image_url=remote_image_url(hit.metadata.image_url),
            )
            for hit in rerank_pool
        ]
        try:
            ranked = self.reranker.rerank(query, candidates, model=model)
        except RetrievalError:
            logger.exception("Rerank failed; falling back to fusion order")
            return rerank_pool, {}

        ordered: list[Any] = []
        rerank_scores: dict[int, float] = {}
        for index, score in ranked:
            if 0 <= index < len(rerank_pool):
                hit = rerank_pool[index]
                ordered.append(hit)
                rerank_scores[hit.id] = score
        return (ordered, rerank_scores) if ordered else (rerank_pool, {})

    @staticmethod
    def _expand_level(mode: ContextExpandMode, header_level: int) -> int:
        if mode is ContextExpandMode.H2:
            return 2
        if mode is ContextExpandMode.PARENT:
            return max(1, header_level - 1)
        return 100

    @staticmethod
    def _candidate_limit(top_k: int) -> int:
        """Return a redundant candidate count used before post-expansion dedup."""
        return max(top_k, math.ceil(top_k * _CANDIDATE_REDUNDANCY_FACTOR))

    @staticmethod
    def _normalize_sparse_scores(hits: list[Any]) -> None:
        """Map negative BM25 distances to ranked ``[0, 1]`` relevance scores.

        Milvus Lite returns unbounded negative BM25 distances. Ranking the
        candidate pool gives dense and sparse scores a shared display scale,
        while retaining the original retrieval order for tie-heavy results.
        """
        if not hits or all(hit.score >= 0 for hit in hits):
            return
        count = len(hits)
        for rank, hit in enumerate(hits):
            hit.score = 1.0 if count == 1 else (count - 1 - rank) / (count - 1)

    def _record_history(
        self,
        *,
        query: str,
        source: SearchSource,
        params: dict[str, Any],
        result_summary: dict[str, Any],
        snapshot: dict[str, Any] | None = None,
    ) -> None:
        """Persist a search summary. Storage failures must not break retrieval."""
        if self.history_store is None:
            return
        try:
            self.history_store.add_async(
                SearchHistory(
                    history_id=str(uuid.uuid4()),
                    query=query,
                    source=source,
                    params=params,
                    result_summary=result_summary,
                    snapshot=snapshot,
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
            )
        except Exception:
            logger.exception("Failed to record search history")
