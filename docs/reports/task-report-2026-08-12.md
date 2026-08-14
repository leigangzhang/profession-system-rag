# 任务执行报告

**日期**: 2026-08-12  
**项目**: RAG Notion 知识库系统  
**分支**: 主分支  

---

## 一、概览

| 任务 | 状态 | 关键文件 |
|------|------|----------|
| Chunk 预览页修复 | ✅ 完成 | `index.html`, `chunking.py` |
| 容器页面过滤 | ✅ 完成 | `notion/client.py`, `sync_service.py`, `models.py` |
| Circle-1 系统流程调整 | ✅ 完成 | 10 个文件变更，2 个新增文件 |

**测试结果**: 123 passed (75 单元 + 28 e2e + 20 集成)

---

## 二、Chunk 预览页修复

### 问题
- Web UI chunk 预览页列表缩进错乱：连续列表后的文本段落被误解析为列表项
- 嵌套列表 CSS 缺失
- 图片 URL 被编码显示

### 修复

| 文件 | 变更 |
|------|------|
| `web/static/index.html` | `renderChunkMarkdown()` 新增列表后空行插入逻辑；`.chunk-card-body li > ul/ol` CSS；图片预览区展示 alt 替代 URL |
| `processing/chunking.py` | 图片标签调整为保护块（与代码/表格同级）；保护块跳过 `_enforce_chunk_size` 截断；`_merge_image_chunks` 丢弃无法合并的纯图片 chunk |

---

## 三、容器页面过滤

### 问题
Notion 中纯子页面引用页面（如"0504-数据治理"）无独立检索价值，但 `notion_to_md` 会将子页面内容 inline，导致父页面产生重复 chunk。

### 修复路径

1. **尝试1**：在 `_sync_page()` 中新增 `page_has_own_blocks()` API 调用 → 失败（增加额外 API 调用，且 Notion 页面间空段落导致误判）
2. **调试**：编写 `debug_blocks.py` 脚本，发现 API 返回的空 `paragraph` block 被误判为"有正文"
3. **最终方案**：在 `enumerate_pages()` 枚举阶段复用已有的 blocks API 调用，通过 `out_has_own` 参数判断容器页面，空段落由 `_is_empty_paragraph()` 过滤

### 最终变更

| 文件 | 变更 |
|------|------|
| `models.py` | `PageMetadata.is_container: bool = False` |
| `notion/client.py` | `_is_empty_paragraph()` 静态方法；`_list_child_page_blocks()` 新增 `out_has_own` 参数；`enumerate_pages()` 标记容器页 |
| `services/sync_service.py` | 容器页检查前置到 `_sync_page()` 首行；跳过时清理 Milvus 旧数据 |
| `web/web_server.py` | 列表页过滤 `skipped` 状态页面 |
| `debug_blocks.py` | 调试脚本（临时） |
| `tests/integration/test_notion_client.py` | 3 个容器页检测测试（含空段落场景） |

---

## 四、Circle-1：向量开关 + 后台异步向量化

### 架构变更

```
原架构: sync() → 拉取 → 分块 → Embedding → Milvus（全量同步）

新架构:
  sync_fetch()   → 拉取页面元数据 + Markdown → SQLite
  sync_vectorize() → 分块 → Embedding → Milvus（按需触发）
  VectorizeWorker → 后台轮询 pending 页面，Semaphore 并发控制
```

### Phase 1: 数据模型 & Schema

| 文件 | 变更 |
|------|------|
| `models.py` | `PageSyncState` 新增 `vector_enabled`, `vector_status`, `vector_error_message`, `vector_progress`；`status` 新增 `"fetched"`；新增 `VectorizeResult` 模型 |
| `storage/sync_state.py` | DDL 新增 4 列 + CHECK 约束；`_migrate_schema()` 自动迁移已有数据库；`get_pending_vectorize()` 查询方法；`update_vector_status()` 局部更新方法 |

**迁移逻辑**：
- 新安装自动包含全部列
- 已有数据库通过 `ALTER TABLE ADD COLUMN` 追加缺失列
- 已有 `synced` 页面默认 `vector_status='indexed'`

