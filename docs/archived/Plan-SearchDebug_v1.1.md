# RAG Notion 知识库系统 — 混合检索效果评估 + 检索历史 实施计划

> **版本**：v1.1  
> **日期**：2026-08-12  
> **依据**：现有已实现代码（SearchService / hybrid_search / context_expand / reranker / MilvusStore / MCP Server / CLI）  
> **范围**：
> 1. 新增 Web **检索调试/评估页面**，支持全链路参数可调、中间分数暴露
> 2. 新增 **检索历史** 记录与回放，覆盖 Debug / MCP / CLI 全部检索来源

---

## 1. 背景与目标

### 1.1 当前已实现能力

系统检索 Pipeline 已完整实现：
- `SearchService.search()`：Embed → Dense ANN → BM25 Sparse → RRF 融合 → ReRank → Context Expand → Truncate
- `SearchService.debug_search()`（本计划新增）：暴露全链路中间分数
- `MilvusStore`：支持 dense + sparse 混合检索，含基础过滤
- `RerankerService`：DashScope Qwen3-VL-Rerank API 封装
- MCP Server：`rag_search` tool 供 Claude Code 调用
- CLI：`rag-kb search`（如已实现）或 `sync` 等命令
- Web UI：已有 Chunk Inspector 页面

### 1.2 新增需求

**需求 A — 检索调试/评估页面**：
1. 调参验证：Dense/Sparse 权重、TopK、相似度阈值、元数据过滤、上下文扩展
2. 效果对比：查看每个结果在 Pipeline 各阶段的分数
3. 分数可视化：直观展示 Dense / Sparse / RRF / ReRank / Final 分数

**需求 B — 检索历史**：

1. 记录所有检索操作：Debug 页面、MCP Server、CLI 的每次检索
2. 历史列表：时间、来源、查询、参数摘要、结果统计
3. 点击回放：点击历史记录，自动跳转到检索调试页面，恢复当时的参数和结果

---

## 2. 核心设计决策

| # | 决策 | 说明 |
|---|---|---|
| 1 | 检索历史统一存储在 SQLite | 新建 `search_history` 表，所有检索入口（Debug/MCP/CLI）调用后写入 |
| 2 | 历史记录异步写入 | 检索完成后，在后台线程写入历史，不阻塞返回 |
| 3 | 历史只存参数和摘要，不存完整结果 | 避免数据膨胀；回放时重新执行检索恢复结果 |
| 4 | 检索调试页面作为回放载体 | 点击历史记录 → 携带 `history_id` 跳转到调试页 → 自动填充参数并执行 |
| 5 | 加权分数融合替代固定 RRF | 支持 `dense_weight` + `sparse_weight = 1.0` 的线性加权融合 |
| 6 | 最小相似度在检索后过滤 | Dense 和 Sparse 分别检索后，按 `min_similarity` 过滤低分结果 |
| 7 | 上下文扩展三种模式映射到 `expand_to_level` | "none"→100, "parent"→`header_level-1`, "h2"→2 |

---

## 3. 数据模型设计

### 3.1 检索调试请求/响应模型

```python
class ContextExpandMode(str, Enum):
    NONE = "none"
    PARENT = "parent"
    H2 = "h2"

class DebugSearchRequest(BaseModel):
    query: str
    dense_weight: float = Field(0.5, ge=0.0, le=1.0)
    sparse_weight: float = Field(0.5, ge=0.0, le=1.0)
    top_k: int = Field(10, ge=1, le=100)
    min_similarity: float = Field(0.5, ge=0.0, le=1.0)
    rerank_model: str = "qwen3-vl-rerank"
    filters: dict[str, list[str]] = Field(default_factory=dict)
    context_mode: ContextExpandMode = ContextExpandMode.NONE
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
```

### 3.2 检索历史模型

```python
class SearchSource(str, Enum):
    DEBUG = "debug"   # Web 检索调试页面
    MCP = "mcp"       # MCP Server rag_search
    CLI = "cli"       # CLI 命令

class SearchHistory(BaseModel):
    history_id: str           # UUID v4
    query: str
    source: SearchSource
    params: dict[str, Any]    # 序列化的检索参数（DebugSearchRequest 或 search() 参数）
    result_summary: dict[str, Any]
    # {
    #   "total_results": 10,
    #   "top_score": 0.92,
    #   "latency_ms": 1250,
    #   "dense_count": 45,
    #   "sparse_count": 32
    # }
    created_at: str           # ISO 8601
```

