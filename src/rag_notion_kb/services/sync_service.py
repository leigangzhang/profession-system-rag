from __future__ import annotations
from collections.abc import Callable

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rag_notion_kb.config import Settings
from rag_notion_kb.embedding.qwen_vl import EMBEDDING_VERSION, EmbeddingService
from rag_notion_kb.exceptions import ConfigError, EmbeddingServiceError, RagKbError
from rag_notion_kb.models import (
    Chunk,
    ChunkType,
    EmbeddingItem,
    ImageDoc,
    PageMetadata,
    PageSyncState,
    SyncResult,
    VectorizeResult,
)
from rag_notion_kb.notion.client import NotionClient
from rag_notion_kb.processing.chunking import (
    CHUNKING_VERSION,
    PROTECTED_HARD_LIMIT,
    MarkdownProcessor,
)
from rag_notion_kb.processing.images import ImageExtractor, clean_image_alt
from rag_notion_kb.storage.milvus_store import MilvusStore
from rag_notion_kb.storage.sync_state import SyncStateStore
from rag_notion_kb.utils.image_cache import (
    cleanup_image_cache,
    download_image,
    image_to_base64,
    pack_image_url,
)

logger = logging.getLogger(__name__)

_IMAGE_MARKDOWN_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


class SyncService:
    """Orchestrate the Notion -> chunks -> embeddings -> vector store pipeline.

    The pipeline is split into two stages:
    1.  *sync_fetch* – pull page metadata and raw Markdown from Notion → SQLite.
    2.  *sync_vectorize* – chunk → embed → Milvus, controlled by per-page toggle.
    """

    def __init__(
        self,
        notion_client: NotionClient,
        processor: MarkdownProcessor,
        image_extractor: ImageExtractor,
        embedding: EmbeddingService,
        store: MilvusStore,
        state_store: SyncStateStore,
        config: Settings,
    ) -> None:
        self.notion_client = notion_client
        self.processor = processor
        self.image_extractor = image_extractor
        self.embedding = embedding
        self.store = store
        self.state_store = state_store
        self.config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sync(
        self,
        root_page_ids: list[str] | None = None,
        force_full: bool = False,
        vectorize: bool = False,
    ) -> tuple[SyncResult, VectorizeResult | None]:
        """Full sync: fetch then optionally vectorize all enabled pages.

        Backward-compatible with the old ``sync()`` signature.
        """
        fetch_result = self.sync_fetch(root_page_ids=root_page_ids, force_full=force_full)
        if not vectorize:
            return fetch_result, None
        vec_result = self.sync_vectorize()
        return fetch_result, vec_result

    def sync_fetch(
        self,
        root_page_ids: list[str] | None = None,
        force_full: bool = False,
        page_ids: list[str] | None = None,
        progress_callback: Callable[[str, str, int, int], None] | None = None,
    ) -> SyncResult:
        """Stage 1: Pull page metadata + raw Markdown from Notion → SQLite.

        No chunking, embedding, or Milvus writes happen here.
        Pages whose ``last_edited_time`` is unchanged are skipped.
        Full-root syncs remove locally stored pages that are no longer reachable.
        When a previously-indexed page is re-fetched, its ``vector_status``
        is reset to ``pending`` (the toggle state is preserved).
        """
        result = SyncResult()
        failed_page_ids: list[str] = []
        if page_ids:
            # Direct page sync: fetch metadata for each specified page
            pages: list[PageMetadata] = []
            for pid in page_ids:
                try:
                    meta = self.notion_client.get_page_metadata_with_container(pid)
                    pages.append(meta)
                except Exception:
                    logger.exception("Failed to get metadata for page %s", pid)
                    result.failed += 1
        else:
            root_ids = self._resolve_root_page_ids(root_page_ids)
            pages = self.notion_client.enumerate_pages(
                root_ids,
                out_failed_page_ids=failed_page_ids,
            )
            result.failed += len(failed_page_ids)
        current_ids = {page.page_id for page in pages}
        total_pages = len(pages) + result.failed

        for page in pages:
            try:
                action = self._fetch_page(page, force_full=force_full)
                self._increment_result(result, action)
            except RagKbError:
                logger.exception("Failed to fetch page %s", page.page_id)
                self._record_failure(page, "Fetch pipeline failed")
                result.failed += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception("Unexpected failure fetching page %s", page.page_id)
                self._record_failure(page, f"Unexpected error: {exc}")
                result.failed += 1
            if progress_callback is not None:
                progress_callback(
                    page.page_id,
                    page.title,
                    result.added + result.updated + result.skipped + result.failed,
                    total_pages,
                )

        if not page_ids:
            result.removed = self._remove_deleted_pages(current_ids)
        return result

    def sync_vectorize(
        self,
        page_ids: list[str] | None = None,
    ) -> VectorizeResult:
        """Stage 2: Chunk → embed → Milvus for pages that have the vector toggle ON.

        If *page_ids* is ``None``, all pages with ``vector_enabled=True``
        and ``vector_status='pending'`` are processed.
        """
        if page_ids is None:
            states = self.state_store.get_pending_vectorize()
        else:
            states = []
            for pid in page_ids:
                s = self.state_store.get(pid)
                if s is not None and s.vector_enabled:
                    states.append(s)

        result = VectorizeResult(total=len(states))
        embedding_service_down = False
        for state in states:
            if embedding_service_down:
                result.skipped += 1
                result.page_results.append({
                    "page_id": state.page_id,
                    "status": "skipped",
                    "error": "Embedding service unavailable",
                })
                continue
            try:
                page_result = self.vectorize_state(state)
                result.page_results.append(page_result)
                if page_result["status"] == "indexed":
                    result.indexed += 1
                elif page_result["status"] == "skipped":
                    result.skipped += 1
                else:
                    result.failed += 1
            except EmbeddingServiceError:
                logger.critical("Embedding service unavailable — stopping vectorize")
                self.state_store.update_vector_status(
                    state.page_id,
                    vector_status="failed",
                    vector_error_message="Embedding service unavailable",
                    vector_stage="failed",
                )
                result.failed += 1
                result.page_results.append({
                    "page_id": state.page_id,
                    "status": "failed",
                    "error": "Embedding service unavailable",
                })
                embedding_service_down = True
            except Exception as exc:  # noqa: BLE001
                logger.exception("Vectorize failed for page %s", state.page_id)
                self.state_store.update_vector_status(
                    state.page_id,
                    vector_status="failed",
                    vector_error_message=str(exc),
                    vector_stage="failed",
                )
                result.failed += 1
                result.page_results.append({
                    "page_id": state.page_id,
                    "status": "failed",
                    "error": str(exc),
                })

        return result

    # ------------------------------------------------------------------
    # Stage 1 helpers
    # ------------------------------------------------------------------

    def _fetch_page(
        self, page: PageMetadata, *, force_full: bool
    ) -> str:
        # Container pages — skip.
        if page.is_container:
            logger.info(
                "Skipping container page %s — only sub-page references, no own content",
                page.page_id,
            )
            try:
                self.store.delete_by_page_id(page.page_id)
            except RagKbError:
                logger.exception("Failed to delete stale chunks for container page %s", page.page_id)
            self._record_skipped_page(page, "Container page — no own content")
            return "skipped"

        old_state = self.state_store.get(page.page_id)
        if not force_full and old_state is not None and old_state.last_edited_time == page.last_edited_time:
            logger.info("Skipping unchanged page: %s", page.page_id)
            return "skipped"

        markdown, _ = self.notion_client.get_page_markdown(page.page_id, metadata=page)
        if self._is_content_light(markdown):
            logger.info(
                "Skipping page %s — no retrievable content (links/sub-pages only)",
                page.page_id,
            )
            try:
                self.store.delete_by_page_id(page.page_id)
            except RagKbError:
                logger.exception(
                    "Failed to delete stale chunks for content-light page %s",
                    page.page_id,
                )
            self._record_skipped_page(page, "No retrievable content")
            return "skipped"

        markdown = self._cache_page_images(page, markdown)

        # Determine vector state for the fetched page.
        # - New page: vector_enabled=False, vector_status='pending'
        # - Updated page that was previously indexed: keep vector_enabled, set pending
        # - Re-fetched page: preserve existing vector state
        chunk_count = 0
        image_count = 0
        vector_enabled = False
        vector_status = "pending"
        if old_state is not None:
            # Preserve old chunk/image counts if the page was already indexed
            # (data is still in Milvus; sync_fetch doesn't touch it).
            if old_state.vector_status == "indexed":
                chunk_count = old_state.chunk_count
                image_count = old_state.image_count
            vector_enabled = old_state.vector_enabled
            # If the page content changed and vector was enabled, mark for re-vectorize
            vector_status = old_state.vector_status if old_state.vector_status != "failed" else "pending"
            if vector_enabled and old_state.vector_status == "indexed":
                vector_status = "pending"

        fetched_state = PageSyncState(
            page_id=page.page_id,
            page_title=page.title,
            page_url=page.url,
            last_edited_time=page.last_edited_time,
            parent_id=page.parent_id,
            chunk_count=chunk_count,
            image_count=image_count,
            status="fetched",
            error_message=None,
            last_synced_time=self._now(),
            raw_markdown=markdown,
            vector_enabled=vector_enabled,
            vector_status=vector_status,
            vector_error_message=None,
            vector_progress=0,
            content_hash=old_state.content_hash if old_state else "",
            chunking_hash=old_state.chunking_hash if old_state else "",
            embedding_hash=old_state.embedding_hash if old_state else "",
        )
        self.state_store.upsert(fetched_state)
        logger.info(
            "Fetched page %s: %d chars markdown, vector_enabled=%s, vector_status=%s",
            page.page_id,
            len(markdown),
            vector_enabled,
            vector_status,
        )
        return "updated" if old_state is not None else "added"

    # ------------------------------------------------------------------
    # Stage 2 helpers
    # ------------------------------------------------------------------

    def _cache_page_images(self, page: PageMetadata, markdown: str) -> str:
        """Download page images to the local cache and rewrite Markdown URLs.

        Notion pre-signed URLs can expire before the async vectorization worker
        runs. Downloading during fetch keeps vectorization on stable local
        paths, while failed URLs remain in the Markdown for a later retry.
        """
        try:
            image_docs = self.image_extractor.extract(markdown, page)
        except Exception:
            logger.warning("Failed to extract images for page %s", page.page_id, exc_info=True)
            return markdown

        image_cache_dir = (
            Path(self.config.storage.data_dir).expanduser() / "images" / page.page_id
        )
        replacements: dict[str, str] = {}
        for img in image_docs:
            local = download_image(img.image_url, image_cache_dir)
            if local:
                local_url = f"/images/{page.page_id}/{Path(local).name}"
                replacements[img.image_url] = pack_image_url(local_url, img.remote_url)

        for original_url, local_url in replacements.items():
            markdown = markdown.replace(original_url, local_url)
            markdown = re.sub(
                r"!\[([^\]]*)\]\(" + re.escape(local_url) + r"\)",
                lambda match, local_url=local_url: (
                    f"![{clean_image_alt(match.group(1))}]({local_url})"
                ),
                markdown,
            )
        keep_names = set(re.findall(r"\(/images/[^/]+/([^/)]+)\)", markdown))
        cleanup_image_cache(image_cache_dir, keep_names)
        return markdown

    def _vectorize_page(self, state: PageSyncState) -> dict[str, str]:
        """Run chunking → embedding → Milvus for one page.

        Skips only when the stable content, chunking algorithm/config, and
        embedding model/config hashes all match, and the current Milvus rows
        still form a complete set of nonzero vectors at the configured dimension.
        """
        # Guard: only process pending/failed pages
        if state.vector_status not in ("pending", "failed"):
            return {"page_id": state.page_id, "status": "skipped", "error": f"vector_status is {state.vector_status}"}

        markdown = state.raw_markdown
        if not markdown:
            self.state_store.update_vector_status(
                state.page_id,
                vector_status="failed",
                vector_error_message="No markdown content",
                vector_stage="failed",
            )
            return {"page_id": state.page_id, "status": "failed", "error": "No markdown content"}

        page_meta = PageMetadata(
            page_id=state.page_id,
            title=state.page_title,
            url=state.page_url,
            last_edited_time=state.last_edited_time,
            parent_id=state.parent_id,
        )
        chunking_hash = self._compute_chunking_hash()
        embedding_hash = self._compute_embedding_hash()

        try:
            self.state_store.update_vector_status(
                state.page_id,
                vector_status="indexing",
                vector_progress=5,
                vector_stage="preparing",
            )
            image_docs = self.image_extractor.extract(markdown, page_meta)
            self.state_store.update_vector_status(
                state.page_id,
                vector_status="indexing",
                vector_progress=12,
                vector_stage="extracting_images",
            )
            self._attach_local_images(state, image_docs)
            if any(img.local_path is None for img in image_docs):
                refreshed = self._refresh_images_from_notion(
                    state,
                    page_meta,
                )
                if refreshed is not None:
                    markdown, image_docs = refreshed
        except Exception as exc:
            self.state_store.update_vector_status(
                state.page_id,
                vector_status="failed",
                vector_error_message=f"Image extraction failed: {exc}",
                vector_stage="failed",
            )
            return {"page_id": state.page_id, "status": "failed", "error": str(exc)}

        missing_images = [img for img in image_docs if img.local_path is None]
        if missing_images:
            self.state_store.update_vector_status(
                state.page_id,
                vector_status="failed",
                vector_error_message=(
                    f"{len(missing_images)} image(s) could not be cached locally"
                ),
                vector_stage="failed",
            )
            return {
                "page_id": state.page_id,
                "status": "failed",
                "error": f"{len(missing_images)} image(s) could not be cached locally",
            }

        content_hash = self._compute_content_hash(markdown, image_docs)
        self.state_store.update_vector_status(
            state.page_id,
            vector_status="indexing",
            vector_progress=18,
            vector_stage="validating",
        )
        stats = self._get_page_embedding_stats(state.page_id)
        hashes_match = (
            content_hash == state.content_hash
            and chunking_hash == state.chunking_hash
            and embedding_hash == state.embedding_hash
        )
        if hashes_match and stats is not None and self._vectors_are_complete(state, stats):
            logger.info(
                "Vectorize skip (unchanged) page %s: content=%s, chunking=%s, embedding=%s",
                state.page_id,
                content_hash[:8],
                chunking_hash[:8],
                embedding_hash[:8],
            )
            self.state_store.mark_vectorized(
                state.page_id,
                chunk_count=state.chunk_count,
                image_count=state.image_count,
                content_hash=content_hash,
                chunking_hash=chunking_hash,
                embedding_hash=embedding_hash,
            )
            return {"page_id": state.page_id, "status": "skipped", "error": "page unchanged"}

        self.state_store.update_vector_status(
            state.page_id,
            vector_status="indexing",
            vector_progress=25,
            vector_stage="chunking",
        )

        try:
            # Chunk
            self.state_store.update_vector_status(
                state.page_id,
                vector_status="indexing",
                vector_progress=28,
                vector_stage="chunking",
            )
            chunks = self.processor.split(markdown, page_meta)

            self.state_store.update_vector_status(
                state.page_id,
                vector_status="indexing",
                vector_progress=38,
                vector_stage="interleaving",
            )

            # Interleave text chunks and image docs into a single sequence
            combined: list[tuple[int, object]] = []
            for chunk in chunks:
                combined.append((
                    chunk.source_offset if chunk.source_offset is not None else 10**9,
                    chunk,
                ))
            for img in image_docs:
                combined.append((
                    img.source_offset if img.source_offset is not None else 10**9,
                    img,
                ))
            combined.sort(key=lambda x: x[0])
            for i, (_, item) in enumerate(combined):
                item.metadata.chunk_index = i

            # Rewrite image URLs in chunk text and raw Markdown to local cache URLs
            url_map: dict[str, str] = {}
            for img in image_docs:
                if img.local_path:
                    filename = Path(img.local_path).name
                    url_map[img.image_url] = pack_image_url(
                        f"/images/{state.page_id}/{filename}",
                        img.remote_url,
                    )
            if url_map:
                for chunk in chunks:
                    for s3_url, local_url in url_map.items():
                        chunk.text = chunk.text.replace(s3_url, local_url)

            # Bound inputs before remote embedding. Image context text can be a
            # full paragraph (tens of thousands of chars) and would blow the
            # model's 60k token context when combined with base64 image data.
            for chunk in chunks:
                if len(chunk.text) > 8000:
                    logger.warning(
                        "Truncating chunk text before embedding for page %s: %d chars",
                        state.page_id,
                        len(chunk.text),
                    )
                    chunk.text = chunk.text[:8000]
            for img in image_docs:
                max_image_context = self.config.chunking.image_context_max_chars
                if len(img.context_text) > max_image_context:
                    img.context_text = img.context_text[:max_image_context]

            # Embed
            self.state_store.update_vector_status(
                state.page_id,
                vector_status="indexing",
                vector_progress=50,
                vector_stage="embedding",
            )
            total_items = len(chunks) + len(image_docs)

            def on_embedding_progress(completed: int, total: int) -> None:
                if total <= 0:
                    return
                progress = 50 + int(29 * completed / total)
                self.state_store.update_vector_status(
                    state.page_id,
                    vector_status="indexing",
                    vector_progress=min(progress, 79),
                    vector_stage="embedding",
                )

            embeddings = self._embed_chunks_and_images(
                chunks,
                image_docs,
                progress_callback=on_embedding_progress if total_items else None,
            )

            zero_count = sum(1 for v in embeddings if all(x == 0.0 for x in v))
            if zero_count > 0:
                logger.error(
                    "Page %s: %d/%d chunks have zero vectors; preserving previous vectors",
                    state.page_id, zero_count, len(embeddings),
                )
                self.state_store.update_vector_status(
                    state.page_id,
                    vector_status="failed",
                    vector_error_message=f"{zero_count} embedding item(s) returned zero vectors",
                    vector_stage="failed",
                )
                return {
                    "page_id": state.page_id,
                    "status": "failed",
                    "error": f"{zero_count} embedding item(s) returned zero vectors",
                }

            # Normalize stored fields to Milvus schema limits before writing.
            # Milvus VARCHAR caps chunk_text at 65535 and image_url at 2048.
            self.state_store.update_vector_status(
                state.page_id,
                vector_status="indexing",
                vector_progress=80,
                vector_stage="storing",
            )
            for chunk in chunks:
                if len(chunk.text) > 60000:
                    logger.warning(
                        "Truncating oversized chunk for page %s: %d chars",
                        state.page_id,
                        len(chunk.text),
                    )
                    chunk.text = chunk.text[:60000]
            for img in image_docs:
                if len(img.context_text) > 60000:
                    img.context_text = img.context_text[:60000]
                if img.local_path:
                    filename = Path(img.local_path).name
                    img.image_url = pack_image_url(
                        f"/images/{state.page_id}/{filename}",
                        img.remote_url,
                    )
                elif len(img.image_url) > 64000:
                    img.image_url = ""

            # Store to Milvus
            self.store.upsert_page(state.page_id, chunks, image_docs, embeddings)

            # Mark as indexed and persist all pipeline hashes atomically.
            self.state_store.mark_vectorized(
                state.page_id,
                chunk_count=len(chunks),
                image_count=len(image_docs),
                content_hash=content_hash,
                chunking_hash=chunking_hash,
                embedding_hash=embedding_hash,
            )
            logger.info(
                "Vectorized page %s: %d chunks, %d images",
                state.page_id, len(chunks), len(image_docs),
            )
            return {"page_id": state.page_id, "status": "indexed", "error": ""}
        except Exception as exc:
            logger.exception("_vectorize_page failed for %s", state.page_id)
            self.state_store.update_vector_status(
                state.page_id,
                vector_status="failed",
                vector_error_message=str(exc)[:500],
                vector_stage="failed",
            )
            return {"page_id": state.page_id, "status": "failed", "error": str(exc)}

    def vectorize_state(self, state: PageSyncState) -> dict[str, str]:
        """Vectorize one page state and return the page-level result."""
        return self._vectorize_page(state)

    def _attach_local_images(self, state: PageSyncState, image_docs: list[ImageDoc]) -> None:
        """Resolve each image document to its local cached file when possible."""
        image_cache_dir = (
            Path(self.config.storage.data_dir).expanduser() / "images" / state.page_id
        )
        for img in image_docs:
            try:
                local = download_image(img.image_url, image_cache_dir)
                if local:
                    img.local_path = local
            except Exception:
                logger.warning("Failed to resolve cached image %s", img.image_url, exc_info=True)

    def _refresh_images_from_notion(
        self,
        state: PageSyncState,
        page_meta: PageMetadata,
    ) -> tuple[str, list[ImageDoc]] | None:
        """Re-fetch a page when local image files are missing or expired."""
        try:
            fresh_markdown, _ = self.notion_client.get_page_markdown(
                state.page_id,
                metadata=page_meta,
            )
            fresh_markdown = self._cache_page_images(page_meta, fresh_markdown)
            fresh_image_docs = self.image_extractor.extract(fresh_markdown, page_meta)
            self._attach_local_images(state, fresh_image_docs)
            self.state_store.update_raw_markdown(state.page_id, fresh_markdown)
            return fresh_markdown, fresh_image_docs
        except Exception:
            logger.warning(
                "Failed to refresh images for page %s from Notion",
                state.page_id,
                exc_info=True,
            )
            return None

    def _compute_content_hash(
        self,
        markdown: str,
        image_docs: list[ImageDoc],
    ) -> str:
        """Hash Markdown with image URLs replaced by stable content identities."""
        normalized = markdown
        for img in image_docs:
            if img.local_path:
                try:
                    fingerprint = hashlib.sha256(
                        Path(img.local_path).read_bytes()
                    ).hexdigest()
                except OSError:
                    fingerprint = hashlib.sha256(img.alt.encode()).hexdigest()
            else:
                # Keep missing images stable by URL-independent alt text.
                stable_alt = img.alt.split("?", 1)[0]
                fingerprint = hashlib.sha256(stable_alt.encode()).hexdigest()
            if img.image_url:
                raw_image_ref = pack_image_url(img.image_url, img.remote_url)
                normalized = normalized.replace(raw_image_ref, f"image:{fingerprint}")
                normalized = normalized.replace(img.image_url, f"image:{fingerprint}")
        return hashlib.sha256(normalized.encode()).hexdigest()

    def _get_page_embedding_stats(self, page_id: str) -> dict[str, int] | None:
        """Return embedding stats, or None when Milvus cannot be inspected."""
        try:
            return self.store.get_page_embedding_stats(page_id)
        except Exception:
            logger.warning("Failed to read embedding stats for %s", page_id, exc_info=True)
            return None

    def _vectors_are_complete(
        self,
        state: PageSyncState,
        stats: dict[str, int],
    ) -> bool:
        """Return True when stored vectors match the expected complete result.

        An empty Milvus result must not be mistaken for a valid indexed page.
        """
        expected = state.chunk_count + state.image_count
        if expected == 0:
            return stats.get("total", -1) == 0
        return (
            stats.get("total", -1) == expected
            and stats.get("nonzero", -1) == expected
            and stats.get("dim", -1) == self.config.embedding.dimensions
        )

    def _compute_chunking_hash(self) -> str:
        """Hash of current chunking configuration parameters."""
        cfg = self.config.chunking
        params = {
            "version": CHUNKING_VERSION,
            "header_levels": cfg.header_levels,
            "max_chunk_size": cfg.max_chunk_size,
            "min_chunk_size": cfg.min_chunk_size,
            "preserve_tables": cfg.preserve_tables,
            "preserve_code_blocks": cfg.preserve_code_blocks,
            "image_context_window": cfg.image_context_window,
            "image_context_max_chars": cfg.image_context_max_chars,
            "protected_hard_limit": PROTECTED_HARD_LIMIT,
        }
        return hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()

    def _compute_embedding_hash(self) -> str:
        """Hash the Embedding pipeline version and behavior-affecting config."""
        params = {
            "version": EMBEDDING_VERSION,
            **self.config.embedding.model_dump(exclude={"api_key"}),
        }
        return hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _resolve_root_page_ids(self, root_page_ids: list[str] | None) -> list[str]:
        resolved = root_page_ids if root_page_ids is not None else self.config.notion.root_page_ids
        if not resolved:
            raise ConfigError("No root_page_ids provided and none configured")
        return resolved

    def _is_subpage_link_list(self, markdown: str) -> bool:
        """Return True when a page is only a list of links to Notion sub-pages."""
        text = re.sub(r"^---\n.*?\n---\n?", "", markdown, flags=re.DOTALL)
        if _IMAGE_MARKDOWN_RE.search(text):
            return False
        links = re.findall(r"\[[^\]]*\]\(([^)]+)\)", text)
        if len(links) < 2:
            return False
        notion_links = [u for u in links if "notion" in u.lower()]
        if len(notion_links) < 2 or len(notion_links) < len(links) * 0.8:
            return False
        without_links = re.sub(r"\[[^\]]*\]\([^)]+\)", "", text)
        meaningful = "".join(
            c for c in without_links if c.isalnum() or "\u4e00" <= c <= "\u9fff"
        )
        return len(meaningful) < 40

    def _is_content_light(self, markdown: str) -> bool:
        """Return True when a page has no retrievable text content."""
        text = re.sub(r"^---\n.*?\n---\n?", "", markdown, flags=re.DOTALL)
        if _IMAGE_MARKDOWN_RE.search(text):
            return False
        if self._is_subpage_link_list(text):
            return True
        text = re.sub(r"\[([^\]]*)\]\([^\)]+\)", r"\1", text)
        text = re.sub(r"!\[([^\]]*)\]\([^\)]+\)", "", text)
        text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
        meaningful = "".join(
            c for c in text if c.isalnum() or "\u4e00" <= c <= "\u9fff"
        )
        return len(meaningful) < 80

    def _embed_chunks_and_images(
        self,
        chunks: list[Chunk],
        image_docs: list[ImageDoc],
        *,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[list[float]]:
        items: list[EmbeddingItem] = [
            EmbeddingItem(type="text", text=chunk.text)
            for chunk in chunks
        ]
        for img in image_docs:
            if img.local_path:
                b64 = image_to_base64(img.local_path)
                if b64:
                    items.append(
                        EmbeddingItem(type="image_url", image_url=b64, text=img.context_text)
                    )
                    continue
            # Never send expiring remote URLs to DashScope. Missing files are
            # refreshed from Notion before vectorization, so this branch is a
            # final text-only safety net.
            items.append(
                EmbeddingItem(
                    type="text",
                    text=f"[Image: {img.alt}] {img.context_text}",
                )
            )
        if not items:
            return []
        return self.embedding.embed(items, progress_callback=progress_callback)

    def _record_failure(self, page: PageMetadata, message: str) -> None:
        failed_state = PageSyncState(
            page_id=page.page_id,
            page_title=page.title,
            page_url=page.url,
            last_edited_time=page.last_edited_time,
            parent_id=page.parent_id,
            chunk_count=0,
            image_count=0,
            status="failed",
            error_message=message,
            last_synced_time=self._now(),
        )
        try:
            self.state_store.upsert(failed_state)
        except RagKbError:
            logger.exception("Failed to record failure state for %s", page.page_id)

    def _remove_deleted_pages(self, current_ids: set[str]) -> int:
        removed = 0
        for state in self.state_store.list_all():
            if state.page_id not in current_ids:
                logger.info("Removing deleted page: %s", state.page_id)
                try:
                    self.store.delete_by_page_id(state.page_id)
                    self.state_store.delete(state.page_id)
                    removed += 1
                except RagKbError:
                    logger.exception("Failed to remove deleted page %s", state.page_id)
        return removed

    @staticmethod
    def _increment_result(result: SyncResult, action: str) -> None:
        if action == "added":
            result.added += 1
        elif action == "updated":
            result.updated += 1
        elif action == "skipped":
            result.skipped += 1
        else:
            raise ValueError(f"Unknown sync action: {action}")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _record_skipped_page(self, page: PageMetadata, message: str) -> None:
        """Persist an invalid page as skipped so it is not refetched every sync."""
        try:
            self.state_store.upsert(PageSyncState(
                page_id=page.page_id,
                page_title=page.title,
                page_url=page.url,
                last_edited_time=page.last_edited_time,
                parent_id=page.parent_id,
                chunk_count=0,
                image_count=0,
                status="skipped",
                error_message=message,
                last_synced_time=self._now(),
            ))
        except Exception:
            logger.exception("Failed to record skipped state for %s", page.page_id)