### Phase 2: SyncService 拆分

| 文件 | 变更 |
|------|------|
| `services/sync_service.py` | `sync_fetch()` 只拉取 markdown 写入 SQLite；`sync_vectorize()` 读取 SQLite 执行分块→embed→Milvus；`sync()` 向后兼容组合方法 |
| `cli.py` | 解包 `sync()` 返回的 `(SyncResult, VectorizeResult | None)` |
| `web/web_server.py` | 解包同步（同上） |

### Phase 3: VectorizeWorker

| 文件 | 变更 |
|------|------|
| `services/vectorize_worker.py` | **新增**：`asyncio.Semaphore` 限并发；`start/stop/enqueue` 生命周期；轮询 `get_pending_vectorize()`；`asyncio.to_thread()` 避免阻塞事件循环 |
| `tests/unit/test_vectorize_worker.py` | **新增**：7 个测试（启动停止、并发上限、失败隔离、入队、状态流转） |

### Phase 4: Web UI

| 文件 | 变更 |
|------|------|
| `models.py` | `PageSummary` 新增 `vector_enabled`, `vector_status`, `vector_progress` |
| `web/web_server.py` | 启动/停止 Worker；`/api/pages/{id}/vector-toggle` 端点；`/api/pages/{id}/vectorize` 端点；`/api/worker/status` 端点 |
| `web/static/index.html` | Toggle Switch 组件 + CSS；向量状态标签（pending/indexing/indexed/failed）；进度条；3 秒轮询刷新 |

### Phase 5: CLI

| 文件 | 变更 |
|------|------|
| `cli.py` | `sync` 命令新增 `--vectorize` 参数；`web` 命令新增 `--max-concurrent` 参数；新架构兼容旧命令格式 |

### Phase 6: 集成验收

| 测试范围 | 结果 |
|----------|------|
| 单元测试 | 75 passed |
| E2E 测试 | 28 passed |
| 集成测试 (web + notion) | 20 passed |
| **合计** | **123 passed** |

---

## 五、测试覆盖

```
tests/unit/test_chunking.py                 5 passed
tests/unit/test_config.py                   3 passed
tests/unit/test_context_expand.py           5 passed
tests/unit/test_exceptions.py               2 passed
tests/unit/test_hybrid_search.py            5 passed
tests/unit/test_images.py                   5 passed
tests/unit/test_logging_setup.py            4 passed
tests/unit/test_milvus_filters.py           5 passed
tests/unit/test_milvus_schema.py            5 passed
tests/unit/test_milvus_store.py             7 passed
tests/unit/test_models.py                   7 passed
tests/unit/test_sync_state.py               7 passed
tests/unit/test_truncate.py                 8 passed
tests/unit/test_vectorize_worker.py         7 passed  ← 新增
tests/e2e/test_cli.py                      13 passed
tests/e2e/test_web_server.py               15 passed
tests/integration/test_notion_client.py    11 passed
tests/integration/test_web_server.py        9 passed
─────────────────────────────────────────────────
                                           123 passed
```

---

## 六、关键决策记录

| # | 决策 | 理由 |
|---|------|------|
| 1 | 容器判断在枚举阶段完成 | 复用已有 API 调用，零额外开销；避免 Notion 页面间空段落误判 |
| 2 | 迁移时已有 synced 页面默认 `vector_status='indexed'` | 数据已在 Milvus，无需重复向量化 |
| 3 | `sync_vectorize()` 通过异步 + 线程池调用 `_vectorize_page()` | 保持现有同步管道不变，避免全面改异步 |
| 4 | `_merge_image_chunks` 合并不了就丢弃 | 图片已单独通过 ImageDoc 做多模态 embedding |
| 5 | 容器页跳过时清理 Milvus 旧数据 | 避免之前 sync 留下脏数据 |

---

## 七、已知限制

- Milvus Lite 集成测试因沙箱 `127.0.0.1` 绑定权限跳过（5 个 sync_service 集成测试）
- Web UI `on_event` 使用已弃用 API（FastAPI 建议迁移到 lifespan），不影响功能
- 需要真实 Notion workspace + DashScope API key 完成完整的端到端验收
