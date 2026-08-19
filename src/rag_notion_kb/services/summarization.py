from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from rag_notion_kb.config import SummarizationConfig
from rag_notion_kb.exceptions import SummarizationError

logger = logging.getLogger(__name__)


class SummarizationService:
    """OpenAI-compatible DeepSeek client for post-retrieval summarization."""

    def __init__(self, config: SummarizationConfig) -> None:
        self.config = config
        self._client = httpx.Client(
            base_url=config.base_url,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            timeout=config.timeout_seconds,
        )

    def summarize(self, query: str, passages: list[tuple[str, str]]) -> str:
        """Deduplicate and summarize retrieved passages for *query*.

        Each passage is a ``(text, source)`` tuple. The DeepSeek model returns
        one Markdown text block.
        """
        if not self.config.api_key.strip():
            raise SummarizationError("RAG_KB_SUMMARIZATION__API_KEY is not configured")

        passage_blocks: list[str] = []
        for index, (text, source) in enumerate(passages, start=1):
            clean_text = (text or "").strip() or "[image: no available text]"
            passage_blocks.append(f"[{index}] Source: {source}\n{clean_text}")

        user_content = (
            f"Query: {query}\n\n"
            "Retrieved passages:\n"
            + "\n\n".join(passage_blocks)
        )
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": self.config.prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.2,
            "stream": False,
        }

        try:
            data = self._call_api(payload)
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException) as exc:
            raise SummarizationError(f"Summarization API request failed: {exc}") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise SummarizationError("Invalid summarization API response structure") from exc

        if not isinstance(content, str) or not content.strip():
            raise SummarizationError("Summarization API returned empty content")
        return content.strip()

    def _call_api(self, payload: dict[str, Any]) -> dict[str, Any]:
        retrying = Retrying(
            stop=stop_after_attempt(self.config.max_retries),
            wait=wait_exponential_jitter(initial=1, max=8),
            retry=retry_if_exception_type(
                (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException)
            ),
            reraise=True,
        )
        for attempt in retrying:
            with attempt:
                response = self._client.post("/chat/completions", json=payload)
                response.raise_for_status()
                return response.json()
        raise SummarizationError("Summarization API retry loop ended unexpectedly")

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()
