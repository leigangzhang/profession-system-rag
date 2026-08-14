# RAG Notion 知识库系统 — 项目验收标准

> **版本**：v2.0.0  
> **日期**：2026-08-14  
> **原则**：验收项对应当前代码实现和现有自动化测试。

## 1. 总览

| Feature | 验收项 | 测试证据 |
|---------|-------:|----------|
| F1 Notion 递归拉取与 Markdown | 4 | `tests/integration/test_notion_client.py` |
| F2 Markdown 分块 | 4 | `tests/unit/test_chunking.py` |
| F3 图片提取与缓存 | 4 | `tests/unit/test_images.py`, `tests/unit/test_image_cache.py` |
| F4 Qwen Embedding | 4 | `tests/integration/test_embedding.py` |
| F5 Milvus 存储 | 4 | `tests/unit/test_milvus_schema.py`, `test_milvus_store.py`, `test_milvus_filters.py`, `tests/integration/test_milvus_store_local.py` |
| F6 混合检索 | 4 | `tests/unit/test_hybrid_search.py` |
| F7 ReRank | 3 | `tests/integration/test_reranker.py` |
| F8 上下文扩展 | 4 | `tests/unit/test_context_expand.py` |
| F9 Token 截断 | 3 | `tests/unit/test_truncate.py` |
| F10 MCP Server | 4 | `tests/e2e/test_mcp_server.py` |
| F11 CLI | 4 | `tests/e2e/test_cli.py` |
| F12 Web 基础 API | 4 | `tests/integration/test_web_server.py`, `tests/e2e/test_web_server.py` |
| F13 两阶段同步与后台向量化 | 5 | `tests/unit/test_sync_service_vectorize.py`, `test_vectorize_worker.py`, `test_sync_state.py` |
| F14 Root 管理与选择性同步 | 4 | `tests/unit/test_root_store.py`, `tests/integration/test_web_server.py` |
| F15 检索调试与历史 | 5 | `tests/unit/test_search_service_debug.py`, `test_search_history_store.py`, `tests/integration/test_web_search_debug.py` |
| F16 Vue SPA | 4 | `tests/e2e/test_web_server.py` + `scripts/consistency_check.py` 静态文件清单 |

## 2. F1 Notion 递归拉取与 Markdown

### AC-1-01 Root 递归拉取

`NotionClient.enumerate_pages()` 能递归遍历 Root 下页面，返回带父级关系的 `PageMetadata`。

### AC-1-02 Markdown 格式保留

`get_page_markdown()` 返回 Notion 导出的 Markdown 原文。

### AC-1-03 Metadata 完整性

页面 metadata 至少包含 `page_id`、`title`、`url`、`last_edited_time`、`parent_id`，容器页有 `is_container`。

### AC-1-04 容错与跳过

单个页面读取失败应记录 failed 页面 ID 并继续；容器页和无可检索内容页应记录为 skipped。

## 3. F2 Markdown 分块

### AC-2-01 标题层级正确切分

分块遵循 `header_levels`，header path 正确。

### AC-2-02 表格块整体保留

`preserve_tables=True` 时表格不被拆散。

### AC-2-03 代码块整体保留

`preserve_code_blocks=True` 时代码块不被拆散。

### AC-2-04 图片标签保留

文本 chunk 中的图片 Markdown 标签应保留，并允许 ImageDoc 独立索引。

## 4. F3 图片提取与缓存

### AC-3-01 图片独立 document

每个 Markdown 图片生成一个 `ImageDoc`，包含 `image_url`、`alt`、`context_text` 与 metadata。

### AC-3-02 图片上下文窗口

`ImageExtractor` 使用 `image_context_window` 与 `image_context_max_chars` 控制上下文。

### AC-3-03 本地缓存与 URL 重写

图片下载到 `~/.rag_kb/images/{page_id}/`，Markdown 中图片 URL 重写为 `/images/{page_id}/...`。

### AC-3-04 无图片页面正常处理

