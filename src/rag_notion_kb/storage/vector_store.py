from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from rag_notion_kb.models import Chunk, ImageDoc, SearchHit


class VectorStore(ABC):
    """Abstract interface for vector storage backends."""

    @abstractmethod
    def init_collection(self, dim: int) -> None: ...

    @abstractmethod
    def upsert_page(
        self,
        page_id: str,
        chunks: list[Chunk],
        image_docs: list[ImageDoc],
        embeddings: list[list[float]],
    ) -> int: ...

    @abstractmethod
    def delete_by_page_id(self, page_id: str) -> int: ...

    @abstractmethod
    def search_dense(
        self,
        vector: list[float],
        limit: int,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchHit]: ...

    @abstractmethod
    def search_sparse(
        self,
        query_text: str,
        limit: int,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchHit]: ...

    @abstractmethod
    def get_page_chunks(self, page_id: str) -> list[SearchHit]: ...

    @abstractmethod
    def stats(self) -> dict[str, Any]: ...
