# RAG Notion 知识库系统 — Notion Root 页面管理 + 选择性同步 实施计划

> **版本**：v1.0  
> **日期**：2026-08-12  
> **依据**：现有已实现代码（向量开关 + 后台异步向量化）  
> **范围**：新增 Notion Root 管理 Web 页面、支持多 Root 增删、选择性批量同步、同步进度追踪

---

## 1. 背景与目标

### 1.1 当前已实现能力

系统已完成向量开关 + 后台异步向量化架构：
- `sync_fetch()`：拉取 Notion 页面元数据 + Markdown → SQLite
- `sync_vectorize()`：对开启向量的页面执行分块 → Embedding → Milvus
- `VectorizeWorker`：后台异步轮询执行向量化
- Web UI：页面列表 + 向量开关 Toggle + 状态展示

### 1.2 新增需求

用户需要在同步之前有一个**Notion 页面管理界面**，实现：

1. **Root 页面管理**：支持添加/删除多个 Notion Root 页面
2. **页面树浏览**：输入 Root 的 page_id，加载该 Root 的所有子孙页面（只拉取页面信息，不获取内容）
3. **选择性同步**：在页面树中多选页面，触发 `sync_fetch` 并查看同步进度
4. **Root 隔离**：增删某个 Root 不影响其他 Root

### 1.3 设计原则

| 原则 | 说明 |
|------|------|
| **Root 配置独立存储** | Root 页面列表从 `config.yaml` 迁移到 SQLite 独立表，Web UI 可直接读写 |
| **树数据实时拉取** | 页面树从 Notion API 实时获取，不持久化到本地（避免与 sync_fetch 的 `page_sync_state` 混淆） |
| **轻量进度追踪** | 同步进度用内存 Tracker（单进程 FastAPI），支持 Web 轮询查询 |
| **向后兼容** | CLI `rag-kb sync --root <id>` 仍然可用；Web UI 管理的 Root 也作为 CLI 默认 Root 列表 |

---

## 2. 核心设计决策

| # | 决策 | 说明 |
|---|---|---|
| 1 | Root 配置存储在 SQLite | 新建 `notion_roots` 表，Web UI 增删即写 SQLite，CLI 启动时读取该表作为默认 Root 列表 |
| 2 | 页面树不入库 | `/api/roots/{root_id}/tree` 实时调用 Notion API 返回树结构，仅用于展示和选择，不同步到本地 |
| 3 | 同步进度内存追踪 | `SyncProgressTracker` 用内存 dict 管理（单进程 uvicorn 足够），任务完成后保留 5 分钟供查询 |
| 4 | 批量同步复用 `sync_fetch` | 选中页面的 page_ids 传给 `sync_fetch` 的内部循环，通过 `progress_callback` 回传进度 |
| 5 | 删除 Root 不级联删除数据 | 删除 Root 仅从 `notion_roots` 表中移除配置，不影响 `page_sync_state` 和 Milvus 中已同步的数据 |

---

## 3. 数据模型与存储

### 3.1 `NotionRoot` 模型（`models.py` 新增）

```python
class NotionRoot(BaseModel):
    """A configured Notion root page for sync."""
    root_id: str  # PK, same as page_id
    page_title: str
    page_url: str
    added_time: str  # ISO 8601
    is_active: bool = True
```

### 3.2 `NotionRootStore`（新增 `storage/root_store.py`）

```python
class NotionRootStore:
    def __init__(self, db_path: Path): ...
    def add(self, root: NotionRoot) -> None: ...
    def remove(self, root_id: str) -> None: ...
    def list_all(self) -> list[NotionRoot]: ...
    def get(self, root_id: str) -> NotionRoot | None: ...
    def list_active_ids(self) -> list[str]: ...
```

### 3.3 SQLite DDL（在 `sync_state.py` 或独立文件中）

```sql
CREATE TABLE IF NOT EXISTS notion_roots (
    root_id TEXT PRIMARY KEY,
    page_title TEXT NOT NULL,
    page_url TEXT NOT NULL,
    added_time TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);
```

### 3.4 同步进度模型（新增 `models.py`）

```python
class SyncTaskStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

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
```

---

## 4. NotionClient 复用分析

现有 `NotionClient.enumerate_pages(root_page_ids)` 已经实现：
- DFS 递归遍历所有子孙页面
- 只调用 `get_page_metadata()`（轻量，不拉取内容）
- 返回 `list[PageMetadata]`

