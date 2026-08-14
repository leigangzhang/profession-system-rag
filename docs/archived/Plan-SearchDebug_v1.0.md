# RAG Notion 知识库系统 — 混合检索效果评估页面 实施计划

> **版本**：v1.0  
> **日期**：2026-08-12  
> **依据**：现有已实现代码（SearchService / hybrid_search / context_expand / reranker / MilvusStore）  
> **范围**：新增 Web 检索调试/评估页面，支持全链路参数可调、中间分数暴露、效果对比

---

## 1. 背景与目标

### 1.1 当前已实现能力

系统检索 Pipeline 已完整实现：

- `SearchService.search()`：Embed → Dense ANN → BM25 Sparse → RRF 融合 → ReRank → Context Expand → Truncate
- `MilvusStore`：支持 dense + sparse 混合检索，含基础过滤（page_ids / header_level）
- `RerankerService`：DashScope Qwen3-VL-Rerank API 封装
- Web UI：已有 Chunk Inspector 页面（页面列表 + Chunk 详情）

### 1.2 新增需求

用户需要**一个检索效果评估/调试页面**，用于：

1. **调参验证**：调整 Dense/Sparse 权重、TopK、相似度阈值等参数，观察检索结果变化
2. **效果对比**：查看每个结果在 Pipeline 各阶段的分数（Dense / Sparse / RRF / ReRank / Final）
3. **元数据过滤验证**：测试不同过滤条件对检索结果的影响
4. **上下文扩展效果验证**：对比不同扩展策略下的返回内容差异

---

## 2. 核心设计决策

| # | 决策 | 说明 |
|---|---|---|
| 1 | 新增独立 `debug_search()` 方法 | 不改动生产用 `search()`，调试接口返回完整的中间态分数和元数据 |
| 2 | 加权分数融合替代固定 RRF | 支持 `dense_weight` + `sparse_weight = 1.0` 的线性加权融合，同时保留 RRF 作为备选 |
| 3 | 最小相似度在检索后过滤 | Dense 和 Sparse 分别检索后，按 `min_similarity` 过滤低分结果，再进入融合阶段 |
| 4 | 上下文扩展三种模式映射到 `expand_to_level` | "none"→100(只取当前chunk), "parent"→`header_level-1`, "h2"→2 |
| 5 | 元数据过滤扩展为通用 KV | 前端输入任意字段名 + 多选 Value，后端转换为 Milvus filter 表达式 |
| 6 | 前端为独立页面 | 通过 sidebar 导航切换，保持现有 Chunk Inspector 不受影响 |

---

## 3. 数据模型设计

### 3.1 请求模型（`models.py` 新增）

```python
class ContextExpandMode(str, Enum):
    NONE = "none"      # 不扩展，只返回命中 chunk
    PARENT = "parent"  # 扩展到上一级标题
    H2 = "h2"          # 扩展到二级标题

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
```

### 3.2 响应模型（`models.py` 新增）

```python
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
    """Merge dense and sparse hits with weighted linear fusion.

    Each hit's final score = dense_weight * dense_score + sparse_weight * sparse_score.

    Hits present in only one list use their single score * corresponding weight.

    """
    scores: dict[int, float] = {}
    hits_by_id: dict[int, SearchHit] = {}
    for hit in dense_hits:
        scores[hit.id] = scores.get(hit.id, 0.0) + dense_weight * hit.score
        hits_by_id[hit.id] = hit
    for hit in sparse_hits:
        scores[hit.id] = scores.get(hit.id, 0.0) + sparse_weight * hit.score
        hits_by_id[hit.id] = hit
    sorted_ids = sorted(scores, key=scores.get, reverse=True)
    # Attach fused score to the hit for downstream use
    fused_hits = []
    for hid in sorted_ids:
        hit = hits_by_id[hid]
        hit.score = scores[hid]  # overwrite with fused score
        fused_hits.append(hit)
    return fused_hits
```

### 4.2 `SearchService.debug_search()`（`services/search_service.py` 新增）

```python
def debug_search(self, request: DebugSearchRequest) -> DebugSearchResponse:
    """Run the full retrieval pipeline with all intermediate scores exposed.

    Pipeline:

    1. Embed query
    2. Dense search → record dense_score per hit
    3. Sparse search → record sparse_score per hit
    4. Min-similarity filter
    5. Weighted fusion → record rrf_score (fused score)
    6. ReRank → record rerank_score
    7. Context expand (per mode) → record final_score
    8. Truncate

    """
```

**实现要点**：

