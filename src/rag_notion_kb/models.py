from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ChunkType(str, Enum):
    TEXT = "text"
    TABLE = "table"
    CODE = "code"
    IMAGE = "image"


class ChunkMetadata(BaseModel):
    page_id: str
    page_title: str
    page_url: str
    header_path: str
    header_level: int = Field(..., ge=1, le=6)
    last_edited_time: str
    chunk_index: int = Field(..., ge=0)
    chunk_type: ChunkType
    image_url: str | None = None


class Chunk(BaseModel):
    text: str
    metadata: ChunkMetadata
    source_offset: int | None = None


class ImageDoc(BaseModel):
    image_url: str
    local_path: str | None = None
    remote_url: str | None = None
    alt: str
    context_text: str
    metadata: ChunkMetadata
    source_offset: int | None = None


class EmbeddingItem(BaseModel):
    type: Literal["text", "image_url"]
    text: str | None = None
    image_url: str | None = None


class RankCandidate(BaseModel):
    text: str | None = None
    image_url: str | None = None


class SearchHit(BaseModel):
    id: int
    chunk_text: str
    score: float
    metadata: ChunkMetadata


class SearchResult(BaseModel):
    text: str
    score: float
    source: ChunkMetadata
    matched_snippet: str | None = None


class PageSyncState(BaseModel):
    page_id: str
    page_title: str
    page_url: str
    last_edited_time: str
    parent_id: str | None = None
    chunk_count: int
    image_count: int
    status: Literal["fetched", "synced", "failed", "skipped"]
    error_message: str | None = None
    last_synced_time: str
    raw_markdown: str = ""
    # Vectorization toggle & state
    vector_enabled: bool = False
    vector_status: Literal["pending", "indexing", "indexed", "failed"] = "pending"
    vector_error_message: str | None = None
    vector_progress: int = Field(0, ge=0, le=100)
    vector_stage: str = ""
    last_vectorized_time: str | None = None
    # Content & chunking hashes for skip-if-unchanged optimization
    content_hash: str = ""
    chunking_hash: str = ""
    embedding_hash: str = ""


class PageMetadata(BaseModel):
    page_id: str
    title: str
    url: str
    last_edited_time: str
    parent_id: str | None = None
    is_container: bool = False


class SyncResult(BaseModel):
    added: int = 0
    updated: int = 0
    removed: int = 0
    skipped: int = 0
    failed: int = 0
    degraded_pages: int = 0
    zero_vector_chunks: int = 0
    degraded_page_ids: list[str] = []


class VectorizeResult(BaseModel):
    """Result of a vectorize operation (one or multiple pages)."""
    total: int = 0
    indexed: int = 0
    failed: int = 0
    skipped: int = 0
    page_results: list[dict[str, str]] = Field(default_factory=list)


# --- Web UI API models ---


class EmbeddingStats(BaseModel):
    """Embedding statistics for a single page."""
    total: int
    nonzero: int
    zero: int
    dim: int
    sample_first5: list[float] = Field(default_factory=list)


class PageSummary(BaseModel):
    """Summary row for the pages list API. Includes vectorization state."""
    page_id: str
    page_title: str
    page_url: str
    chunk_count: int
    image_count: int
    status: str
    zero_vector_chunks: int
    last_edited_time: str
    last_synced_time: str
    vector_enabled: bool = False
    vector_status: str = "pending"
    vector_progress: int = 0
    vector_stage: str = ""
    last_vectorized_time: str | None = None
    doc_size_kb: float = 0
    root_id: str | None = None


class ChunkDetail(BaseModel):
    """Individual chunk detail for the preview page API."""
    chunk_index: int
    chunk_type: str
    header_path: str
    header_level: int
    text: str
    vector_nonzero: bool
    image_url: str | None = None


class PageDetail(BaseModel):
    """Full page detail for the preview page API."""
    page_id: str
    page_title: str
    page_url: str
    status: str
    last_synced_time: str
    raw_markdown: str
    chunks: list[ChunkDetail]
    embedding_stats: EmbeddingStats


# --- Root Management & Sync Progress models ---


class NotionRoot(BaseModel):
    """A configured Notion root page for sync."""
    root_id: str  # PK, same as page_id
    page_title: str
    page_url: str
    added_time: str  # ISO 8601
    is_active: bool = True


class SyncTaskStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SyncTask(BaseModel):
    """Progress tracking model for a batch sync operation."""
    sync_id: str
    status: SyncTaskStatus
    total_pages: int
    completed_pages: int
    current_page_id: str | None = None
    current_page_title: str | None = None
    results: SyncResult | None = None
    error_message: str | None = None
    started_at: str
    finished_at: str | None = None


class TreePageNode(BaseModel):
    """A page node in the page tree view, with recursive children."""
    page_id: str
    title: str
    url: str
    last_edited_time: str
    children: list[TreePageNode] = []


# --- Search Debug & History models ---


class ContextExpandMode(str, Enum):
    NONE = "none"
    PARENT = "parent"
    H2 = "h2"


class DebugSearchRequest(BaseModel):
    """Fully tunable search request used by the retrieval debug UI."""

    query: str
    dense_weight: float = Field(0.5, ge=0.0, le=1.0)
    sparse_weight: float = Field(0.5, ge=0.0, le=1.0)
    top_k: int = Field(5, ge=1, le=100)
    min_similarity: float = Field(0.4, ge=0.0, le=1.0)
    rerank_model: str = "qwen3-vl-rerank"
    filters: dict[str, list[str]] = Field(default_factory=dict)
    context_mode: ContextExpandMode = ContextExpandMode.H2
    max_tokens: int = Field(4000, ge=100, le=8000)


class StageScores(BaseModel):
    """Scores at each stage of the retrieval pipeline for a single hit."""

    dense_score: float | None = None
    sparse_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
    final_score: float


class DebugSearchHit(BaseModel):
    """A single search result with full debugging metadata."""

    rank: int
    page_id: str
    page_title: str
    page_url: str
    chunk_index: int
    chunk_type: str
    header_path: str
    header_level: int
    chunk_text: str
    expanded_text: str
    scores: StageScores
    matched_snippet: str | None = None
    image_url: str | None = None


class DebugSearchResponse(BaseModel):
    """Full response for a debug search query."""

    query: str
    params: DebugSearchRequest
    total_dense: int
    total_sparse: int
    total_after_filter: int
    total_after_fusion: int
    total_after_rerank: int
    results: list[DebugSearchHit]
    latency_ms: int
    error: str | None = None


class SearchSource(str, Enum):
    DEBUG = "debug"
    MCP = "mcp"
    CLI = "cli"


class SearchHistory(BaseModel):
    """A persisted search record with an optional full result snapshot."""

    history_id: str
    query: str
    source: SearchSource
    params: dict[str, Any]
    result_summary: dict[str, Any]
    snapshot: dict[str, Any] | None = None
    created_at: str
