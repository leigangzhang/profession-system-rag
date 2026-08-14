# Consistency Report

> Generated: 2026-08-14 12:07:34 UTC
> Data Dir: `/Users/ray/.rag_kb`

## Summary

| Dimension | Aligned | Mismatched | Missing in Docs | Missing in Data |
|-----------|--------:|-----------:|----------------:|----------------:|
| Data Models | 23 | 0 | 0 | 0 |
| Config Groups | 9 | 0 | 0 | 0 |
| Exceptions | 7 | 0 | 0 | 0 |
| SQLite Schema | 5 | 0 | 0 | 0 |
| API Routes | 23 | 0 | 0 | 0 |
| CLI Commands | 7 | 0 | 0 | 0 |
| MCP Tools | 4 | 0 | 0 | 0 |
| Milvus Fields | 13 | 0 | 0 | 0 |
| Frontend Files | 17 | 0 | 0 | 0 |

## Action Items

None. Code, documentation, and checked data schema are aligned.


## Inventory

### Pydantic Models (23)

| Model | Fields | Fields |
|-------|-------:|--------|
| ChunkMetadata | 9 | page_id, page_title, page_url, header_path, header_level, last_edited_time, chunk_index, chunk_type, image_url |
| Chunk | 3 | text, metadata, source_offset |
| ImageDoc | 7 | image_url, local_path, remote_url, alt, context_text, metadata, source_offset |
| EmbeddingItem | 3 | type, text, image_url |
| RankCandidate | 2 | text, image_url |
| SearchHit | 4 | id, chunk_text, score, metadata |
| SearchResult | 4 | text, score, source, matched_snippet |
| PageSyncState | 20 | page_id, page_title, page_url, last_edited_time, parent_id, chunk_count, image_count, status, error_message, last_synced_time, raw_markdown, vector_enabled, vector_status, vector_error_message, vector_progress, vector_stage, last_vectorized_time, content_hash, chunking_hash, embedding_hash |
| PageMetadata | 6 | page_id, title, url, last_edited_time, parent_id, is_container |
| SyncResult | 8 | added, updated, removed, skipped, failed, degraded_pages, zero_vector_chunks, degraded_page_ids |
| VectorizeResult | 5 | total, indexed, failed, skipped, page_results |
| EmbeddingStats | 5 | total, nonzero, zero, dim, sample_first5 |
| PageSummary | 16 | page_id, page_title, page_url, chunk_count, image_count, status, zero_vector_chunks, last_edited_time, last_synced_time, vector_enabled, vector_status, vector_progress, vector_stage, last_vectorized_time, doc_size_kb, root_id |
| ChunkDetail | 7 | chunk_index, chunk_type, header_path, header_level, text, vector_nonzero, image_url |
| PageDetail | 8 | page_id, page_title, page_url, status, last_synced_time, raw_markdown, chunks, embedding_stats |
| NotionRoot | 5 | root_id, page_title, page_url, added_time, is_active |
| SyncTask | 10 | sync_id, status, total_pages, completed_pages, current_page_id, current_page_title, results, error_message, started_at, finished_at |
| TreePageNode | 5 | page_id, title, url, last_edited_time, children |
| DebugSearchRequest | 9 | query, dense_weight, sparse_weight, top_k, min_similarity, rerank_model, filters, context_mode, max_tokens |
| StageScores | 5 | dense_score, sparse_score, rrf_score, rerank_score, final_score |
| DebugSearchHit | 13 | rank, page_id, page_title, page_url, chunk_index, chunk_type, header_path, header_level, chunk_text, expanded_text, scores, matched_snippet, image_url |
| DebugSearchResponse | 10 | query, params, total_dense, total_sparse, total_after_filter, total_after_fusion, total_after_rerank, results, latency_ms, error |
| SearchHistory | 7 | history_id, query, source, params, result_summary, snapshot, created_at |

### Config Groups (9)

| Group | Fields | Fields |
|-------|-------:|--------|
| NotionConfig | 2 | token, root_page_ids |
| EmbeddingConfig | 6 | api_key, base_url, model, dimensions, batch_size, max_retries |
| RerankerConfig | 4 | api_key, base_url, model, max_retries |
| StorageConfig | 1 | data_dir |
| ChunkingConfig | 7 | header_levels, max_chunk_size, min_chunk_size, preserve_tables, preserve_code_blocks, image_context_window, image_context_max_chars |
| VectorizeConfig | 2 | max_concurrent, poll_interval_seconds |
| RetrievalConfig | 6 | default_top_k, default_expand_to_level, default_max_tokens, dense_limit, sparse_limit, rrf_k |
| LoggingConfig | 2 | level, format |
| Settings | 8 | notion, embedding, reranker, storage, chunking, retrieval, vectorize, logging |

