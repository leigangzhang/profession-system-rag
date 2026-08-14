# RAG Notion 知识库系统 — 后端实现说明

> **版本**：v2.0.0  
> **日期**：2026-08-14  
> **原则**：本文件由当前代码实现驱动；代码是唯一 Source of Truth。

## 1. 范围与现状

后端以 Python 包 `rag_notion_kb` 实现，负责：

- 递归读取 Notion 页面，导出元数据与 Markdown，并缓存页面图片。
- 将同步流程拆为 `sync_fetch`（拉取到 SQLite）与 `sync_vectorize`（分块、向量化、写入 Milvus）。
- 以 SQLite 保存同步状态、Root 配置、页面树缓存、同步任务与检索历史。
- 使用 Milvus Lite 提供 dense + BM25 sparse 混合检索。
- 通过 FastAPI 提供 Web UI API，通过 Typer 提供 CLI，通过 MCP stdio 提供 `rag_search`、`rag_sync`、`rag_stats`、`rag_page_detail`。

## 2. 包结构

```text
src/rag_notion_kb/
├── app_context.py              # 依赖装配与生命周期
├── cli.py                      # Typer CLI
├── config.py                   # Pydantic Settings
├── exceptions.py               # 异常与重试装饰器
├── models.py                   # 共享 Pydantic 模型
├── mcp_server.py               # FastMCP stdio server
├── embedding/
│   ├── qwen_vl.py              # Qwen3-VL Embedding
│   └── reranker.py             # Qwen3-VL Rerank
├── notion/client.py            # Notion API 封装
├── processing/
│   ├── chunking.py             # Markdown 分块
│   └── images.py               # 图片提取
├── retrieval/
│   ├── context_expand.py       # 上下文扩展
│   ├── hybrid_search.py        # 加权融合
│   └── truncate.py             # Token 截断
├── services/
│   ├── search_service.py       # 检索编排
│   ├── sync_progress.py        # 同步任务状态
│   ├── sync_service.py         # 两阶段同步编排
│   └── vectorize_worker.py     # 后台向量化 Worker
├── storage/
│   ├── milvus_store.py         # Milvus 读写
│   ├── root_store.py           # Root 配置
│   ├── schema.py               # Milvus Schema
│   ├── search_history_store.py # 检索历史
│   ├── sync_state.py           # 同步状态
│   └── tree_cache.py           # 页面树缓存
└── web/
    ├── web_server.py           # FastAPI app factory
    └── static/                 # Vue 3 SPA 静态文件
```

## 3. 共享数据模型

`src/rag_notion_kb/models.py` 当前共 23 个 Pydantic 模型。下面按实现顺序列出字段；没有显式默认值的字段为必填。

### 3.1 数据管道模型

```python
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
```

`ChunkType` 的值为 `text`、`table`、`code`、`image`。

### 3.2 检索模型

```python
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
```

### 3.3 同步状态模型

```python
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
    vector_enabled: bool = False
    vector_status: Literal["pending", "indexing", "indexed", "failed"] = "pending"
    vector_error_message: str | None = None
    vector_progress: int = Field(0, ge=0, le=100)
    vector_stage: str = ""
    last_vectorized_time: str | None = None
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
```

`SyncResult` 的字段为 `added`、`updated`、`removed`、`skipped`、`failed`、`degraded_pages`、`zero_vector_chunks`、`degraded_page_ids`，除 `degraded_page_ids` 为 `list[str]` 外均为整数。`VectorizeResult` 的字段为 `total`、`indexed`、`failed`、`skipped`、`page_results`。

### 3.4 Web UI 模型

```python
class EmbeddingStats(BaseModel):
    total: int
    nonzero: int
    zero: int
    dim: int
    sample_first5: list[float] = Field(default_factory=list)

class PageSummary(BaseModel):
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
    chunk_index: int
    chunk_type: str
    header_path: str
    header_level: int
    text: str
    vector_nonzero: bool
    image_url: str | None = None

class PageDetail(BaseModel):
    page_id: str
    page_title: str
    page_url: str
    status: str
    last_synced_time: str
    raw_markdown: str
    chunks: list[ChunkDetail]
    embedding_stats: EmbeddingStats
```

### 3.5 Root、同步任务与页面树

```python
class NotionRoot(BaseModel):
    root_id: str
    page_title: str
    page_url: str
    added_time: str
    is_active: bool = True

class SyncTask(BaseModel):
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
    page_id: str
    title: str
    url: str
    last_edited_time: str
    children: list[TreePageNode] = []
```

