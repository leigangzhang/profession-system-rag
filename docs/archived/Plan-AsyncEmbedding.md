# RAG Notion 知识库系统 - 向量开关 + 后台异步向量化 实施计划

## Context

### 变更原因
用户要求从"命令触发全量同步"（拉取→分块→Embedding→存储一次性完成）转变为"先拉取元数据、手动控制向量化、后台异步执行"的模式：

1. **Notion 递归拉取页面后，默认不做分块**
2. **先将页面元数据 + 原始 Markdown 写入 SQLite**
3. **Web UI 列表页展示所有页面，用户手动控制每个页面的向量开关**
4. **打开开关后，后台异步执行分块→Embedding→向量存储**
5. **执行完成后 Web UI 自动同步状态（HTTP 轮询）**
6. **后台支持多个 Notion 页面并发执行向量化**
7. **向量化流程稳定可靠，异常可追踪（日志）**

### 用户已确认的决策
- **并发控制**：`asyncio.Semaphore` 限制并发数，默认 2~3，可配置
- **状态同步**：Web UI 每 2~3 秒 HTTP 轮询 `/api/pages`
- **增量同步行为**：Notion 页面更新后保留开关状态，标记为 `pending`，用户手动触发重新向量化

---

## 核心设计决策

| # | 决策 | 说明 |
|---|---|---|
| 1 | 同步拆分为两阶段 | `sync_fetch`（拉取元数据+Markdown→SQLite）和 `sync_vectorize`（分块→Embedding→Milvus）。前者轻量快速，后者重且耗时。 |
| 2 | 向量开关持久化 | `vector_enabled: bool` 持久化在 SQLite `page_sync_state` 表中，开关状态与页面生命周期绑定。 |
| 3 | 向量状态机 | `pending → indexing → indexed/failed`。页面更新后从 `indexed` 回退到 `pending`（保留开关状态）。 |
| 4 | 后台 Worker | `VectorizeWorker` 管理异步任务队列，每个页面一个独立任务，Semaphore 控制并发。 |
| 5 | 失败隔离 | 单个页面向量化失败（API/存储异常）不影响其他页面，错误写入 `vector_error_message`。 |
| 6 | CLI 向后兼容 | `rag-kb sync` 默认只拉取；新增 `--vectorize` 参数可一次性拉取+向量化所有页面。 |

---

## 数据模型变更

### `PageSyncState`（`models.py`）新增字段

```python
class PageSyncState(BaseModel):
    # 已有字段保持不变
    page_id: str
    page_title: str
    page_url: str
    last_edited_time: str
    parent_id: str | None = None
    chunk_count: int
    image_count: int
    status: Literal["fetched", "synced", "failed", "skipped"]  # "synced" 拆分为 fetched + vector_status
    error_message: str | None = None
    last_synced_time: str
    raw_markdown: str = ""  # v1.2 已新增

    # === 新增：向量开关与状态 ===
    vector_enabled: bool = False
    vector_status: Literal["pending", "indexing", "indexed", "failed"] = "pending"
    vector_error_message: str | None = None
    vector_progress: int = Field(0, ge=0, le=100)  # 0~100
```

### SQLite DDL 变更（`storage/sync_state.py`）

```sql
ALTER TABLE page_sync_state ADD COLUMN vector_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE page_sync_state ADD COLUMN vector_status TEXT NOT NULL DEFAULT 'pending' CHECK(vector_status IN ('pending','indexing','indexed','failed'));
ALTER TABLE page_sync_state ADD COLUMN vector_error_message TEXT;
ALTER TABLE page_sync_state ADD COLUMN vector_progress INTEGER NOT NULL DEFAULT 0;
```

> 首次安装直接创建带新列的表；已有数据库通过 Schema 迁移逻辑处理（检测列是否存在）。

---

## 服务拆分设计

### 原 `SyncService.sync()` 拆分为