### 3.3 SQLite DDL

```sql
-- 检索历史表
CREATE TABLE IF NOT EXISTS search_history (
    history_id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('debug','mcp','cli')),
    params TEXT NOT NULL,        -- JSON
    result_summary TEXT NOT NULL, -- JSON
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_search_history_created_at ON search_history(created_at);
CREATE INDEX IF NOT EXISTS idx_search_history_source ON search_history(source);
```

---

## 4. 后端扩展设计

### 4.1 加权融合（`retrieval/hybrid_search.py` 新增）

```python
def weighted_fusion(
    dense_hits: list[SearchHit],
    sparse_hits: list[SearchHit],
    dense_weight: float,
    sparse_weight: float,
) -> list[SearchHit]:
    """Linear weighted fusion: score = dense_weight*dense_score + sparse_weight*sparse_score."""
    scores: dict[int, float] = {}
    hits_by_id: dict[int, SearchHit] = {}
    for hit in dense_hits:
        scores[hit.id] = scores.get(hit.id, 0.0) + dense_weight * hit.score
        hits_by_id[hit.id] = hit
    for hit in sparse_hits:
        scores[hit.id] = scores.get(hit.id, 0.0) + sparse_weight * hit.score
        hits_by_id[hit.id] = hit
    sorted_ids = sorted(scores, key=scores.get, reverse=True)
    for hid in sorted_ids:
        hits_by_id[hid].score = scores[hid]
    return [hits_by_id[hid] for hid in sorted_ids]
```

### 4.2 `SearchService` 扩展

**新增 `debug_search()`**：
```python
def debug_search(self, request: DebugSearchRequest) -> DebugSearchResponse:
    """Full pipeline with stage scores exposed."""
    # 1. Embed
    # 2. Dense search → record dense_score per hit
    # 3. Sparse search → record sparse_score per hit
    # 4. Min-similarity filter
    # 5. Weighted fusion → record rrf_score
    # 6. ReRank → record rerank_score
    # 7. Context expand (per mode) → record final_score
    # 8. Truncate
```

**上下文扩展映射**：
| 模式 | expand_to_level 计算 |
|------|---------------------|
| `none` | `100`（> max header_level，只取当前 chunk） |
| `parent` | `max(1, hit.metadata.header_level - 1)` |
| `h2` | `2` |

**最小相似度过滤**：
```python
dense_hits = [h for h in dense_hits if h.score >= request.min_similarity]
sparse_hits = [h for h in sparse_hits if h.score >= request.min_similarity]
```

### 4.3 检索历史存储（`storage/search_history_store.py` 新增）

```python
class SearchHistoryStore:
    def __init__(self, db_path: Path): ...
    def add(self, record: SearchHistory) -> None: ...
    def list_recent(self, limit: int = 50, source: str | None = None) -> list[SearchHistory]: ...
    def get(self, history_id: str) -> SearchHistory | None: ...
    def delete(self, history_id: str) -> None: ...
    def clear_all(self) -> None: ...
```

### 4.4 检索历史记录 Hook

**记录点 1 — `SearchService.debug_search()` 完成后**：
```python
# 在 debug_search 返回前
history = SearchHistory(
    history_id=str(uuid.uuid4()),
    query=request.query,
    source=SearchSource.DEBUG,
    params=request.model_dump(),
    result_summary={
        "total_results": len(response.results),
        "top_score": response.results[0].scores.final_score if response.results else 0,
        "latency_ms": response.latency_ms,
        "dense_count": response.total_dense,
        "sparse_count": response.total_sparse,
    },
    created_at=datetime.now(timezone.utc).isoformat(),
)
# 后台线程异步写入
threading.Thread(target=self._history_store.add, args=(history,), daemon=True).start()
```

**记录点 2 — `SearchService.search()` 完成后**（MCP 和 CLI 调用）：
```python
# 在 search() 返回后，由调用方记录
# 或在 search() 内部增加可选的 history_store 参数
```

**记录点 3 — MCP Server `rag_search` tool**：
```python
# tool 执行完成后，将 query + params + results 写入历史
```