**无需新增方法**，直接复用 `enumerate_pages` 即可。但 Web UI 的树展示需要层级关系，所以 API 层需要将扁平列表重建为树结构。

---

## 5. SyncService 扩展

### 5.1 `sync_fetch` 支持进度回调

```python
def sync_fetch(
    self,
    root_page_ids: list[str] | None = None,
    force_full: bool = False,
    page_ids: list[str] | None = None,  # 新增：直接指定要同步的页面
    progress_callback: Callable[[str, str, int, int], None] | None = None,  # 新增
) -> SyncResult:
    """
    progress_callback(current_page_id, current_page_title, completed, total)
    """
```

**逻辑变更**：
- 若 `page_ids` 提供，则只同步这些页面（跳过 `enumerate_pages` 的遍历）
- 否则保持原有逻辑：按 `root_page_ids` 枚举所有页面后同步
- 在 `_fetch_page()` 调用前后触发 `progress_callback`

### 5.2 新增 `SyncProgressTracker`（`services/sync_progress.py`）

```python
class SyncProgressTracker:
    def __init__(self, ttl_seconds: int = 300): ...  # 保留 5 分钟
    def create_task(self, total_pages: int) -> str: ...  # 返回 sync_id
    def update(self, sync_id: str, page_id: str, page_title: str, completed: int): ...
    def complete(self, sync_id: str, results: SyncResult): ...
    def fail(self, sync_id: str, error: str): ...
    def get(self, sync_id: str) -> SyncTask | None: ...
    def cleanup(self) -> None: ...  # 清理过期任务
```

**实现要点**：
- 用 `dict[str, SyncTask]` 存储
- `cleanup()` 在 `create_task()` 时自动调用，删除 `finished_at` 超过 ttl 的任务
- 线程安全：使用 `threading.Lock`（因为 `sync_fetch` 在 thread 中执行）

---

## 6. Web API 设计

### 6.1 Root 管理 API

```python
# GET /api/roots
# 返回: list[NotionRoot]

# POST /api/roots
# 请求体: {"page_id": "a1b2c3d4-..."}
# 逻辑: 
#   1. 调用 NotionClient.get_page_metadata(page_id) 验证页面存在
#   2. 写入 notion_roots 表
#   3. 返回创建的 NotionRoot
# 错误: 404(页面不存在), 409(已存在)

# DELETE /api/roots/{root_id}
# 逻辑: 从 notion_roots 表中删除，不级联删除 page_sync_state/Milvus
# 返回: {"root_id": "...", "deleted": true}
```

### 6.2 页面树 API

```python
# GET /api/roots/{root_id}/tree
# 逻辑:
#   1. 验证 root_id 在 notion_roots 中存在
#   2. 调用 NotionClient.enumerate_pages([root_id]) 实时拉取所有子孙页面
#   3. 将扁平列表重建为树结构返回
# 返回:
# {
#   "root_id": "...",
#   "root_title": "...",
#   "pages": [
#     {"page_id": "...", "title": "...", "url": "...", "last_edited_time": "...", "children": [...]}
#   ]
# }
# 
# 注意：此处 "pages" 是树形结构，children 嵌套
```

### 6.3 批量同步 API

```python
# POST /api/sync
# 请求体: {"page_ids": ["id1", "id2", ...], "force_full": false}
# 逻辑:
#   1. Tracker 创建任务，返回 sync_id
#   2. 在后台 asyncio.Task 中执行 sync_fetch(page_ids=..., progress_callback=...)
#   3. 立即返回 {"sync_id": "...", "status": "started"}
# 
# 注意：不自动向量化，只执行 fetch。向量开关由后续 Web UI 操作控制。

# GET /api/sync/{sync_id}/progress
# 返回: SyncTask 的 JSON 序列化
```

### 6.4 现有 API 兼容

- `/api/pages`：保持现有逻辑，返回已同步的 `page_sync_state` 列表
- `/api/pages/{page_id}/vector-toggle`：保持不变
- `/api/pages/{page_id}/vectorize`：保持不变
- `/api/worker/status`：保持不变

---

## 7. Web UI 设计

### 7.1 导航结构

```
┌─────────────────────────────────────────┐
│  RAG Notion KB        [Root管理] [页面列表] │
├─────────────────────────────────────────┤
```

- **Root 管理**：新功能，默认首页
- **页面列表**：现有功能（已同步页面 + 向量开关）

### 7.2 Root 管理页布局

