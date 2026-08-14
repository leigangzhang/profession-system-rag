from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from rag_notion_kb.config import RerankerConfig
from rag_notion_kb.exceptions import ConfigError, RetrievalError
from rag_notion_kb.models import RankCandidate

logger = logging.getLogger(__name__)


class RerankerService:
    """Client for a DashScope-compatible rerank API."""

    def __init__(self, config: RerankerConfig) -> None:
        self.config = config
        if not config.api_key.strip():
            raise ConfigError("RAG_KB_RERANKER__API_KEY is not configured")
        self._client = httpx.Client(
            base_url=config.base_url,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    def rerank(
        self,
        query: str,
        candidates: list[RankCandidate],
        model: str | None = None,
    ) -> list[tuple[int, float]]:
        """Rerank candidates and return sorted (original_index, score) pairs.

        Args:
            query: The user query.
            candidates: Records to rerank. Each may contain text and/or image_url.

        Returns:
            Sorted list of ``(original_index, relevance_score)`` tuples, highest first.

        Args:
            model: Optional per-request model override. When omitted, the model
                configured for the service is used.

        Raises:
            RetrievalError: If the rerank API returns an error or invalid payload.
        """
        if not candidates:
            return []

        documents = [
            self._build_document(c)
            for c in candidates
        ]
        payload: dict[str, Any] = {
            "model": model or self.config.model,
            "input": {
                "query": {"text": query},
                "documents": documents
            },
            "parameters": {
                "top_n": len(candidates),
                "return_documents": False,
            },
        }
        try:
            response_data = self._call_api(payload)
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException) as exc:
            raise RetrievalError(f"Rerank API request failed: {exc}") from exc

        results = self._parse_response(response_data, len(candidates))
        return sorted(results, key=lambda item: item[1], reverse=True)

    def _build_document(self, candidate: RankCandidate) -> dict[str, str]:
        """Build a DashScope rerank API document from a candidate."""
        doc: dict[str, str] = {}
        if candidate.text:
            doc["text"] = candidate.text
        if candidate.image_url:
            doc["image"] = candidate.image_url
        if not doc:
            doc["text"] = ""
        return doc

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=8),
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException)),
        reraise=True,
    )
    def _call_api(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post("", json=payload)
        response.raise_for_status()
        return response.json()

    def _parse_response(
        self,
        response_data: dict[str, Any],
        candidate_count: int,
    ) -> list[tuple[int, float]]:
        try:
            results = response_data.get("output", {}).get("results", [])
        except AttributeError as exc:
            raise RetrievalError("Invalid rerank API response structure") from exc

        parsed: list[tuple[int, float]] = []
        for item in results:
            index = item.get("index")
            score = item.get("relevance_score")
            if index is None or score is None:
                raise RetrievalError(f"Malformed rerank result item: {item}")
            if not isinstance(index, int) or not 0 <= index < candidate_count:
                raise RetrievalError(f"Rerank result index out of range: {index}")
            parsed.append((index, float(score)))
        return parsed

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()