### Exceptions (7)

- RagKbError, ConfigError, NotionApiError, EmbeddingError, EmbeddingServiceError, StorageError, RetrievalError

### API Routes (23)

| Method | Path | Handler | Line | Docs |
|--------|------|---------|-----:|------|
| GET | `/health` | health | 117 | ✅ |
| GET | `/api/pages` | list_pages | 147 | ✅ |
| GET | `/api/pages/{page_id}` | page_detail | 189 | ✅ |
| POST | `/api/pages/{page_id}/sync` | sync_single_page | 244 | ✅ |
| POST | `/api/pages/{page_id}/vector-toggle` | toggle_vector | 254 | ✅ |
| POST | `/api/pages/{page_id}/vectorize` | trigger_vectorize | 281 | ✅ |
| GET | `/api/worker/status` | worker_status | 290 | ✅ |
| GET | `/api/roots` | list_roots | 303 | ✅ |
| POST | `/api/roots` | add_root | 310 | ✅ |
| DELETE | `/api/roots/{root_id}` | delete_root | 356 | ✅ |
| GET | `/api/roots/{root_id}/tree` | get_page_tree | 383 | ✅ |
| POST | `/api/roots/{root_id}/refresh` | refresh_root_tree | 424 | ✅ |
| POST | `/api/sync` | batch_sync | 454 | ✅ |
| GET | `/api/sync/{sync_id}/progress` | sync_progress | 486 | ✅ |
| POST | `/api/search/debug` | debug_search | 501 | ✅ |
| GET | `/api/search/history` | list_search_history | 508 | ✅ |
| GET | `/api/search/history/stats` | search_history_stats | 521 | ✅ |
| DELETE | `/api/search/history` | clear_search_history | 528 | ✅ |
| GET | `/api/search/history/{history_id}` | get_search_history | 537 | ✅ |
| POST | `/api/search/history/{history_id}/replay` | replay_search_history | 547 | ✅ |
| DELETE | `/api/search/history/{history_id}` | delete_search_history | 559 | ✅ |
| GET | `/` | index | 581 | ✅ |
| GET | `/{full_path:path}` | spa_fallback | 585 | ✅ |

### CLI Commands (7)

- sync, status, search, inspect, web, serve, config

### MCP Tools (4)

- rag_search, rag_sync, rag_stats, rag_page_detail

### Milvus Fields (13)

| Field | Type | Limit/Dim |
|-------|------|----------:|
| id | INT64 | 0 |
| chunk_text | VARCHAR | 65535 |
| dense_vector | FLOAT_VECTOR | 2048 |
| sparse_vector | SPARSE_FLOAT_VECTOR | 0 |
| page_id | VARCHAR | 64 |
| page_title | VARCHAR | 512 |
| page_url | VARCHAR | 2048 |
| header_path | VARCHAR | 2048 |
| header_level | INT8 | 0 |
| last_edited_time | VARCHAR | 32 |
| chunk_index | INT32 | 0 |
| chunk_type | VARCHAR | 16 |
| image_url | VARCHAR | 65535 |

### Frontend Static Files

| Kind | Files |
|------|-------|
| JS | api.js, app.js, highlight.min.js, marked.min.js, utils.js |
| Components | AppLayout.vue, EmptyState.vue, PageHeader.vue, PageTree.vue, ScoreBar.vue, StatusTag.vue, SyncProgress.vue |
| Views | ChunkInspector.vue, PageList.vue, RootManager.vue, SearchDebug.vue, SearchHistory.vue |

### Frontend API Client Methods (20)

- listRoots, addRoot, deleteRoot, getRootTree, refreshRootTree, listPages, getPageDetail, syncPage, toggleVector, triggerVectorize, syncPages, getSyncProgress, debugSearch, listHistory, getHistoryStats, getHistory, replayHistory, deleteHistory, clearHistory, getWorkerStatus

### Docs Organization

- Active root documents: `Architecture.md, Backend.md, Frontend.md, Manual.md, README.md, Spec.md, Stand.md`
- Active count within limit: ✅
- Archived directory: ✅
- Dev directory: ✅