```
┌─────────────────────────────────────────┐
│ [+ 添加 Root]                            │
├─────────────────────────────────────────┤
│ ┌─────────────┐  ┌─────────────┐       │
│ │ Root: 产品文档 │  │ Root: 技术博客 │       │
│ │ id: xxx...  │  │ id: yyy...  │       │
│ │ [删除] [展开树]│  │ [删除] [展开树]│       │
│ └─────────────┘  └─────────────┘       │
├─────────────────────────────────────────┤
│ 页面树（展开后）                          │
│ ▼ 产品文档                                │
│   ☐ 需求分析                              │
│   ☐ 架构设计                              │
│     ☐ 技术选型                            │
│     ☐ 数据模型                            │
│   ☐ 上线计划                              │
│                                         │
│ [全选] [取消全选]  [同步选中页面 (3)]      │
│                                         │
│ 同步进度: ████████░░ 80% (4/5) 当前: 数据模型 │
└─────────────────────────────────────────┘
```

### 7.3 前端交互流程

1. **加载 Root 列表**：`GET /api/roots` → 渲染卡片
2. **添加 Root**：弹窗输入 page_id → `POST /api/roots` → 刷新列表
3. **删除 Root**：确认弹窗 → `DELETE /api/roots/{id}` → 刷新列表
4. **展开树**：`GET /api/roots/{id}/tree` → 渲染树形组件（递归 `<ul>`）
5. **选择页面**：勾选 checkbox，底部显示已选数量
6. **批量同步**：点击按钮 → `POST /api/sync` → 获得 sync_id → 显示进度条
7. **轮询进度**：每 1 秒 `GET /api/sync/{sync_id}/progress` → 更新进度条
8. **同步完成**：提示"同步完成，点击前往页面列表开启向量化" → 导航到页面列表

### 7.4 同步进度 UI

```
┌─────────────────────────┐
│ 同步进度                  │
│ ████████░░ 80% (4/5)     │
│ 当前: 数据模型 (id: xxx)  │
│                         │
│ [取消]                  │
└─────────────────────────┘
```

---

## 8. CLI 兼容性变更

### 8.1 `rag-kb sync` 默认 Root 来源

当前 `sync` 命令的 Root 来源优先级：
1. `--root` 参数
2. `config.notion.root_page_ids`

**变更后优先级**：
1. `--root` 参数（最高）
2. `NotionRootStore.list_active_ids()`（从 SQLite 读取 Web UI 管理的 Root）
3. `config.notion.root_page_ids`（兜底兼容）

### 8.2 `rag-kb config` 显示

`config` 命令的 Root 显示改为：
- 先从 SQLite 读取 `notion_roots` 列表
- 若为空，显示 `config.notion.root_page_ids`

---

## 9. 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/rag_notion_kb/models.py` | 修改 | 新增 `NotionRoot`、`SyncTaskStatus`、`SyncTask` |
| `src/rag_notion_kb/storage/root_store.py` | **新增** | `NotionRootStore`：SQLite CRUD |
| `src/rag_notion_kb/storage/sync_state.py` | 修改 | 可选：新增 `list_by_root_id`（若需要按 Root 过滤） |
| `src/rag_notion_kb/services/sync_service.py` | 修改 | `sync_fetch` 新增 `page_ids` 和 `progress_callback` 参数 |
| `src/rag_notion_kb/services/sync_progress.py` | **新增** | `SyncProgressTracker`：内存同步任务追踪 |
| `src/rag_notion_kb/web/web_server.py` | 修改 | 新增 `/api/roots/*`、`/api/sync`、`/api/sync/{id}/progress` 路由 |
| `src/rag_notion_kb/web/static/index.html` | 修改 | 新增 Root 管理页、页面树、批量同步、进度 UI |
| `src/rag_notion_kb/cli.py` | 修改 | `sync` 命令 Root 来源增加 `NotionRootStore` |
| `tests/unit/test_root_store.py` | **新增** | Root Store CRUD 测试 |
| `tests/unit/test_sync_progress.py` | **新增** | Tracker 状态流转测试 |
| `tests/e2e/test_web_roots.py` | **新增** | Web UI Root 管理 E2E 测试 |

---

## 10. 详细实现步骤

### Phase 1：数据层（1.5h）

1. **修改 `models.py`**：
   - 新增 `NotionRoot` 模型
   - 新增 `SyncTaskStatus` Enum 和 `SyncTask` 模型

2. **新增 `storage/root_store.py`**：
   - `NotionRootStore` 类
   - DDL 创建 `notion_roots` 表（复用现有 SQLite 连接或独立连接）
   - 建议复用现有 `sync_state.db`，同一数据库不同表
   - CRUD 方法实现