### 3.6 检索调试与历史

```python
class ContextExpandMode(str, Enum):
    NONE = "none"
    PARENT = "parent"
    H2 = "h2"

class DebugSearchRequest(BaseModel):
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
    dense_score: float | None = None
    sparse_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
    final_score: float

class DebugSearchHit(BaseModel):
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
    history_id: str
    query: str
    source: SearchSource
    params: dict[str, Any]
    result_summary: dict[str, Any]
    snapshot: dict[str, Any] | None = None
    created_at: str
```

## 4. 配置管理

`Settings` 使用 `env_nested_delimiter="__"` 与 `env_prefix="RAG_KB_"`，来源优先级为：

1. 显式构造参数。
2. 环境变量。
3. `.env`。
4. `~/.rag_kb/config.yaml`。
5. `file_secret_settings`。

当前配置组：

| 配置组 | 字段 |
|--------|------|
| `NotionConfig` | `token`, `root_page_ids` |
| `EmbeddingConfig` | `api_key`, `base_url`, `model`, `dimensions`, `batch_size`, `max_retries` |
| `RerankerConfig` | `api_key`, `base_url`, `model`, `max_retries` |
| `StorageConfig` | `data_dir` |
| `ChunkingConfig` | `header_levels`, `max_chunk_size`, `min_chunk_size`, `preserve_tables`, `preserve_code_blocks`, `image_context_window`, `image_context_max_chars` |
| `VectorizeConfig` | `max_concurrent`, `poll_interval_seconds` |
| `RetrievalConfig` | `default_top_k`, `default_expand_to_level`, `default_max_tokens`, `dense_limit`, `sparse_limit`, `rrf_k` |
| `LoggingConfig` | `level`, `format` |
| `Settings` | `notion`, `embedding`, `reranker`, `storage`, `chunking`, `retrieval`, `vectorize`, `logging` |

主要默认值：

- `storage.data_dir`: `~/.rag_kb`。
- `embedding.model`: `qwen3-vl-embedding`，`dimensions`: `2048`。
- `reranker.model`: `qwen3-vl-rerank`。
- `vectorize.max_concurrent`: `3`，`poll_interval_seconds`: `2`。
- `retrieval.default_top_k`: `10`，`default_max_tokens`: `4000`。
- `logging.format`: `json`。

## 5. 异常体系

异常类位于 `src/rag_notion_kb/exceptions.py`：

- `RagKbError`
- `ConfigError`
- `NotionApiError`
- `EmbeddingError`
- `EmbeddingServiceError`
- `StorageError`
- `RetrievalError`

`with_notion_retry` 使用 tenacity 对幂等 Notion 读请求最多重试 3 次，指数退避初始 1 秒、最大 8 秒，并仅在 `NotionApiError` 时重试。

## 6. 存储层

### 6.1 SQLite 表

运行时数据库统一为 `~/.rag_kb/sync_state.db`。除 `sync_state.py` 的迁移逻辑外，其他表均由 `CREATE TABLE IF NOT EXISTS` 创建。

#### `page_sync_state`

字段：`page_id`、`page_title`、`page_url`、`last_edited_time`、`parent_id`、`chunk_count`、`image_count`、`status`、`error_message`、`last_synced_time`、`raw_markdown`、`vector_enabled`、`vector_status`、`vector_error_message`、`vector_progress`、`vector_stage`、`last_vectorized_time`、`content_hash`、`chunking_hash`、`embedding_hash`。

`status` 仅允许 `fetched`、`synced`、`failed`、`skipped`；`vector_status` 仅允许 `pending`、`indexing`、`indexed`、`failed`。旧库缺少新增列时由 `SyncStateStore._migrate_schema()` 使用 `ALTER TABLE` 补齐。

#### `notion_roots`

字段：`root_id`、`page_title`、`page_url`、`added_time`、`is_active`。

#### `page_tree_cache`

字段：`root_id`、`page_id`、`title`、`url`、`last_edited_time`、`parent_id`、`cached_at`，主键为 `(root_id, page_id)`。`NotionTreeCache` 将扁平页面列表重建为树。

#### `sync_progress`

字段：`sync_id`、`status`、`total_pages`、`completed_pages`、`current_page_id`、`current_page_title`、`results_json`、`error_message`、`started_at`、`finished_at`。`SyncProgressTracker` 同时使用内存热路径并写穿到该表，默认任务 TTL 为 300 秒。

#### `search_history`

