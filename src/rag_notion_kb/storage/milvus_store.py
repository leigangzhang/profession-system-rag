from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import warnings

from pymilvus import MilvusClient

warnings.filterwarnings("ignore", category=DeprecationWarning, module="pymilvus")

from rag_notion_kb.exceptions import StorageError
from rag_notion_kb.models import Chunk, ChunkMetadata, ChunkType, ImageDoc, SearchHit
from rag_notion_kb.storage.schema import COLLECTION_SCHEMA
from rag_notion_kb.storage.vector_store import VectorStore

logger = logging.getLogger(__name__)

_PAGE_ID_RE = re.compile(
    r"^[0-9a-fA-F]{32}$|^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_SAFE_FILTER_VALUE_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
_EDITED_AFTER_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def validate_page_id(page_id: str) -> bool:
    """Return True when *page_id* is a safe Notion page identifier."""
    return bool(_PAGE_ID_RE.fullmatch(page_id))


_OUTPUT_FIELDS = [
    "id",
    "chunk_text",
    "page_id",
    "page_title",
    "page_url",
    "header_path",
    "header_level",
    "last_edited_time",
    "chunk_index",
    "chunk_type",
    "image_url",
]


class MilvusStore(VectorStore):
    """Milvus-backed vector store with dense + BM25 sparse retrieval."""

    # File where the web server writes its Milvus gRPC address so the
    # CLI can connect as a client instead of starting its own server.
    _ADDR_FILE = ".milvus_addr"

    def __init__(
        self,
        uri: str | Path,
        collection_name: str = "rag_kb_chunks",
        dim: int = 2048,
    ) -> None:
        self.uri = str(uri)
        self.collection_name = collection_name
        self.dim = dim
        self._is_owner = False
        self.client = self._connect_or_create()
        self.init_collection()

    def _connect_or_create(self) -> MilvusClient:
        """Connect to an existing Milvus Lite server if available, otherwise start one.

        When an address file exists but the server at that address is dead
        (e.g. the owning process was killed), the stale file is removed and
        a new server is started automatically.
        """
        from pathlib import Path as _Path

        db_dir = _Path(self.uri).expanduser().parent
        addr_file = db_dir / self._ADDR_FILE
        logger.debug("Milvus addr file path: %s (exists=%s)", addr_file, addr_file.exists())

        if addr_file.exists():
            self._is_owner = False
            addr_data = addr_file.read_text().strip()
            logger.info("Found Milvus addr file: %s", addr_data)
            host, port_str = addr_data.split(":")
            port = int(port_str)
            try:
                logger.info("Connecting to existing Milvus server at %s:%s", host, port)
                return MilvusClient(uri=f"http://{host}:{port}")
            except Exception:
                logger.warning(
                    "Stale Milvus addr file — server at %s:%s is not reachable, "
                    "removing file and starting a new local server",
                    host, port,
                )
                try:
                    addr_file.unlink(missing_ok=True)
                except Exception:
                    pass

        # Start a new Milvus Lite server (no addr file, or stale file was just removed).
        self._is_owner = True
        logger.info("Starting new local Milvus server (addr_file=%s)", addr_file)
        from milvus_lite.server_manager import server_manager_instance

        db_path = str(_Path(self.uri).expanduser().resolve())
        server_uri = server_manager_instance.start_and_get_uri(db_path)
        if server_uri is None:
            raise StorageError("Failed to start Milvus Lite server")
        try:
            addr = server_uri.replace("http://", "").replace("https://", "")
            addr_file.write_text(addr)
            logger.info("Milvus server address written to %s: %s", addr_file, addr)
        except Exception:
            logger.debug("Could not write Milvus address file")
        return MilvusClient(uri=server_uri)

    def close(self) -> None:
        """Close the client and remove the shared address file if we own it."""
        try:
            self.client.close()
        except Exception:
            pass
        if self._is_owner:
            addr_file = Path(self.uri).expanduser().parent / self._ADDR_FILE
            try:
                addr_file.unlink(missing_ok=True)
            except Exception:
                pass

    def init_collection(self, dim: int | None = None) -> None:
        """Create the collection if it does not already exist."""
        target_dim = dim or self.dim
        if self.client.has_collection(self.collection_name):
            logger.info("Collection already exists: %s", self.collection_name)
            return
        try:
            self.client.create_collection(
                collection_name=self.collection_name,
                schema=COLLECTION_SCHEMA,
                dimension=target_dim,
            )
        except Exception as exc:
            raise StorageError(f"Failed to create Milvus collection: {exc}") from exc

    def upsert_page(
        self,
        page_id: str,
        chunks: list[Chunk],
        image_docs: list[ImageDoc],
        embeddings: list[list[float]],
    ) -> int:
        """Replace all chunks for a page, restoring the old rows on insert failure."""
        return self.replace_page(page_id, chunks, image_docs, embeddings)

    def replace_page(
        self,
        page_id: str,
        chunks: list[Chunk],
        image_docs: list[ImageDoc],
        embeddings: list[list[float]],
    ) -> int:
        """Replace a page's vectors with a best-effort rollback.

        Milvus Lite does not expose cross-collection transactions. The previous
        rows are snapshotted before deletion and restored if inserting the new
        batch fails, which prevents ordinary transient failures from leaving a
        page partially indexed. A process crash between delete and insert is
        recovered by the worker's stale-state reset on the next start.
        """
        rows = self._build_rows(chunks, image_docs, embeddings)
        previous_rows = self._raw_page_rows(page_id)
        self.delete_by_page_id(page_id)
        if rows:
            try:
                self.client.insert(self.collection_name, data=rows)
            except Exception as exc:
                if previous_rows:
                    try:
                        self.client.insert(self.collection_name, data=previous_rows)
                    except Exception:
                        logger.exception(
                            "Failed to restore previous chunks for page %s after insert failure",
                            page_id,
                        )
                raise StorageError(f"Failed to insert chunks for {page_id}: {exc}") from exc
        return len(rows)

    def delete_by_page_id(self, page_id: str) -> int:
        """Remove all vectors belonging to *page_id*."""
        try:
            result = self.client.delete(
                self.collection_name,
                filter=self._page_filter_expr(page_id),
            )
        except Exception as exc:
            raise StorageError(f"Failed to delete chunks for {page_id}: {exc}") from exc
        return len(result) if isinstance(result, list) else 0

    def search_dense(
        self,
        vector: list[float],
        limit: int,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        expr = self._build_filter_expr(filters)
        try:
            results = self.client.search(
                collection_name=self.collection_name,
                data=[vector],
                anns_field="dense_vector",
                limit=limit,
                filter=expr,
                output_fields=_OUTPUT_FIELDS,
                search_params={"metric_type": "COSINE"},
            )
        except Exception as exc:
            raise StorageError(f"Dense search failed: {exc}") from exc
        return self._hits_from_search(results[0] if results else [])

    def search_sparse(
        self,
        query_text: str,
        limit: int,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        expr = self._build_filter_expr(filters)
        try:
            results = self.client.search(
                collection_name=self.collection_name,
                data=[query_text],
                anns_field="sparse_vector",
                limit=limit,
                filter=expr,
                output_fields=_OUTPUT_FIELDS,
                search_params={"metric_type": "BM25"},
            )
        except Exception as exc:
            raise StorageError(f"Sparse search failed: {exc}") from exc
        return self._hits_from_search(results[0] if results else [])

    def get_page_chunks(self, page_id: str) -> list[SearchHit]:
        try:
            results = self.client.query(
                collection_name=self.collection_name,
                filter=self._page_filter_expr(page_id),
                output_fields=_OUTPUT_FIELDS,
            )
        except Exception as exc:
            raise StorageError(f"Query page chunks failed for {page_id}: {exc}") from exc
        hits = [self._to_search_hit(fields, 0.0) for fields in results]
        hits.sort(key=lambda h: h.metadata.chunk_index)
        return hits

    def get_page_chunks_with_vectors(
        self, page_id: str
    ) -> list[tuple[SearchHit, bool]]:
        """Return page chunks together with their dense-vector presence flag.

        This avoids the N+1 queries previously made by the web UI when
        inspecting a page.
        """
        try:
            results = self.client.query(
                collection_name=self.collection_name,
                filter=self._page_filter_expr(page_id),
                output_fields=_OUTPUT_FIELDS + ["dense_vector"],
            )
        except Exception as exc:
            raise StorageError(f"Query page chunks failed for {page_id}: {exc}") from exc

        pairs: list[tuple[SearchHit, bool]] = []
        for fields in results:
            vector = fields.get("dense_vector", [])
            nonzero = bool(vector) and not all(v == 0.0 for v in vector)
            pairs.append((self._to_search_hit(fields, 0.0), nonzero))
        pairs.sort(key=lambda pair: pair[0].metadata.chunk_index)
        return pairs

    def get_page_embedding_stats(self, page_id: str) -> dict[str, Any]:
        """Return embedding stats for a page: total, nonzero, zero counts and sample."""
        try:
            results = self.client.query(
                collection_name=self.collection_name,
                filter=self._page_filter_expr(page_id),
                output_fields=["id", "dense_vector", "chunk_index"],
            )
        except Exception as exc:
            raise StorageError(f"Query embeddings failed for {page_id}: {exc}") from exc

        total = len(results)
        zero_count = 0
        sample_first5: list[float] = []
        actual_dim: int | None = None
        for row in results:
            vec = row.get("dense_vector", [])
            if vec and actual_dim is None:
                actual_dim = len(vec)
            if vec and not sample_first5:
                sample_first5 = vec[:5]
            if not vec or all(v == 0.0 for v in vec):
                zero_count += 1
        return {
            "total": total,
            "nonzero": total - zero_count,
            "zero": zero_count,
            "dim": actual_dim if actual_dim is not None else self.dim,
            "sample_first5": sample_first5,
        }

    def stats(self) -> dict[str, Any]:
        try:
            raw_stats = self.client.get_collection_stats(self.collection_name)
            total = int(raw_stats.get("row_count", 0))
        except Exception as exc:
            raise StorageError(f"Failed to read Milvus stats: {exc}") from exc
        return {"total_chunks": total}

    def _build_filter_expr(self, filters: dict[str, Any] | None) -> str:
        if not filters:
            return ""

        known_fields = {
            "page_ids",
            "header_level",
            "chunk_type",
            "page_title",
            "edited_after",
        }
        unknown_fields = set(filters) - known_fields
        if unknown_fields:
            joined = ", ".join(sorted(unknown_fields))
            raise StorageError(f"Unsupported filter field(s): {joined}")

        clauses: list[str] = []

        def _value_list(values: Any) -> list[str]:
            if values is None:
                return []
            if isinstance(values, list):
                return values
            return [values]

        page_ids = filters.get("page_ids")
        if page_ids:
            for page_id in _value_list(page_ids):
                if not isinstance(page_id, str) or not _SAFE_FILTER_VALUE_RE.fullmatch(page_id):
                    raise StorageError(f"Invalid page_id in filters: {page_id!r}")
            quoted = ", ".join(json.dumps(page_id) for page_id in _value_list(page_ids))
            clauses.append(f"page_id in [{quoted}]")

        header_level = filters.get("header_level")
        if header_level is not None:
            levels = _value_list(header_level)
            normalized: list[int] = []
            for level in levels:
                try:
                    parsed = int(level)
                except (TypeError, ValueError) as exc:
                    raise StorageError(f"Invalid header_level in filters: {level!r}") from exc
                if parsed < 1 or parsed > 6:
                    raise StorageError(f"Invalid header_level in filters: {level!r}")
                normalized.append(parsed)
            if len(normalized) == 1:
                clauses.append(f"header_level == {normalized[0]}")
            else:
                clauses.append(f"header_level in [{', '.join(map(str, normalized))}]")

        chunk_types = filters.get("chunk_type")
        if chunk_types:
            allowed_types = {item.value for item in ChunkType}
            normalized_types = [str(item) for item in _value_list(chunk_types)]
            invalid = sorted(set(normalized_types) - allowed_types)
            if invalid:
                raise StorageError(f"Invalid chunk_type in filters: {invalid!r}")
            quoted_types = ", ".join(json.dumps(item) for item in normalized_types)
            clauses.append(f"chunk_type in [{quoted_types}]")

        page_titles = filters.get("page_title")
        if page_titles:
            titles = [str(item) for item in _value_list(page_titles)]
            if any(not isinstance(item, str) for item in _value_list(page_titles)):
                raise StorageError("Invalid page_title in filters")
            quoted_titles = ", ".join(
                json.dumps(title, ensure_ascii=False) for title in titles
            )
            clauses.append(f"page_title in [{quoted_titles}]")

        edited_after = filters.get("edited_after")
        if edited_after:
            if not isinstance(edited_after, str) or not _EDITED_AFTER_RE.fullmatch(edited_after):
                raise StorageError(f"Invalid edited_after in filters: {edited_after!r}")
            clauses.append(f"last_edited_time > {json.dumps(edited_after)}")

        return " and ".join(clauses)

    def _page_filter_expr(self, page_id: str) -> str:
        """Return a quoted Milvus filter for one validated page identifier."""
        if not isinstance(page_id, str) or not _SAFE_FILTER_VALUE_RE.fullmatch(page_id):
            raise StorageError(f"Invalid page_id: {page_id!r}")
        return f'page_id == "{page_id}"'

    def _raw_page_rows(self, page_id: str) -> list[dict[str, Any]]:
        """Snapshot current page rows so they can be restored after a failed insert."""
        try:
            results = self.client.query(
                collection_name=self.collection_name,
                filter=self._page_filter_expr(page_id),
                output_fields=_OUTPUT_FIELDS + ["dense_vector"],
            )
        except Exception as exc:
            raise StorageError(f"Failed to snapshot chunks for {page_id}: {exc}") from exc

        rows: list[dict[str, Any]] = []
        for fields in results:
            row = {key: value for key, value in fields.items() if key not in {"id", "sparse_vector"}}
            rows.append(row)
        return rows

    def _build_rows(
        self,
        chunks: list[Chunk],
        image_docs: list[ImageDoc],
        embeddings: list[list[float]],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for idx, chunk in enumerate(chunks):
            rows.append(self._chunk_to_row(chunk, embeddings[idx]))
        offset = len(chunks)
        for idx, img in enumerate(image_docs):
            rows.append(self._image_to_row(img, embeddings[offset + idx]))
        return rows

    def _chunk_to_row(self, chunk: Chunk, vector: list[float]) -> dict[str, Any]:
        meta = chunk.metadata
        return {
            "chunk_text": chunk.text,
            "dense_vector": vector,
            "page_id": meta.page_id,
            "page_title": meta.page_title,
            "page_url": meta.page_url,
            "header_path": meta.header_path,
            "header_level": meta.header_level,
            "last_edited_time": meta.last_edited_time,
            "chunk_index": meta.chunk_index,
            "chunk_type": meta.chunk_type.value,
            "image_url": meta.image_url,
        }

    def _image_to_row(self, img: ImageDoc, vector: list[float]) -> dict[str, Any]:
        meta = img.metadata
        return {
            "chunk_text": img.context_text,
            "dense_vector": vector,
            "page_id": meta.page_id,
            "page_title": meta.page_title,
            "page_url": meta.page_url,
            "header_path": meta.header_path,
            "header_level": meta.header_level,
            "last_edited_time": meta.last_edited_time,
            "chunk_index": meta.chunk_index,
            "chunk_type": ChunkType.IMAGE.value,
            "image_url": img.image_url,
        }

    def _hits_from_search(self, raw: list[dict[str, Any]]) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for item in raw:
            entity = dict(item.get("entity", {}))
            entity.setdefault("id", item.get("id"))
            score = float(item.get("distance", item.get("score", 0.0)))
            hits.append(self._to_search_hit(entity, score))
        return hits

    def _to_search_hit(self, fields: dict[str, Any], score: float) -> SearchHit:
        return SearchHit(
            id=fields["id"],
            chunk_text=fields["chunk_text"],
            score=score,
            metadata=ChunkMetadata(
                page_id=fields["page_id"],
                page_title=fields["page_title"],
                page_url=fields["page_url"],
                header_path=fields["header_path"],
                header_level=fields["header_level"],
                last_edited_time=fields["last_edited_time"],
                chunk_index=fields["chunk_index"],
                chunk_type=fields["chunk_type"],
                image_url=fields.get("image_url"),
            ),
        )