```python
class SyncService:
    # ... 依赖注入保持不变 ...

    async def sync_fetch(
        self,
        root_page_ids: list[str] | None = None,
        force_full: bool = False,
    ) -> SyncResult:
        """阶段1：只拉取 Notion 页面元数据和原始 Markdown，写入 SQLite。

        不执行分块、Embedding、Milvus 写入。
        已有数据且未修改的页面跳过。
        页面更新后：vector_status 回退到 'pending'（保留 vector_enabled）。
        """
        ...

    async def sync_vectorize(
        self,
        page_ids: list[str] | None = None,
    ) -> VectorizeResult:
        """阶段2：对指定页面执行分块→Embedding→Milvus 写入。

        若 page_ids 为 None，则处理所有 vector_enabled=True 且 vector_status='pending' 的页面。
        支持被 VectorizeWorker 调用。
        """
        ...

    async def sync(
        self,
        root_page_ids: list[str] | None = None,
        force_full: bool = False,
        vectorize: bool = False,  # CLI --vectorize 参数
    ) -> tuple[SyncResult, VectorizeResult | None]:
        """组合调用：先 fetch，若 vectorize=True 再 vectorize 所有已启用页面。"""
        ...
```

### 新增 `VectorizeWorker`（`services/vectorize_worker.py`）

```python
class VectorizeWorker:
    def __init__(
        self,
        sync_service: SyncService,
        state_store: SyncStateStore,
        max_concurrent: int = 3,
    ): ...

    async def start(self) -> None:
        """启动后台 Worker，持续监听待向量化页面队列。"""
        ...

    async def stop(self) -> None:
        """优雅停止：等待正在执行的任务完成，不再接受新任务。"""
        ...

    async def enqueue(self, page_id: str) -> None:
        """将指定页面加入向量化队列。"""
        ...

    async def _run_page_vectorize(self, page_id: str) -> None:
        """执行单个页面的向量化流程，含完整异常捕获和状态更新。"""
        ...
```

**Worker 内部逻辑**：
1. `start()` 启动一个 `asyncio.Task` 作为守护任务
2. 守护任务循环：查询 SQLite 中 `vector_enabled=1 AND vector_status='pending'` 的页面
3. 使用 `asyncio.Semaphore(self.max_concurrent)` 控制并发
4. 每个页面启动一个 `_run_page_vectorize()` 协程
5. 协程内部：
   - 更新 `vector_status='indexing'`, `vector_progress=0`
   - 读取 `raw_markdown` → `processor.split()` → `image_extractor.extract()`
   - `vector_progress=30`
   - `embedding.embed()` → `vector_progress=70`
   - `store.delete_by_page_id()` → `store.upsert_page()` → `vector_progress=100`
   - 更新 `vector_status='indexed'`
6. 任何异常：捕获 → 记录结构化日志 → 更新 `vector_status='failed'`, `vector_error_message=str(e)`

---

## Web UI 变更（`web/`）

### 新增/修改 API 路由

```python
# web/web_server.py

@app.get("/api/pages")
async def list_pages() -> list[PageSummary]:
    """返回所有页面，含向量开关和状态。"""

@app.post("/api/pages/{page_id}/vector-toggle")
async def toggle_vector(page_id: str, enabled: bool) -> PageSummary:
    """切换指定页面的向量开关。
    enabled=True: vector_enabled=True, vector_status='pending'
    enabled=False: vector_enabled=False, 同时删除 Milvus 中该 page_id 的数据
    """

@app.post("/api/pages/{page_id}/vectorize")
async def trigger_vectorize(page_id: str) -> dict:
    """手动触发单个页面向量化（通常由 toggle 自动触发，也支持手动重试）。"""

@app.get("/api/worker/status")
async def worker_status() -> dict:
    """返回 Worker 运行状态：是否运行中、当前处理中的页面数、队列中的页面数。"""
```

### 前端变更

- **列表页**：每行新增 Toggle Switch（向量开关）+ 状态标签（pending/indexing/indexed/failed）+ 进度条（indexing 时显示）
- **状态列**：
  - `pending`：灰色，等待中
  - `indexing`：蓝色 + 进度条动画
  - `indexed`：绿色，含 chunk_count
  - `failed`：红色，hover 显示 `vector_error_message`