字段：`history_id`、`query`、`source`、`params`、`result_summary`、`snapshot`、`created_at`。`params`、`result_summary`、`snapshot` 为 JSON 文本。保留策略为最多 500 条、最多 30 天，历史写入为单线程后台异步队列。

### 6.2 Milvus Schema

`storage/schema.py` 定义 collection `rag_kb_chunks`，`DENSE_DIM = 2048`，包含 13 个字段：

| Field | Type | 说明 |
|-------|------|------|
| `id` | `INT64` | 主键，自动生成 |
| `chunk_text` | `VARCHAR(65535)` | 开启 analyzer 与 match |
| `dense_vector` | `FLOAT_VECTOR(2048)` | Dense ANN 字段 |
| `sparse_vector` | `SPARSE_FLOAT_VECTOR` | BM25 输出 |
| `page_id` | `VARCHAR(64)` | |
| `page_title` | `VARCHAR(512)` | |
| `page_url` | `VARCHAR(2048)` | |
| `header_path` | `VARCHAR(2048)` | |
| `header_level` | `INT8` | |
| `last_edited_time` | `VARCHAR(32)` | |
| `chunk_index` | `INT32` | |
| `chunk_type` | `VARCHAR(16)` | |
| `image_url` | `VARCHAR(65535)` | nullable |

`chunk_text_bm25_emb` 是 BM25 函数，输入 `chunk_text`、输出 `sparse_vector`。`MilvusStore` 的 Dense 使用 COSINE，Sparse 使用 BM25；页面替换采用“删除旧行、插入新行，失败时恢复旧行”的最佳努力回滚。

## 7. Notion 与处理层

`NotionClient`：

- `get_page_metadata()`：读取单页轻量元数据。
- `get_page_metadata_with_container()`：同时判断页面是否只包含 `child_page` block。
- `get_page_markdown()`：导出 Markdown，调用方可复用已有 metadata。
- `enumerate_pages()`：DFS 递归，带 visited 去重、`max_depth=10` 与失败 ID 收集。
- `list_child_pages()`：读取直接子页面。

`MarkdownProcessor` 按 `header_levels`、`max_chunk_size`、`min_chunk_size` 分块，保持表格和代码块完整。`ImageExtractor` 从 Markdown 提取图片并生成上下文，配合本地图片缓存重写 URL。

## 8. 服务层

### 8.1 SyncService

```python
def sync(
    root_page_ids: list[str] | None = None,
    force_full: bool = False,
    vectorize: bool = False,
) -> tuple[SyncResult, VectorizeResult | None]: ...

def sync_fetch(
    root_page_ids: list[str] | None = None,
    force_full: bool = False,
    page_ids: list[str] | None = None,
    progress_callback: Callable[[str, str, int, int], None] | None = None,
) -> SyncResult: ...

def sync_vectorize(page_ids: list[str] | None = None) -> VectorizeResult: ...
```

行为要点：

- `sync_fetch` 只写 SQLite，不执行分块、Embedding 或 Milvus 写入。
- `last_edited_time` 未变化时跳过；全 Root 同步会删除远端已不存在的本地页面。
- 内容变化且页面已开启向量时，`vector_status` 回到 `pending`，开关状态保留。
- `sync_vectorize` 默认处理 `vector_enabled=True AND vector_status='pending'` 的页面。
- `_vectorize_page` 使用 `content_hash`、`chunking_hash`、`embedding_hash` 进行“未变化即跳过”判断。
- 单个页面失败不会阻断其他页面；Embedding 服务整体不可用时停止后续处理并标记 skipped。

### 8.2 VectorizeWorker

`VectorizeWorker` 启动时先调用 `reset_stale_vectorize()`，然后周期轮询 pending 页面。它使用 `asyncio.Semaphore` 限制并发、`_active_page_ids` 防重，并把重活通过 `asyncio.to_thread()` 交给 `sync_service.vectorize_state()`。`enqueue()` 可手动触发或开启向量。

### 8.3 SearchService

`search()` 是生产检索入口：Embed query → dense/sparse 检索 → `weighted_fusion` → ReRank → context expand → token truncate。默认参数来自 `RetrievalConfig`，`history_source` 非空时异步写入历史。

`debug_search()` 返回 `DebugSearchResponse`，暴露 dense、sparse、fusion（RRF 字段）、rerank、final 分数和每阶段数量。ReRank 失败时回退 fusion 顺序，检索失败以 `error` 字段返回而非抛出到 Web API。

### 8.4 历史与进度

