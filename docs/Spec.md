# RAG Notion 知识库系统 — 需求规格说明书

> **版本**：v2.0.0  
> **日期**：2026-08-14  
> **状态**：与当前代码实现对齐。

## 1. 项目概述

### 1.1 背景

用户需要把 Notion 中的长文档知识库转成可检索的本地 RAG 数据源，供 Web UI、CLI 与 MCP Agent 使用。系统应保留 Markdown 原文，支持文本与图片的多模态向量化，并提供可解释、可调参的混合检索。

### 1.2 目标

- 递归拉取 Notion 页面并保存原始 Markdown 与轻量元数据。
- 将“拉取”与“向量化”解耦，支持按页面开关、后台并发向量化。
- 支持多 Notion Root 管理、页面树浏览和选择性批量同步。
- 提供 dense + BM25 混合检索、ReRank、上下文扩展和 token 截断。
- 记录 Debug、MCP、CLI 检索历史，支持 Web 调参与回放。
- 用本地 SQLite + Milvus Lite 运行，不依赖远程数据库。

### 1.3 非目标

- 不实现多人账号、权限或组织级隔离。
- 不实现 Notion 双向写入。
- 不替代 Notion 的实时在线搜索体验。
- 不提供云托管服务。

## 2. 术语

| 术语 | 定义 |
|------|------|
| Root | Notion 递归同步的入口页面 |
| Page | Notion 页面，含标题、URL、父级、最后编辑时间 |
| Chunk | Markdown 按标题与长度规则分出的检索单元 |
| ImageDoc | 从 Markdown 提取出的独立图片 document |
| Dense | Qwen3-VL-Embedding 生成的浮点向量 |
| Sparse | Milvus BM25 生成的稀疏向量 |
| Vector toggle | 页面级别的向量化开关 |

## 3. 功能需求

### FR-1：Notion 数据拉取

系统必须递归枚举 Root 下的页面，读取轻量 metadata 和 Markdown，并将结果写入 SQLite。需处理容器页、仅链接页、空内容页、Notion 读取失败和递归深度限制。

### FR-2：图片提取与本地缓存

系统必须提取 Markdown 图片，生成上下文文本，下载到 `~/.rag_kb/images/{page_id}/`，并把即将过期的远端 URL 重写为本地 `/images/...` 路径。图片可作为独立 ImageDoc 向量化。

### FR-3：文档分块与 Metadata

分块应保留标题层级、表格和代码块。每个 Chunk/ImageDoc 必须携带 `page_id`、`page_title`、`page_url`、`header_path`、`header_level`、`last_edited_time`、`chunk_index`、`chunk_type` 等 metadata。

### FR-4：两阶段同步

`sync_fetch` 必须只执行拉取和 SQLite 写入；`sync_vectorize` 必须执行分块、图片恢复、Embedding 与 Milvus 写入。完整 `sync()` 可组合两阶段，CLI `--vectorize` 可要求一次完成。

### FR-5：页面向量开关与后台 Worker

每个页面有 `vector_enabled`、`vector_status`、`vector_progress`、`vector_stage`、`vector_error_message`、`last_vectorized_time` 等状态。后台 `VectorizeWorker` 必须轮询 enabled + pending 页面，限制并发，防重复处理，并支持启动时清理 stale indexing。

### FR-6：Root 管理与选择性同步

系统必须持久化多个 Root，提供添加、删除、读取页面树、刷新树和按选中 page_ids 发起批量同步。删除 Root 时按当前实现同时删除该 Root 缓存的页面状态和 Milvus 数据。

### FR-7：同步进度

批量同步必须返回 `sync_id`，并可通过 Web API 查询运行状态、完成页数、当前页面、最终 `SyncResult` 或错误。进度状态需可跨进程恢复。

### FR-8：混合检索

检索 Pipeline 必须包括：

1. Query Embedding。
2. Dense COSINE ANN 检索。
3. BM25 sparse 检索。
4. 支持 `page_ids`、`header_level`、`chunk_type`、`page_title`、`edited_after` 过滤。
5. Dense/Sparse 权重融合。
6. 可选 ReRank。
7. 上下文扩展。
8. Token 截断。

### FR-9：检索调试与历史

`debug_search()` 必须暴露每阶段数量和 dense/sparse/fusion/rerank/final 分数。Debug、MCP、CLI 的成功检索应异步写入 `search_history`，包含参数、结果摘要和可选的完整 snapshot。历史页面应支持统计、过滤、删除、清空和回放。

### FR-10：Web UI

Web UI 必须是 Vue 3 SPA，提供 Root 管理、页面列表、Chunk 详情、检索调试和检索历史页面。API 必须与 `api.js` 封装一致。

### FR-11：MCP Server

stdio MCP Server 必须暴露 `rag_search`、`rag_sync`、`rag_stats`、`rag_page_detail`。`rag_search` 和 `rag_page_detail`、`rag_stats` 应标记 read-only；`rag_search` 还应标记 open-world。

### FR-12：CLI

CLI 必须提供 `sync`、`status`、`search`、`inspect`、`web`、`serve`、`config` 七个命令。

## 4. 非功能需求

| 类别 | 要求 |
|------|------|
| 可用性 | 单页失败不阻断批量同步；ReRank 失败可回退 fusion；历史写入失败不影响检索 |
| 数据安全 | 不修改 Notion 数据；本地删除范围限定在该 Root/页面对应数据 |
| 可维护性 | 代码模块边界清晰，`AppContext` 统一资源生命周期 |
| 可观测性 | JSON 日志覆盖拉取、分块、Embedding、写入、检索、ReRank 与截断 |
| 本地运行 | 默认数据目录为 `~/.rag_kb`，不强制外部 Milvus |
| 性能 | 页面树使用 SQLite 缓存；单页 embedding 统计避免 N+1 查询 |

## 5. 数据与接口摘要

完整数据模型、配置、SQLite DDL、Milvus Schema、Web API、MCP 参数和 CLI 参数以 `Backend.md` 为准。用户操作说明见 `Manual.md`。

## 6. 当前实现范围

以下能力已在当前代码中实现：

- 两阶段同步与 hash 跳过优化。
- 页面级向量开关和后台并发 Worker。
- 多 Root 管理、页面树缓存与批量同步。
- Dense + BM25 混合检索和可调参数 debug search。
- Debug/MCP/CLI 检索历史。
- Vue 3 + Arco SPA。

以下能力不在当前代码中：

- CI 自动执行 `scripts/consistency_check.py`（脚本已提供，可后续接入）。
- 浏览器自动化前端测试。

## 7. 风险

| 风险 | 缓解 |
|------|------|
| Notion API 限流或失败 | 重试 3 次、单页隔离、failed 状态可重试 |
| 图片 URL 过期 | 拉取阶段下载并缓存；向量化失败时从 Notion 重取 |
| Milvus Lite 页面替换中断 | Worker 重启时重置 stale indexing；插入失败恢复旧行 |
| SQLite 历史文件损坏 | 记录 `sync_state.db.corrupted`；文档与日志提示备份/重建路径 |
| 外部 CDN 不可用 | Markdown/高亮资源本地化；Vue/Arco 仍依赖 CDN |

## 8. 修订历史

| 版本 | 日期 | 变更内容 |
|------|------|----------|
| v2.0.0 | 2026-08-14 | 纳入两阶段同步、Root 管理、选择性同步、检索调试、检索历史、Vue SPA |
