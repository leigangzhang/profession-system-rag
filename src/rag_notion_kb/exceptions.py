from __future__ import annotations

from tenacity import retry, stop_after_attempt, wait_exponential_jitter


class RagKbError(Exception):
    """Base exception for the RAG Notion KB system."""


class ConfigError(RagKbError):
    """Raised when configuration is invalid or missing."""


class NotionApiError(RagKbError):
    """Raised when Notion API call fails."""


class EmbeddingError(RagKbError):
    """Raised when a specific embedding batch fails (non-fatal, zero vectors used)."""


class EmbeddingServiceError(RagKbError):
    """Fatal: the entire embedding service is unavailable. Stop sync immediately."""


class StorageError(RagKbError):
    """Raised when vector store or sync state operation fails."""


class RetrievalError(RagKbError):
    """Raised when retrieval pipeline fails."""


def with_notion_retry(func):
    """Retry decorator for idempotent Notion API reads."""
    return retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=8),
        retry=lambda s: isinstance(s.outcome.exception(), NotionApiError),
        reraise=True,
    )(func)