`SearchHistoryStore` 提供 `add`、`add_async`、`list_recent`、`stats`、`get`、`delete`、`clear_all`。`SyncProgressTracker` 提供 `create_task`、`update`、`complete`、`fail`、`get` 和 callback 工厂。

## 9. Web API

FastAPI app 由 `create_app()` 创建，支持传入已装配的 `AppContext` 或独立依赖。

| Method | Path | Handler | 说明 |
|--------|------|---------|------|
| GET | `/health` | `health` | 健康检查与 Worker 运行状态 |
| GET | `/api/pages` | `list_pages` | 已同步页面摘要，含向量状态 |
| GET | `/api/pages/{page_id}` | `page_detail` | 单页 Markdown、chunks 与 embedding stats |
| POST | `/api/pages/{page_id}/sync` | `sync_single_page` | 重拉单个页面 |
| POST | `/api/pages/{page_id}/vector-toggle` | `toggle_vector` | 开启/关闭向量开关 |
| POST | `/api/pages/{page_id}/vectorize` | `trigger_vectorize` | 手动入队向量化 |
| GET | `/api/worker/status` | `worker_status` | Worker 状态与并发上限 |
| GET | `/api/roots` | `list_roots` | Root 列表 |
| POST | `/api/roots` | `add_root` | 添加并验证 Root，后台填充树缓存 |
| DELETE | `/api/roots/{root_id}` | `delete_root` | 删除 Root、树缓存、页面状态与 Milvus 数据 |
| GET | `/api/roots/{root_id}/tree` | `get_page_tree` | 返回缓存页面树与同步状态 |
| POST | `/api/roots/{root_id}/refresh` | `refresh_root_tree` | 重拉并更新页面树缓存 |
| POST | `/api/sync` | `batch_sync` | 后台批量 `sync_fetch`，返回 `sync_id` |
| GET | `/api/sync/{sync_id}/progress` | `sync_progress` | 查询批量同步进度 |
| POST | `/api/search/debug` | `debug_search` | 调参检索，返回各阶段分数 |
| GET | `/api/search/history` | `list_search_history` | 最近历史，不返回 snapshot |
| GET | `/api/search/history/stats` | `search_history_stats` | 历史统计与检索健康指标 |
| DELETE | `/api/search/history` | `clear_search_history` | `clear=all` 时清空历史 |
| GET | `/api/search/history/{history_id}` | `get_search_history` | 单条历史，含回放参数 |
| POST | `/api/search/history/{history_id}/replay` | `replay_search_history` | 返回保存的 snapshot |
| DELETE | `/api/search/history/{history_id}` | `delete_search_history` | 删除单条历史 |
| GET | `/` | `index` | SPA 入口 |
| GET | `/{full_path:path}` | `spa_fallback` | 非保留路径回退到 SPA |

此外挂载 `/static` 与 `/images`。`/images` 服务于 `~/.rag_kb/images` 本地缓存。

## 10. MCP Tools

| Tool | 说明 | 只读 |
|------|------|------|
| `rag_search` | 混合检索，支持过滤、上下文扩展、ReRank 与高亮；标记 open-world | 是 |
| `rag_sync` | 增量或全量同步 | 否 |
| `rag_stats` | 知识库统计 | 是 |
| `rag_page_detail` | 返回已同步页面的完整拼接正文 | 是 |

`rag_search` 的过滤模型 `RagSearchFilters` 支持 `page_ids`、`header_level`、`chunk_type`、`page_title`、`edited_after`。

## 11. CLI

| 命令 | 作用 |
|------|------|
| `rag-kb sync` | 拉取同步；`--root`、`--full`、`--vectorize` |
| `rag-kb status` | 知识库统计 |
| `rag-kb search` | 命令行检索并记录历史 |
| `rag-kb inspect` | 查看单页 chunk 与 embedding 状态 |
| `rag-kb web` | 启动 FastAPI Web UI，默认 `127.0.0.1:58000` |
| `rag-kb serve` | 启动 MCP stdio server |
| `rag-kb config` | 展示脱敏配置 |

## 12. 日志与测试

日志默认 JSON，由 `logging_setup.setup_logging()` 初始化。测试分为 `tests/unit`、`tests/integration`、`tests/e2e`；外网 API 与真实 Milvus 相关测试使用 pytest marker。

## 13. 修订历史

| 版本 | 日期 | 变更内容 |
|------|------|----------|
| v2.0.0 | 2026-08-14 | 与当前代码全面对齐：两阶段同步、Root 管理、页面树缓存、检索调试、检索历史、Vue SPA |