无图片页面可正常分块与向量化。

## 5. F4 Qwen Embedding

### AC-4-01 文本 chunk 向量化

`EmbeddingService.embed()` 返回与配置维度一致的向量。

### AC-4-02 图片 document 向量化

本地图片以 base64 传入多模态 Embedding，缺失文件回退为文本安全兜底。

### AC-4-03 Batch 与重试

Embedding 批量大小和最大重试次数来自 `EmbeddingConfig`。

### AC-4-04 零向量识别

任一输入返回全零向量时，页面标记 failed 并保留旧向量，不写入不完整数据。

## 6. F5 Milvus 存储

### AC-5-01 Schema 正确性

`rag_kb_chunks` 包含 `id`、`chunk_text`、`dense_vector`、`sparse_vector`、页面 metadata 与 chunk 字段，dense 维度为 2048。

### AC-5-02 文本与图片写入

页面替换写入文本 chunk 和图片 document，并返回写入行数。

### AC-5-03 页面更新时旧数据清理

更新前删除同 page_id 旧行；插入失败时恢复旧行。

### AC-5-04 Dense/BM25 可用

Dense 使用 COSINE，sparse 使用 BM25，`search_dense()` 与 `search_sparse()` 均可执行过滤。

## 7. F6 混合检索

### AC-6-01 Dense 检索

Dense 查询按 ANN 返回 `SearchHit`。

### AC-6-02 BM25 检索

sparse 查询由 BM25 function 返回结果。

### AC-6-03 权重融合与去重

`weighted_fusion()` 合并 dense/sparse，并归一化权重。

### AC-6-04 过滤条件

支持 `page_ids`、`header_level`、`chunk_type`、`page_title`、`edited_after`，未知字段应报错。

## 8. F7 ReRank

### AC-7-01 改变结果顺序

ReRank 可用时按模型分数重排 fusion 结果。

### AC-7-02 保留分数

`debug_search()` 返回 rerank 分数和 final 分数。

### AC-7-03 失败降级

ReRank 调用失败时回退到 fusion 顺序，不使检索失败。

## 9. F8 上下文扩展

### AC-8-01 H2 模式

`context_mode="h2"` 时向上扩展到二级标题区间。

### AC-8-02 PARENT 模式

`parent` 模式向上扩展到当前标题的父级。

### AC-8-03 NONE 模式

`none` 只返回当前 chunk。

### AC-8-04 兼容旧参数

旧 `expand_to_level` 参数仍可映射到新 context mode。

## 10. F9 Token 截断

### AC-9-01 超长截断

扩展文本按 `max_tokens` 截断。

### AC-9-02 未超限完整返回

未超过上限时保持内容完整。

### AC-9-03 截断策略

截断逻辑由 `TokenTruncator` 封装，使用 tiktoken 估算。

## 11. F10 MCP Server

### AC-10-01 stdio 启动

`rag-kb serve` 启动 `FastMCP` stdio server。

### AC-10-02 rag_search

`rag_search` 支持 `query`、`page_size`、`max_highlight_length`、`search_mode`、`filters`、`page_id`、`context_mode`、`max_tokens`、`rerank`、`min_similarity`、`rerank_model`。

### AC-10-03 rag_sync

`rag_sync` 支持 `root` 与 `full`，返回同步计数。

### AC-10-04 rag_stats / rag_page_detail

`rag_stats` 返回统计；`rag_page_detail` 返回按 chunk_index 拼接的页面全文。

## 12. F11 CLI

### AC-11-01 sync

`rag-kb sync` 支持 `--root`、`--full`、`--vectorize`。

### AC-11-02 status/search/inspect

`status` 展示统计；`search` 检索并写历史；`inspect` 查看单页 chunk 与 embedding 状态。

### AC-11-03 web/serve/config

`web` 启动 FastAPI；`serve` 启动 MCP；`config` 输出脱敏配置。

### AC-11-04 配置优先级

命令行 Root > SQLite Root 配置 > `notion.root_page_ids`。