3. **测试**：
   - `tests/unit/test_root_store.py`：验证增删查

### Phase 2：SyncService + ProgressTracker（2h）

1. **修改 `services/sync_service.py`**：
   - `sync_fetch()` 签名增加 `page_ids` 和 `progress_callback`
   - 内部逻辑：若 `page_ids` 提供，遍历 `page_ids` 而非 `enumerate_pages`
   - 在 `_fetch_page()` 前后调用 `progress_callback`

2. **新增 `services/sync_progress.py`**：
   - `SyncProgressTracker` 实现
   - 线程安全（`threading.Lock`）
   - TTL 自动清理

3. **测试**：
   - `tests/unit/test_sync_progress.py`：验证创建→更新→完成→查询→清理流程
   - Mock `progress_callback` 验证调用次数和参数

### Phase 3：Web 后端 API（2h）

1. **修改 `web/web_server.py`**：
   - `create_app()` 新增 `root_store` 和 `progress_tracker` 依赖注入
   - 实现 `/api/roots` (GET/POST/DELETE)
   - 实现 `/api/roots/{root_id}/tree` (GET)
   - 实现 `/api/sync` (POST)
   - 实现 `/api/sync/{sync_id}/progress` (GET)
   - 所有 API 的错误处理（404, 409, 500）

2. **修改 `cli.py`** 的 `web` 命令：
   - 初始化 `NotionRootStore`
   - 初始化 `SyncProgressTracker`
   - 注入到 `create_app()`

3. **测试**：
   - 用 `TestClient` 测试 API 路由
   - 验证添加 Root 时 Notion API 验证逻辑

### Phase 4：Web 前端（3h）

1. **修改 `web/static/index.html`**：
   - 添加顶部导航栏（Root 管理 / 页面列表）
   - **Root 管理视图**：
     - Root 卡片列表（标题、ID、添加时间、操作按钮）
     - 添加 Root 弹窗（输入 page_id，验证中状态）
     - 页面树组件（递归渲染，checkbox 选择）
     - 底部批量操作栏（显示已选数量 + 同步按钮）
     - 同步进度模态框（进度条、当前页面、取消按钮）
   - **页面列表视图**：
     - 保持现有 UI，添加导航返回按钮

2. **前端逻辑**：
   - 树形数据结构渲染（递归函数）
   - 多选状态管理（set of page_ids）
   - 同步流程：POST /api/sync → 轮询 GET /api/sync/{id}/progress
   - 错误提示（toast/snackbar）

### Phase 5：CLI 兼容（0.5h）

1. **修改 `cli.py` 的 `sync` 命令**：
   - `_resolve_root_page_ids()` 增加从 `NotionRootStore` 读取的逻辑

2. **修改 `cli.py` 的 `config` 命令**：
   - Root 显示优先从 SQLite 读取

### Phase 6：集成验收（2h）

1. **手动测试流程**：
   - 启动 Web UI → 添加 2 个 Root → 验证列表显示
   - 展开 Root A → 验证树结构正确
   - 选择 3 个页面 → 点击同步 → 验证进度条更新
   - 同步完成后 → 切换到"页面列表"→ 验证新页面出现
   - 删除 Root A → 验证 Root B 不受影响
   - CLI `rag-kb sync` → 验证自动读取 SQLite 中的 Root

2. **边界测试**：
   - 添加无效的 page_id → 验证 404 错误
   - 重复添加同一 Root → 验证 409 错误
   - 同步过程中关闭浏览器 → 重新打开后查询进度 → 验证能获取到完成状态
   - 大 Root（50+ 页面）→ 验证树加载性能和展示

---

## 11. API 详细契约

### 11.1 `POST /api/roots`