- 用 `dict[int, StageScores]` 跟踪每个 hit 在各阶段的分数
- Dense/Sparse 检索后，过滤掉 `score < request.min_similarity` 的 hit
- 融合阶段：调用 `weighted_fusion()`，将融合分数写入 `hit.score`
- ReRank 阶段：保留原始 RRF 分数，将 rerank 分数单独记录
- Final 分数：若 rerank 成功则用 rerank_score，否则用 rrf_score
- 上下文扩展：根据 `context_mode` 映射为 `expand_to_level`
  - `NONE` → `expand_to_level = 100`（> max header_level，只取当前 chunk）
  - `PARENT` → `expand_to_level = max(1, hit.metadata.header_level - 1)`
  - `H2` → `expand_to_level = 2`

### 4.3 元数据过滤扩展（`storage/milvus_store.py` 扩展）

当前 `_build_filter_expr()` 只支持 `page_ids`, `header_level`, `edited_after`。

**扩展为通用过滤**：

```python
def _build_filter_expr(self, filters: dict[str, list[str]]) -> str | None:
    """Build Milvus filter expression from generic KV filters.

    Supported fields:

    - page_ids → page_id in [...]
    - header_level → header_level == N 或 header_level in [1,2,3]
    - chunk_type → chunk_type in ['text','table','code','image']
    - page_title → page_title in ['A','B'] 或 page_title == 'A'
    - edited_after → last_edited_time > "..."

    """
```

### 4.4 API 路由（`web/web_server.py` 新增）

```python
@app.post("/api/search/debug")
async def debug_search(req: DebugSearchRequest) -> DebugSearchResponse:
    """Execute a debug search with full parameter control and stage scores."""
```

---

## 5. Web UI 设计

### 5.1 导航集成

在现有 sidebar 中新增导航项：

```
[Chunk Inspector]
[Root 管理]        ← 已规划
[检索调试]          ← 新增
```

### 5.2 页面布局

```
┌─────────────────────────────────────────────────────────────────────────┐
│  RAG Notion KB      [Chunk Inspector] [Root 管理] [检索调试]              │
├────────────────────────┬────────────────────────────────────────────────┤
│  参数面板               │  结果面板                                        │
│                        │                                                 │
│  查询问题               │  ┌─────────────────────────────────────────┐   │
│  ┌──────────────────┐  │  │ 统计: Dense 45 | Sparse 32 | Filter 28   │   │
│  │ 登录流程的验证方式 │  │  │      Fusion 28 | Rerank 28 | 耗时 1.2s   │   │
│  └──────────────────┘  │  └─────────────────────────────────────────┘   │
│  [开始检索]             │                                                 │
│                        │  ┌─ Rank 1  Score: 0.92 ───────────────────┐   │
│  ── 权重 ──             │  │ 来源: 产品文档 / 需求分析 / 登录流程        │   │
│  Dense ████████░░ 0.7  │  │ 类型: text  Level: 3                      │   │
│  Sparse ███░░░░░░░ 0.3 │  │                                          │   │
│                        │  │ Dense: 0.85 | Sparse: 0.72 | RRF: 0.81   │   │
│  ── 检索参数 ──         │  │ ReRank: 0.92 | Final: 0.92               │   │
│  TopK: [10    ]        │  │                                          │   │
│  最小相似度: [0.5 ▓▓▓] │  │ 正文片段...                               │   │
│                        │  └──────────────────────────────────────────┘   │
│  ── 过滤条件 ──         │                                                 │
│  [+ 添加过滤]           │  ┌─ Rank 2  Score: 0.88 ───────────────────┐   │
│  chunk_type: [☑ text]   │  │ ...                                      │   │
│             [☑ table]   │  └──────────────────────────────────────────┘   │
│             [☐ code]    │                                                 │
│             [☐ image]   │                                                 │
│                        │                                                 │
│  ── 上下文扩展 ──       │                                                 │
│  ○ 不扩展               │                                                 │
│  ● 扩展到上一级标题      │                                                 │
│  ○ 扩展到二级标题        │                                                 │
│                        │                                                 │
└────────────────────────┴────────────────────────────────────────────────┘
```

### 5.3 前端交互细节

**权重滑动杆**：

- 两个 range input（dense / sparse），总和锁定为 1.0
- 拖动一个时，另一个自动反向调整
- 显示精确数值（两位小数）

**元数据过滤**：

- 点击"+ 添加过滤"弹出选择字段名（下拉：chunk_type / header_level / page_title / page_ids）
- 选择字段后，Value 区域显示多选 checkbox（选项从已同步数据中动态获取）
- 可添加多个过滤条件（AND 关系）
- 可删除单个过滤条件

**结果卡片**：