## 13. F12 Web 基础 API

### AC-12-01 健康检查

`GET /health` 返回 `status: ok` 和 Worker 状态。

### AC-12-02 页面列表与详情

`GET /api/pages` 与 `GET /api/pages/{page_id}` 返回 `Backend.md` 第 9 节定义的模型。

### AC-12-03 单页同步

`POST /api/pages/{page_id}/sync` 重拉单页。

### AC-12-04 静态资源与 SPA 回退

`/static`、`/images` 可用，未知前端路径返回 SPA 入口。

## 14. F13 两阶段同步与后台向量化

### AC-13-01 sync_fetch 不写 Milvus

`sync_fetch` 只更新 SQLite 原始内容与状态。

### AC-13-02 开关状态保留

页面更新后 `vector_enabled` 保留，已开启页面回到 `pending`。

### AC-13-03 Worker 并发与防重

Worker 使用 Semaphore，并发页 ID 集合防止重复处理。

### AC-13-04 stale indexing 恢复

Worker 启动时把异常遗留的 `indexing` 页面重置为 `pending`。

### AC-13-05 hash 跳过

`content_hash`、`chunking_hash`、`embedding_hash` 和完整向量校验同时满足时才跳过重建。

## 15. F14 Root 管理与选择性同步

### AC-14-01 Root 持久化

`notion_roots` 表支持增删查与 active ID 列表。

### AC-14-02 页面树缓存

`page_tree_cache` 保存扁平页面信息，API 返回树结构并附同步状态。

### AC-14-03 选择性批量同步

`POST /api/sync` 接收 `page_ids` 与 `force_full`，后台执行 `sync_fetch`。

### AC-14-04 Root 删除清理

删除 Root 同时清理树缓存、页面状态和 Milvus 页面数据。

## 16. F15 检索调试与历史

### AC-15-01 全阶段分数

`DebugSearchResponse` 暴露 dense、sparse、fusion、rerank、final 分数与各阶段数量。

### AC-15-02 历史来源

Debug、MCP、CLI 成功检索均写入 `search_history`，来源分别为 `debug`、`mcp`、`cli`。

### AC-15-03 历史统计

历史 API 返回成功率、零结果率、top score 分位数、平均延迟与质量分。

### AC-15-04 历史回放

可按 `history_id` 获取参数与 snapshot，并在检索调试页恢复。

### AC-15-05 保留策略

最多保留 500 条、30 天；历史写入使用后台队列，不阻塞检索。

## 17. F16 Vue SPA

### AC-16-01 组件化

前端由 `app.js`、`api.js`、7 个通用组件和 5 个视图组件构成，不再是单文件页面逻辑。

### AC-16-02 页面完整

路由覆盖 `/roots`、`/pages`、`/page/:page_id`、`/search-debug`、`/search-history`。

### AC-16-03 API 统一封装

所有后端调用经过 `api.js`，请求路径与 FastAPI 路由一致。

### AC-16-04 文件清单校验

`scripts/consistency_check.py` 能提取前端文件清单并纳入一致性报告。

## 18. 文档治理验收

| # | 验收项 | 通过标准 |
|---|--------|----------|
| D-1 | 文档重组 | 根目录活跃文档不超过 7 份，`archived/` 与 `dev/` 存在 |
| D-2 | 一致性脚本 | `python scripts/consistency_check.py --fail-on-mismatch` 可运行且返回 0 |
| D-3 | 模型与 Schema | 报告中的 Models、Config、SQLite、API、CLI、MCP 差异为 0 |
| D-4 | 版本统一 | 所有活跃文档 major 版本一致，当前为 v2.x.x |
| D-5 | 幽灵文档清理 | 已实现 Plan 归档；Plan 中不存在的接口不被描述为已实现 |

## 19. 修订历史

| 版本 | 日期 | 变更内容 |
|------|------|----------|
| v2.0.0 | 2026-08-14 | 与当前代码、测试文件和一致性报告对齐 |