- **轮询**：`setInterval(() => fetch('/api/pages'), 2000)`

---

## CLI 变更

```python
# cli.py

@app.command()
def sync(
    root: str | None = None,
    full: bool = False,
    vectorize: bool = typer.Option(False, "--vectorize", help="拉取后自动向量化所有已启用页面"),
): ...

@app.command()
def vectorize(
    page_ids: list[str] = typer.Argument(default_factory=list),
    all_pending: bool = typer.Option(False, "--all", help="向量化所有 pending 且已启用的页面"),
): ...

@app.command()
def web(
    host: str = "127.0.0.1",
    port: int = 58000,
    max_concurrent: int = typer.Option(3, "--max-concurrent", help="后台向量化最大并发数"),
): ...
```

---

## 详细实现步骤

### Phase 1：数据模型 + Schema 迁移（1.5h）

1. **修改 `models.py`**：
   - `PageSyncState` 新增 `vector_enabled`, `vector_status`, `vector_error_message`, `vector_progress`
   - 新增 `VectorizeResult` 模型

2. **修改 `storage/sync_state.py`**：
   - 更新 DDL，新增 4 列
   - 实现 Schema 迁移检测（`PRAGMA table_info` 检测列是否存在，不存在则 `ALTER TABLE`）
   - `SyncStateStore` 新增方法：
     - `list_pending_vectorize() -> list[PageSyncState]`
     - `update_vector_status(page_id, status, progress=0, error=None)`
     - `update_vector_enabled(page_id, enabled)`

3. **测试**：单元测试验证新增字段的 CRUD。

### Phase 2：SyncService 拆分（2h）

1. **重构 `services/sync_service.py`**：
   - 提取 `sync_fetch()`：只执行 Notion 拉取 + SQLite 写入
   - 提取 `sync_vectorize()`：执行分块→Embedding→Milvus 写入
   - 修改 `sync()` 为组合调用，支持 `vectorize` 参数
   - `sync_fetch()` 中页面更新逻辑：若页面已存在且 `last_edited_time` 变化，且 `vector_enabled=True`，则重置 `vector_status='pending'`

2. **测试**：Mock 依赖，验证 fetch 不调用 Embedding/Milvus；验证 vectorize 正确调用各依赖。

### Phase 3：VectorizeWorker 实现（2.5h）

1. **新增 `services/vectorize_worker.py`**：
   - `VectorizeWorker` 类，含 `start/stop/enqueue/_run_page_vectorize`
   - `asyncio.Semaphore(max_concurrent)` 控制并发
   - 守护任务循环（`while self._running`）
   - 每个页面向量化协程：状态机推进 + 进度更新 + 异常捕获
   - 结构化日志：每步输出 `page_id`, `progress`, `stage`

2. **测试**：
   - 单元测试：Mock `SyncService`，验证 Semaphore 并发控制
   - 验证失败隔离：模拟第 2 个页面失败，第 1 和第 3 个成功
   - 验证状态正确流转：pending → indexing → indexed

### Phase 4：Web UI API 与前端（3h）

1. **修改 `web/web_server.py`**：
   - 启动时创建并启动 `VectorizeWorker`
   - 优雅关闭时 `worker.stop()`
   - 实现 `/api/pages`（含新字段）
   - 实现 `/api/pages/{page_id}/vector-toggle`
   - 实现 `/api/pages/{page_id}/vectorize`
   - 实现 `/api/worker/status`

2. **修改 `web/static/index.html`**：
   - 列表页新增 Toggle Switch + 状态标签 + 进度条
   - 2 秒轮询 `/api/pages`
   - Toggle 开关时调用 `/api/pages/{id}/vector-toggle`

3. **测试**：手动验证 Web UI 开关交互和状态同步。

### Phase 5：CLI 更新（1h）

1. **修改 `cli.py`**：
   - `sync` 命令新增 `--vectorize` 参数
   - 新增 `vectorize` 命令
   - `web` 命令新增 `--max-concurrent` 参数，传递给 Worker

2. **测试**：验证 CLI 参数解析和命令调用链。

