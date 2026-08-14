from __future__ import annotations

import logging
from typing import Any, Callable

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from rag_notion_kb.config import EmbeddingConfig
from rag_notion_kb.exceptions import ConfigError, EmbeddingError, EmbeddingServiceError
from rag_notion_kb.models import EmbeddingItem

logger = logging.getLogger(__name__)

_NAN_FLT: float = float("nan")
# Bump when embedding request construction or vector normalization changes.
EMBEDDING_VERSION = 1


class EmbeddingService:
    """Client for DashScope native multimodal embedding API (qwen3-vl-embedding)."""

    def __init__(self, config: EmbeddingConfig) -> None:
        self.config = config
        if not config.api_key.strip():
            raise ConfigError("RAG_KB_EMBEDDING__API_KEY is not configured")
        self._client = httpx.Client(
            base_url=config.base_url,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            timeout=300.0,
        )

    def embed(
        self,
        items: list[EmbeddingItem],
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[list[float]]:
        """Embed a list of text/image items, batching per config.batch_size.

        Returns zero vectors for failed batches so the overall pipeline continues.
        """
        results: list[list[float]] = []
        batch_size = self.config.batch_size
        for i in range(0, len(items), batch_size):
            batch = items[i : i + batch_size]
            try:
                vectors = self._embed_batch(batch)
            except EmbeddingError as exc:
                logger.error(
                    "Embedding batch %d failed, returning zero vectors: %s",
                    i // batch_size,
                    exc,
                )
                vectors = [[0.0] * self.config.dimensions for _ in batch]
            # EmbeddingServiceError is NOT caught here — it propagates to SyncService
            results.extend(vectors)
            if progress_callback is not None:
                try:
                    progress_callback(min(len(items), i + len(batch)), len(items))
                except Exception:
                    logger.exception("Embedding progress callback failed")
        return results

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=8),
        retry=retry_if_exception_type((EmbeddingError, httpx.HTTPStatusError)),
        reraise=True,
    )
    def _embed_batch(self, items: list[EmbeddingItem]) -> list[list[float]]:
        contents = [self._build_content(item) for item in items]
        for idx, item in enumerate(items):
            if item.type == "image_url":
                logger.info(
                    "Embedding request item %d: type=image_url, image_prefix=%r",
                    idx,
                    (item.image_url or "")[:200],
                )
            else:
                logger.info(
                    "Embedding request item %d: type=text, text_prefix=%r",
                    idx,
                    (item.text or "")[:120],
                )
        payload: dict[str, Any] = {
            "model": self.config.model,
            "input": {"contents": contents},
            "parameters": {"dimension": self.config.dimensions},
        }
        try:
            response = self._client.post("", json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            resp = exc.response
            resp_text = resp.text[:500]
            logger.error("DashScope HTTP error: %s %s", resp.status_code, resp_text)
            if resp.status_code in (401, 403, 404):
                raise EmbeddingServiceError(
                    f"DashScope embedding service unavailable (HTTP {resp.status_code})"
                ) from exc
            # Image download failure: retry once with text-only fallback
            if resp.status_code == 400 and "download" in resp_text.lower():
                logger.warning(
                    "Image URL download failed, retrying batch with text-only fallback"
                )
                return self._embed_batch_text_only(items)
            if resp.status_code == 400:
                for idx, item in enumerate(items):
                    if item.type == "image_url":
                        logger.error(
                            "Rejected image item %d: image=%r, text=%r",
                            idx,
                            (item.image_url or "")[:400],
                            (item.text or "")[:120],
                        )
            raise EmbeddingError(
                f"DashScope embedding batch returned {resp.status_code}"
            ) from exc
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
            raise EmbeddingServiceError(
                f"DashScope embedding service unreachable: {exc}"
            ) from exc

        data = response.json()
        output = data.get("output", {})
        embeddings = output.get("embeddings", [])
        # Sort by text_index to preserve input order
        embeddings.sort(key=lambda e: e.get("text_index", e.get("index", 0)))
        return [self._normalize(emb.get("embedding", [])) for emb in embeddings]

    def _embed_batch_text_only(self, items: list[EmbeddingItem]) -> list[list[float]]:
        """Retry embedding with images downgraded to text-only."""
        text_items: list[EmbeddingItem] = []
        for item in items:
            if item.type == "image_url":
                text = item.text or ""
                if item.image_url:
                    text = f"[Image: {item.image_url[-40:]}] {text}".strip()
                text_items.append(EmbeddingItem(type="text", text=text))
            else:
                text_items.append(item)
        contents = [self._build_content(item) for item in text_items]
        payload: dict[str, Any] = {
            "model": self.config.model,
            "input": {"contents": contents},
            "parameters": {"dimension": self.config.dimensions},
        }
        response = self._client.post("", json=payload)
        response.raise_for_status()
        data = response.json()
        output = data.get("output", {})
        embeddings = output.get("embeddings", [])
        embeddings.sort(key=lambda e: e.get("text_index", e.get("index", 0)))
        return [self._normalize(emb.get("embedding", [])) for emb in embeddings]

    def _build_content(self, item: EmbeddingItem) -> dict[str, Any]:
        """Build a single content dict for the DashScope multimodal embedding API."""
        content: dict[str, Any] = {}
        if item.type == "image_url" and item.image_url:
            content["image"] = item.image_url
            if item.text:
                content["text"] = item.text
        elif item.type == "text":
            content["text"] = item.text or ""
        else:
            content["text"] = item.text or ""
        return content

    def _normalize(self, vector: list[float]) -> list[float]:
        dim = self.config.dimensions
        if len(vector) > dim:
            return vector[:dim]
        if len(vector) < dim:
            return vector + [0.0] * (dim - len(vector))
        return vector

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()