**记录点 4 — CLI 检索命令**：
```python
# CLI 执行 search 后，将参数和结果摘要写入历史
```

### 4.5 元数据过滤扩展（`storage/milvus_store.py`）

扩展 `_build_filter_expr()` 支持通用 KV：
```python
def _build_filter_expr(self, filters: dict[str, list[str]]) -> str | None:
    # page_ids → page_id in [...]
    # header_level → header_level in [1,2,3] 或 header_level == 2
    # chunk_type → chunk_type in ['text','table','code','image']
    # page_title → page_title in ['A','B']
    # edited_after → last_edited_time > "..."
```

---

## 5. Web API 设计

### 5.1 检索调试 API

```python
# POST /api/search/debug
# 请求: DebugSearchRequest
# 响应: DebugSearchResponse
# 副作用: 异步写入 search_history（source=debug）
```

### 5.2 检索历史 API

```python
# GET /api/search/history?limit=50&source=debug|mcp|cli
# 响应: list[SearchHistory]

# GET /api/search/history/{history_id}
# 响应: SearchHistory（含完整 params，用于回放）

# POST /api/search/history/{history_id}/replay
# 逻辑: 读取历史记录的 params → 调用 debug_search() → 返回 DebugSearchResponse
# 响应: DebugSearchResponse

# DELETE /api/search/history/{history_id}
# 响应: {"deleted": true}

# DELETE /api/search/history?clear=all
# 响应: {"cleared": true, "count": 123}
```

---

## 6. Web UI 设计

### 6.1 导航结构

```
┌─────────────────────────────────────────┐
│  RAG Notion KB  [Chunk Inspector] [Root管理] [检索调试] [检索历史] │
└─────────────────────────────────────────┘
```

### 6.2 检索调试页布局

左侧参数面板 + 右侧结果面板（详见 v1.0 Plan）。

**新增 — URL 参数支持回放**：

- `/search-debug?history_id=xxx` → 自动加载历史参数并执行检索
- `/search-debug` → 空白参数面板，等待用户输入

### 6.3 检索历史页布局

```
┌─────────────────────────────────────────────────────────────────────────┐
│  检索历史                                         [🗑 清空全部]            │
├─────────────────────────────────────────────────────────────────────────┤
│  来源筛选: [全部 ▼]  查询: [________]  [搜索]                              │
├─────────────────────────────────────────────────────────────────────────┤
│  时间 ▼           | 来源    | 查询问题              | 结果 | Top分 | 操作   │
├─────────────────────────────────────────────────────────────────────────┤
│  2026-08-12 10:05 | debug   | 登录流程的验证方式     | 10   | 0.92 | [预览] │
│  2026-08-12 09:58 | mcp     | 验证码有效期多久       | 5    | 0.88 | [预览] │
│  2026-08-12 09:45 | cli     | 架构设计的技术选型     | 10   | 0.85 | [预览] │
│  2026-08-12 09:30 | debug   | 用户注册需要哪些字段   | 8    | 0.91 | [预览] │
└─────────────────────────────────────────────────────────────────────────┘
```

**交互细节**：
- **来源筛选**：下拉选择 `全部 / debug / mcp / cli`
- **查询搜索**：输入关键词，过滤 `query` 字段（前端本地过滤或后端模糊查询）
- **预览按钮**：点击后跳转 `/search-debug?history_id=xxx`
- **删除单条**：hover 显示删除图标，确认后删除
- **清空全部**：顶部按钮，二次确认后清空

**来源标签颜色**：
| 来源 | 颜色 |
|------|------|
| debug | 蓝色 `#2563eb` |
| mcp | 紫色 `#7c3aed` |
| cli | 灰色 `#6b7280` |

### 6.4 检索调试页 — 回放逻辑

```javascript
// 页面加载时检查 URL 参数
const urlParams = new URLSearchParams(window.location.search);
const historyId = urlParams.get('history_id');

if (historyId) {
    // 1. 加载历史记录参数
    fetch(`/api/search/history/${historyId}`)
        .then(r => r.json())
        .then(history => {
            // 2. 填充参数面板
            document.getElementById('query').value = history.query;
            document.getElementById('dense_weight').value = history.params.dense_weight;
            // ... 填充所有参数
            // 3. 自动执行检索
            executeSearch(history.params);
        });
}
```