### Phase 6：集成验收（2h）

1. **E2E 测试**：
   - 拉取 5 个页面 → 验证 SQLite 中状态为 `fetched`，`vector_enabled=False`
   - Web UI 打开 3 个页面的开关 → 验证 `vector_enabled=True`, `vector_status='pending'`
   - 观察 Worker 自动向量化 → 验证状态变为 `indexing` → `indexed`
   - 修改 Notion 页面 → 重新拉取 → 验证状态回退到 `pending`
   - 关闭开关 → 验证 Milvus 中该页面数据被删除

2. **异常测试**：
   - 模拟 Embedding API 失败 → 验证 `vector_status='failed'`，其他页面不受影响
   - 验证日志包含完整上下文（page_id, error_type, traceback）

---

## 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/rag_notion_kb/models.py` | 修改 | `PageSyncState` 新增 4 个字段；新增 `VectorizeResult` |
| `src/rag_notion_kb/storage/sync_state.py` | 修改 | DDL 新增 4 列；新增查询/更新方法 |
| `src/rag_notion_kb/services/sync_service.py` | 修改 | 拆分为 `sync_fetch` + `sync_vectorize` + `sync` |
| `src/rag_notion_kb/services/vectorize_worker.py` | **新增** | 后台异步向量化 Worker |
| `src/rag_notion_kb/web/web_server.py` | 修改 | 新增 API 路由，集成 Worker |
| `src/rag_notion_kb/web/static/index.html` | 修改 | 新增 Toggle、状态标签、进度条、轮询 |
| `src/rag_notion_kb/cli.py` | 修改 | 新增 `--vectorize` 参数、`vectorize` 命令、`--max-concurrent` |
| `tests/unit/test_sync_state.py` | 修改 | 新增字段测试 |
| `tests/unit/test_vectorize_worker.py` | **新增** | Worker 并发、失败隔离、状态流转 |
| `tests/e2e/test_web_ui.py` | **新增** | Web UI 开关交互 E2E |

---

## 验收验证

### 验证命令

```bash
# 1. 拉取页面（不向量化）
rag-kb sync --root <page_id>
# 验证：SQLite 中状态为 fetched，vector_enabled=False

# 2. 启动 Web UI
rag-kb web --max-concurrent 2
# 浏览器打开 http://127.0.0.1:58000

# 3. 在 Web UI 中打开某页面的向量开关
# 验证：Worker 自动启动向量化，状态从 pending → indexing → indexed
# 验证：Milvus 中该页面有数据

# 4. 修改 Notion 页面，重新执行 sync
rag-kb sync
# 验证：该页面 vector_status 回退到 pending

# 5. 关闭开关
# 验证：Milvus 中该页面数据被删除

# 6. 模拟失败
# 临时修改配置使 Embedding API 地址错误
# 验证：vector_status='failed'，vector_error_message 有内容，其他页面正常
```

### 关键日志检查

```json
{"timestamp":"2026-08-12T10:00:00Z","level":"INFO","module":"vectorize_worker","message":"page vectorize started","page_id":"a1b2c3d4","progress":0}
{"timestamp":"2026-08-12T10:00:05Z","level":"INFO","module":"vectorize_worker","message":"page chunking completed","page_id":"a1b2c3d4","progress":30,"chunk_count":12}
{"timestamp":"2026-08-12T10:00:15Z","level":"INFO","module":"vectorize_worker","message":"page embedding completed","page_id":"a1b2c3d4","progress":70}
{"timestamp":"2026-08-12T10:00:18Z","level":"INFO","module":"vectorize_worker","message":"page vectorize completed","page_id":"a1b2c3d4","progress":100,"status":"indexed"}
{"timestamp":"2026-08-12T10:00:20Z","level":"ERROR","module":"vectorize_worker","message":"page vectorize failed","page_id":"e5f6g7h8","error_type":"EmbeddingError","error_message":"DashScope API 429","progress":0}
```

---

*本计划基于用户确认的 Semaphore 并发控制、HTTP 轮询状态同步、保留开关并标记待更新的增量行为设计。*