- 顶部：Rank #、Final Score（大字号高亮）
- 元数据行：page_title → header_path、chunk_type badge、header_level
- 分数条：横向展示 5 个分数，用不同颜色区分
  - Dense（蓝色）、Sparse（绿色）、RRF（紫色）、ReRank（橙色）、Final（红色）
- 内容区：`expanded_text`（若扩展了则标注"已扩展"）
- 折叠/展开：长文本默认折叠，点击展开

**分数可视化**：

```
分数构成:  [Dense ████████░░ 0.85]  [Sparse ██████░░░░ 0.72]
           [RRF   ███████░░░ 0.81]  [ReRank ████████▓░ 0.92]
```

---

## 6. 实现步骤

### Phase 1：后端数据模型 + 加权融合（1.5h）

1. **修改 `models.py`**：
   - 新增 `ContextExpandMode` Enum
   - 新增 `DebugSearchRequest`、`StageScores`、`DebugSearchHit`、`DebugSearchResponse`
2. **修改 `retrieval/hybrid_search.py`**：
   - 新增 `weighted_fusion()` 函数
   - 保留原有 `reciprocal_rank_fusion()` 不变
3. **测试**：
   - `tests/unit/test_hybrid_search.py`：验证加权融合公式正确性
   - 验证 dense_weight=1.0, sparse_weight=0.0 时结果等同于纯 dense

### Phase 2：SearchService.debug_search()（2h）

1. **修改 `services/search_service.py`**：
   - 新增 `debug_search(self, request)` 方法
   - 实现 Pipeline 各阶段分数追踪
   - 实现 `context_mode` → `expand_to_level` 映射
   - 最小相似度过滤逻辑
2. **修改 `storage/milvus_store.py`**：
   - 扩展 `_build_filter_expr()` 支持通用 KV 过滤
   - 新增字段支持：`chunk_type`, `page_title`
3. **测试**：
   - Mock MilvusStore 和 RerankerService，验证 debug_search 返回正确的 StageScores
   - 验证 min_similarity 过滤生效
   - 验证 context_mode 映射正确

### Phase 3：Web API（0.5h）

1. **修改 `web/web_server.py`**：
   - 新增 `POST /api/search/debug` 路由
   - 错误处理（400 参数校验错误、500 检索失败）
2. **测试**：
   - `TestClient` 验证 API 请求/响应格式

### Phase 4：Web 前端（3h）

1. **修改 `web/static/index.html`**：
   - Sidebar 新增"检索调试"导航项
   - 新增检索调试视图（参数面板 + 结果面板）
   - 权重滑动杆组件（联动逻辑）
   - 元数据过滤动态添加/删除组件
   - 上下文扩展单选组件
   - 结果卡片组件（含分数可视化）
   - 查询按钮 + Loading 状态
2. **前端逻辑**：
   - `fetch('/api/search/debug', {method:'POST', body:JSON.stringify(params)})`
   - 响应后渲染统计栏 + 结果列表
   - 错误提示（toast）

### Phase 5：集成验收（1h）

1. **手动测试流程**：
   - 输入查询 → 调整 Dense/Sparse 权重 → 观察结果排序变化
   - 调整 min_similarity → 验证低分结果被过滤
   - 添加 chunk_type 过滤 → 验证结果只含指定类型
   - 切换上下文扩展模式 → 验证 expanded_text 长度变化
   - 验证分数可视化显示正确
2. **边界测试**：
   - dense_weight=1.0, sparse_weight=0.0 → 结果按 dense score 排序
   - min_similarity=0.99 → 几乎无结果
   - 空查询 → 返回空结果（不报错）

---

## 7. API 详细契约

### `POST /api/search/debug`

**请求示例**：

```json
{
  "query": "登录流程的验证方式",
  "dense_weight": 0.7,
  "sparse_weight": 0.3,
  "top_k": 10,
  "min_similarity": 0.5,
  "rerank_model": "qwen3-vl-rerank",
  "filters": {
    "chunk_type": ["text", "table"],
    "header_level": ["2", "3"]
  },
  "context_mode": "parent",
  "max_tokens": 4000
}
```

**响应示例**：