---

## 7. 实现步骤

### Phase 1：数据模型 + 加权融合 + 历史存储（2.5h）

1. **修改 `models.py`**：
   - 新增 `ContextExpandMode`, `DebugSearchRequest`, `StageScores`, `DebugSearchHit`, `DebugSearchResponse`
   - 新增 `SearchSource`, `SearchHistory`

2. **修改 `retrieval/hybrid_search.py`**：
   - 新增 `weighted_fusion()`

3. **新增 `storage/search_history_store.py`**：
   - `SearchHistoryStore` 类 + DDL
   - CRUD + list_recent + clear_all

4. **测试**：
   - `test_hybrid_search.py`：加权融合
   - `test_search_history_store.py`：CRUD + 分页 + 筛选

### Phase 2：SearchService 扩展（2.5h）

1. **修改 `services/search_service.py`**：
   - 新增 `debug_search()` 方法（全链路 + 阶段分数追踪）
   - 新增 `_record_history()` 辅助方法
   - 修改 `search()` 增加可选 `history_store` 参数用于记录 MCP/CLI 检索
   - 上下文扩展模式映射逻辑
   - 最小相似度过滤

2. **修改 `storage/milvus_store.py`**：
   - 扩展 `_build_filter_expr()` 支持通用 KV 过滤

3. **测试**：
   - `test_search_service_debug.py`：Mock 依赖，验证 stage scores 正确
   - 验证 min_similarity 过滤
   - 验证 context_mode 映射

### Phase 3：Web 后端 API（1h）

1. **修改 `web/web_server.py`**：
   - 注入 `SearchHistoryStore`
   - `POST /api/search/debug`
   - `GET /api/search/history`
   - `GET /api/search/history/{id}`
   - `POST /api/search/history/{id}/replay`
   - `DELETE /api/search/history/{id}`
   - `DELETE /api/search/history?clear=all`

2. **修改 `cli.py` 的 `web` 命令**：
   - 初始化 `SearchHistoryStore` 并注入

3. **测试**：
   - `TestClient` 验证所有历史 API

### Phase 4：MCP + CLI 历史记录 Hook（0.5h）

1. **修改 `mcp_server.py`**：
   - `rag_search` tool 执行完成后，调用 `SearchHistoryStore.add()`

2. **修改 `cli.py`**：
   - 检索相关命令执行完成后，写入历史

### Phase 5：Web 前端（4h）

1. **修改 `web/static/index.html`**：
   - Sidebar 新增"检索调试"和"检索历史"导航
   - **检索调试视图**：
     - 参数面板（权重滑动杆、TopK、min_similarity、过滤、扩展模式）
     - URL 参数解析（`history_id`）自动回放
     - 结果面板（统计栏 + 结果卡片 + 分数条可视化）
   - **检索历史视图**：
     - 筛选栏（来源下拉 + 查询输入）
     - 历史表格（时间、来源标签、查询、结果数、Top分、操作）
     - 分页或滚动加载
     - 清空全部按钮（二次确认）
     - 删除单条按钮

2. **前端逻辑**：
   - 权重滑动杆联动（dense + sparse = 1.0）
   - 元数据过滤动态添加/删除
   - 检索请求 + Loading 状态
   - 历史列表加载 + 筛选
   - 回放跳转（携带 history_id）

### Phase 6：集成验收（1.5h）

1. **手动测试**：
   - 检索调试页调参 → 验证结果变化
   - 执行检索 → 切换到历史页 → 验证记录出现
   - 点击历史记录预览 → 验证参数恢复和结果一致
   - MCP 调用检索 → 验证历史中出现 mcp 来源记录
   - 清空历史 → 验证列表为空

2. **边界测试**：
   - 历史记录 1000+ 条 → 验证列表加载性能
   - 删除不存在的 history_id → 验证 404
   - 回放时原始参数已不兼容（如字段变更）→ 优雅降级

---