**请求**：
```json
{
  "page_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

**成功响应 201**：
```json
{
  "root_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "page_title": "产品文档",
  "page_url": "https://www.notion.so/...",
  "added_time": "2026-08-12T10:00:00Z",
  "is_active": true
}
```

**错误响应**：
- `404`：Notion 中不存在该页面
- `409`：该 Root 已存在
- `500`：Notion API 调用失败

### 11.2 `GET /api/roots/{root_id}/tree`

**成功响应 200**：
```json
{
  "root_id": "a1b2c3d4-...",
  "root_title": "产品文档",
  "total_pages": 12,
  "tree": [
    {
      "page_id": "b2c3d4e5-...",
      "title": "需求分析",
      "url": "https://www.notion.so/...",
      "last_edited_time": "2026-08-10T08:00:00Z",
      "children": [
        {
          "page_id": "c3d4e5f6-...",
          "title": "用户调研",
          "url": "...",
          "last_edited_time": "...",
          "children": []
        }
      ]
    }
  ]
}
```

### 11.3 `POST /api/sync`

**请求**：
```json
{
  "page_ids": ["b2c3d4e5-...", "c3d4e5f6-..."],
  "force_full": false
}
```

**成功响应 202**（Accepted，异步执行）：
```json
{
  "sync_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "started",
  "total_pages": 2
}
```

### 11.4 `GET /api/sync/{sync_id}/progress`

**运行中响应**：
```json
{
  "sync_id": "550e8400-...",
  "status": "running",
  "total_pages": 5,
  "completed_pages": 3,
  "current_page_id": "d4e5f6g7-...",
  "current_page_title": "数据模型",
  "started_at": "2026-08-12T10:00:00Z",
  "finished_at": null
}
```

**完成响应**：
```json
{
  "sync_id": "550e8400-...",
  "status": "completed",
  "total_pages": 5,
  "completed_pages": 5,
  "current_page_id": null,
  "current_page_title": null,
  "results": {
    "added": 2,
    "updated": 3,
    "removed": 0,
    "skipped": 0,
    "failed": 0
  },
  "started_at": "2026-08-12T10:00:00Z",
  "finished_at": "2026-08-12T10:00:15Z"
}
```

---

## 12. 风险与应对

| 风险 | 影响 | 可能性 | 应对策略 |
|------|------|--------|----------|
| Notion API 限流导致树加载慢 | 页面树展开延迟高 | 高 | 添加加载动画；缓存树到 sessionStorage；限制展开深度 |
| 大 Root（100+ 页面）树渲染性能差 | 前端卡顿 | 中 | 虚拟滚动或分页加载；默认折叠深层节点 |
| 同步任务 Tracker 内存泄漏 | 长时间运行后内存增长 | 低 | TTL 自动清理；限制最大保留任务数（如 100 个） |
| 并发同步与后台 VectorizeWorker 冲突 | Milvus 写入冲突 | 低 | `sync_fetch` 不写 Milvus，只写 SQLite；与 Worker 无冲突 |
| Root 删除后重新添加，页面重复 | SQLite 中重复 page_id | 低 | `page_sync_state` 以 `page_id` 为 PK，`upsert` 自动去重 |

---

## 13. 验收验证

### 13.1 功能验收清单

| # | 验收项 | 验证方法 |
|---|--------|----------|
| 1 | 添加 Root 成功，列表显示 | Web UI 手动测试 |
| 2 | 添加无效 page_id 返回 404 | API 测试（TestClient） |
| 3 | 重复添加返回 409 | API 测试 |
| 4 | 删除 Root 不影响其他 Root | 添加 2 个 Root，删 1 个，验证另 1 个仍在 |
| 5 | 展开 Root 显示正确树结构 | 对比 Notion 实际结构 |
| 6 | 多选页面后批量同步 | 选择 3 页，点击同步，验证 SQLite 中新增 3 条记录 |
| 7 | 同步进度实时更新 | 轮询进度 API，验证 completed_pages 递增 |
| 8 | 同步完成后页面列表可见 | 同步完成后切换视图，验证新页面出现 |
| 9 | CLI `rag-kb sync` 读取 SQLite Root | 删除 config.yaml 中的 root，验证 CLI 仍能同步 |
| 10 | 大 Root 加载不超时 | 测试 50+ 页面的 Root，树加载 < 10s |

### 13.2 性能目标

| 指标 | 目标值 |
|------|--------|
| 添加 Root API 响应 | < 2s（含 Notion API 验证） |
| 页面树加载（20 页） | < 3s |
| 页面树加载（100 页） | < 10s |
| 批量同步 10 页 | < 15s（不含向量化和网络波动） |
| 进度轮询响应 | < 50ms |

---

## 14. 修订历史

| 版本 | 日期 | 作者 | 变更内容 |
|------|------|------|----------|
| v1.0 | 2026-08-12 | Claude | 初始版本，基于现有向量开关架构，设计 Notion Root 管理 + 选择性同步 + 进度追踪的完整实施计划 |

---

*本文档为 Notion Root 页面管理功能的权威实施计划，开发需严格按 Phase 顺序执行，每 Phase 完成后进行验收测试。*