```json
{
  "query": "登录流程的验证方式",
  "params": { /* 回显请求参数 */ },
  "total_dense": 45,
  "total_sparse": 32,
  "total_after_filter": 28,
  "total_after_fusion": 28,
  "total_after_rerank": 28,
  "latency_ms": 1250,
  "error": null,
  "results": [
    {
      "rank": 1,
      "page_id": "a1b2c3d4-...",
      "page_title": "产品文档",
      "page_url": "https://notion.so/...",
      "chunk_index": 3,
      "chunk_type": "text",
      "header_path": "# 产品文档 > ## 需求分析 > ### 登录流程",
      "header_level": 3,
      "chunk_text": "用户登录需要输入手机号和验证码...",
      "expanded_text": "## 需求分析\n\n### 登录流程\n用户登录需要输入手机号和验证码...\n\n### 注册流程\n...",
      "scores": {
        "dense_score": 0.8521,
        "sparse_score": 0.7234,
        "rrf_score": 0.8135,
        "rerank_score": 0.9210,
        "final_score": 0.9210
      },
      "matched_snippet": "用户登录需要输入手机号和验证码"
    }
  ]
}
```

---

## 8. 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/rag_notion_kb/models.py` | 修改 | 新增 `ContextExpandMode`, `DebugSearchRequest`, `StageScores`, `DebugSearchHit`, `DebugSearchResponse` |
| `src/rag_notion_kb/retrieval/hybrid_search.py` | 修改 | 新增 `weighted_fusion()` 函数 |
| `src/rag_notion_kb/services/search_service.py` | 修改 | 新增 `debug_search()` 方法 |
| `src/rag_notion_kb/storage/milvus_store.py` | 修改 | 扩展 `_build_filter_expr()` 支持通用 KV 过滤 |
| `src/rag_notion_kb/web/web_server.py` | 修改 | 新增 `POST /api/search/debug` 路由 |
| `src/rag_notion_kb/web/static/index.html` | 修改 | 新增检索调试页面（参数面板、结果卡片、分数可视化） |
| `tests/unit/test_hybrid_search.py` | 修改 | 新增加权融合测试 |
| `tests/unit/test_search_service.py` | **新增** | `debug_search` 全流程测试 |
| `tests/e2e/test_web_search_debug.py` | **新增** | Web UI 检索调试 E2E 测试 |

---

## 9. 风险与应对

| 风险 | 影响 | 可能性 | 应对策略 |
|------|------|--------|----------|
| 加权融合后分数不可比 | Dense 和 Sparse 分数尺度不同，线性加权可能不公平 | 中 | 文档中说明此为调试工具，非生产最优策略；未来可支持分数归一化 |
| ReRank API 耗时导致页面卡顿 | 调试页面每次查询都调 ReRank，延迟高 | 高 | 前端显示 Loading 动画；支持"跳过 ReRank"开关；设置 30s 超时 |
| 元数据过滤字段名拼写错误 | 用户输入不存在的字段名导致 Milvus 报错 | 中 | 前端下拉选择字段名（限制可选范围），禁止自由输入 |
| 大结果集渲染性能差 | top_k=100 时前端卡顿 | 低 | 默认 top_k=10；结果列表使用虚拟滚动（如需要） |

---

## 10. 验收验证

### 10.1 功能验收清单

| # | 验收项 | 验证方法 |
|---|--------|----------|
| 1 | Dense/Sparse 权重滑动杆联动正确 | 拖动 Dense 到 0.8，Sparse 自动变为 0.2 |
| 2 | 权重调整影响结果排序 | dense_weight=1.0 与 0.0 的结果 Top-1 不同 |
| 3 | 最小相似度过滤生效 | min_similarity=0.9 时结果数明显减少 |
| 4 | 元数据过滤生效 | 只选 chunk_type=table 时结果均为表格 |
| 5 | 上下文扩展三种模式差异 | 同一条结果，三种模式的 expanded_text 长度不同 |
| 6 | 分数可视化显示正确 | 每个结果卡片的 5 个分数与 API 返回一致 |
| 7 | 统计栏数字正确 | total_dense / total_sparse / total_after_filter 之和逻辑正确 |
| 8 | 空查询不报错 | 输入空字符串，返回空结果而非 500 |
| 9 | ReRank 失败降级 | Mock ReRank 503，结果仍返回（使用 RRF 分数作为 Final） |

### 10.2 性能目标

| 指标 | 目标值 |
|------|--------|
| 页面加载 | < 1s |
| 单次检索总耗时（含 ReRank） | < 3s |
| 单次检索总耗时（不含 ReRank） | < 1s |
| 结果渲染（10 条） | < 200ms |

---

## 11. 修订历史

| 版本 | 日期 | 作者 | 变更内容 |
|------|------|------|----------|
| v1.0 | 2026-08-12 | Claude | 初始版本，设计混合检索效果评估页面的完整实施计划，含后端扩展、API 契约、前端 UI、验收标准 |

---

*本文档为混合检索效果评估页面的权威实施计划。该页面是调试工具，不影响生产检索流程。*