## 8. 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/rag_notion_kb/models.py` | 修改 | 新增 DebugSearch* + SearchHistory 模型 |
| `src/rag_notion_kb/retrieval/hybrid_search.py` | 修改 | 新增 `weighted_fusion()` |
| `src/rag_notion_kb/services/search_service.py` | 修改 | 新增 `debug_search()` + 历史记录 Hook |
| `src/rag_notion_kb/storage/milvus_store.py` | 修改 | 扩展 `_build_filter_expr()` |
| `src/rag_notion_kb/storage/search_history_store.py` | **新增** | `SearchHistoryStore` + DDL |
| `src/rag_notion_kb/web/web_server.py` | 修改 | 新增 `/api/search/debug` + `/api/search/history/*` |
| `src/rag_notion_kb/mcp_server.py` | 修改 | `rag_search` 完成后写入历史 |
| `src/rag_notion_kb/cli.py` | 修改 | 检索命令完成后写入历史；`web` 命令注入 HistoryStore |
| `src/rag_notion_kb/web/static/index.html` | 修改 | 检索调试页 + 检索历史页 |
| `tests/unit/test_hybrid_search.py` | 修改 | 加权融合测试 |
| `tests/unit/test_search_service_debug.py` | **新增** | debug_search 全流程测试 |
| `tests/unit/test_search_history_store.py` | **新增** | 历史存储 CRUD 测试 |
| `tests/e2e/test_web_search_debug.py` | **新增** | Web UI 调试 + 历史 E2E |

---

## 9. API 详细契约

### `POST /api/search/debug`

**请求**：
```json
{
  "query": "登录流程的验证方式",
  "dense_weight": 0.7,
  "sparse_weight": 0.3,
  "top_k": 10,
  "min_similarity": 0.5,
  "rerank_model": "qwen3-vl-rerank",
  "filters": {"chunk_type": ["text"], "header_level": ["2","3"]},
  "context_mode": "parent",
  "max_tokens": 4000
}
```

**响应**：`DebugSearchResponse`（同 v1.0 Plan）

### `GET /api/search/history`

**查询参数**：`limit` (默认50), `source` (可选: debug/mcp/cli)

**响应**：
```json
[
  {
    "history_id": "550e8400-...",
    "query": "登录流程的验证方式",
    "source": "debug",
    "params": {"dense_weight": 0.7, ...},
    "result_summary": {"total_results": 10, "top_score": 0.92, "latency_ms": 1250},
    "created_at": "2026-08-12T10:05:00Z"
  }
]
```

### `POST /api/search/history/{history_id}/replay`

**响应**：`DebugSearchResponse`

---

## 10. 验收验证

### 10.1 功能验收清单

| # | 验收项 | 验证方法 |
|---|--------|----------|
| 1 | 检索调试参数调参生效 | 调整权重/过滤，结果变化 |
| 2 | 分数可视化显示 5 阶段分数 | 卡片内分数条与 API 一致 |
| 3 | 检索后历史记录自动保存 | 执行检索 → 历史页出现新记录 |
| 4 | MCP 检索记录在历史中 | Claude Code 调用 rag_search → 历史页出现 mcp 记录 |
| 5 | CLI 检索记录在历史中 | CLI 执行 search → 历史页出现 cli 记录 |
| 6 | 点击历史预览恢复参数 | 点击历史 → 调试页参数与当时一致 |
| 7 | 回放结果与原检索一致 | 同一 history_id 多次回放，结果相同 |
| 8 | 来源筛选生效 | 筛选 mcp → 只显示 mcp 记录 |
| 9 | 清空历史生效 | 点击清空 → 列表为空，数据库清空 |
| 10 | 最小相似度过滤生效 | min_similarity=0.9 → 结果减少 |

### 10.2 性能目标

| 指标 | 目标值 |
|------|--------|
| 检索调试单次查询 | < 3s（含 ReRank） |
| 历史列表加载（50条） | < 200ms |
| 回放加载 | < 3s |
| 历史写入延迟 | < 50ms（异步，不影响检索） |

---

## 11. 修订历史

| 版本 | 日期 | 作者 | 变更内容 |
|------|------|------|----------|
| v1.0 | 2026-08-12 | Claude | 初始版本：检索调试页面设计 |
| v1.1 | 2026-08-12 | Claude | 新增检索历史功能：统一记录 Debug/MCP/CLI 检索、历史列表页、回放跳转、数据模型、API、存储层 |

---

*本文档为混合检索效果评估 + 检索历史功能的权威实施计划。检索历史覆盖所有检索入口，是系统可观测性的重要组成部分。*
