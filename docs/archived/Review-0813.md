# RAG Notion KB 交互与 Embedding 流程 Review

> 生成时间：2026-08-13
> 范围：项目整体交互 + Embedding 流程（不含检索）
> 状态：Review 完成，高/中严重度问题已落地；剩余为 tracing 与限流/熔断

---

## 一、Review 发现清单

### 高严重度

#### 1. 资源生命周期管理缺失：连接/客户端泄漏
- **位置**：CLI 各命令、`MCPServer`、`web_server` 的启动/关闭路径
- **问题**：`EmbeddingService`（httpx Client）、`SyncStateStore`/`NotionRootStore`/`NotionTreeCache`（SQLite 连接）、`MilvusStore`（MilvusClient）创建后没有统一关闭。`EmbeddingService.close()` 存在但从未被调用；CLI 短命令没有 `try/finally`；Web 只在 shutdown 关闭 `store`。
- **风险**：FD 泄漏、Milvus addr 文件残留、SQLite WAL 不清理、长期运行 Web/MCP 时连接堆积。
- **修复状态**：✅ 已引入 `AppContext`，统一持有资源并提供 `close()` / `aclose()`；CLI/MCP/Web 已接入。`NotionClient.close()` 已添加。`RerankerService.close()` 在 httpx 改造中已添加。
- **剩余问题**：无。全量测试已覆盖 CLI/MCP/Web 生命周期路径。

#### 2. VectorizeWorker 可能并发处理同一页面
- **位置**：`src/rag_notion_kb/services/vectorize_worker.py:88-99`
- **问题**：Worker 先 `get_pending_vectorize()` 取到 pending 页面，再 `acquire` + `create_task`；状态改为 `indexing` 是在子任务内部。两次 poll 之间可能重复派发同一页面。
- **风险**：重复嵌入、浪费 DashScope/Milvus 调用、状态竞争。
- **修复状态**：✅ 已通过 SQLite 原子 `claim_vectorize` 在派发前领取任务，并增加重复领取回归测试。

#### 3. SQLite 状态与 Milvus 向量之间无事务
- **位置**：`src/rag_notion_kb/services/sync_service.py:212-214`, `:284-380`
- **问题**：`_fetch_page` 先 `delete_by_page_id` 再写 SQLite；`_vectorize_page` 先删再 `insert` 最后改状态。任一步失败都会导致两边不一致。
- **风险**：状态显示 `indexed` 但 Milvus 无数据，或 Milvus 有数据但状态失败。
- **修复状态**：✅ 向量整页替换改为失败回滚：写入前快照旧行，新插入失败时恢复；进程中断场景由 Worker 启动时 reset stale 状态恢复。

#### 4. Web API `/api/pages/{page_id}` 存在 N+1 查询
- **位置**：`src/rag_notion_kb/web/web_server.py:158-169`
- **问题**：拿到所有 chunks 后，对每个 chunk 单独 query 一次 Milvus 取 `dense_vector`。
- **风险**：chunk 多的时候延迟高、Milvus 压力大。
- **修复状态**：✅ `MilvusStore.get_page_chunks_with_vectors()` 一次查询返回 chunk 与 dense-vector 状态，Web 详情页已接入。

#### 5. Milvus 过滤表达式存在注入风险
- **位置**：`src/rag_notion_kb/storage/milvus_store.py:258-259`, `web_server.py:162`
- **问题**：`_build_filter_expr` 直接拼接 `page_id` / `id` 到 filter 字符串；Web API 未校验输入格式。
- **风险**：破坏 filter 语法、非预期匹配。
- **修复状态**：✅ Web page_id 使用 Notion ID 格式校验；Milvus filter 使用安全字符集并校验时间格式，防止引号/运算符注入。

---

### 中严重度

#### 6. CLI 依赖装配重复且混乱
- **位置**：`src/rag_notion_kb/cli.py`
- **问题**：原文件存在重复 import，多处手动重复装配同一套依赖。
- **风险**：新增依赖时容易漏改、路径不一致。
- **修复状态**：✅ 已引入 `AppContext`，CLI 各命令通过 `AppContext` 获取服务，重复装配和重复 import 已清理。

#### 7. HTTP 客户端不统一
- **位置**：`src/rag_notion_kb/embedding/reranker.py`
- **问题**：`EmbeddingService` 用 `httpx`，`RerankerService` 用 `urllib.request`。
- **风险**：连接池、超时、重试不一致；urllib 无 keep-alive。
- **修复状态**：✅ 已把 `RerankerService` 改为 `httpx.Client`，并添加 `close()`。

#### 8. 批量同步进度全部放在内存
- **位置**：`src/rag_notion_kb/services/sync_progress.py:23`
- **问题**：`SyncProgressTracker` 是进程内 dict，重启丢失，无法多实例共享。
- **风险**：Web Server 重启后进度丢失。
- **修复状态**：✅ `SyncProgressTracker` 支持可选 SQLite db_path，状态写穿并可在重启后恢复；`AppContext` 已接入并统一关闭。

#### 9. 图片下载发生在向量化时刻而非拉取时刻
- **位置**：`src/rag_notion_kb/services/sync_service.py:350-353`
- **问题**：`ImageExtractor` 只提取 URL，`_vectorize_page` 才调用 `download_image`。Notion 预签名 URL 可能已失效。
- **风险**：向量化时图片下载失败率高。
- **修复状态**：✅ `_fetch_page` 在拉取阶段下载图片并重写 Markdown 为本地 URL；向量化阶段保留本地缓存 fallback。

#### 10. `SyncStateStore` schema 迁移默认值有误导性
- **位置**：`src/rag_notion_kb/storage/sync_state.py:79`
- **问题**：迁移时为 `vector_status` 新增列默认值为 `'indexed'`，新行若未显式设置会继承该值，与业务语义不符。
- **风险**：状态错乱。
- **修复状态**：✅ 新列默认值为 `'pending'`，仅将历史 `synced` 页面显式迁移为 `'indexed'`。

---

### 低严重度 / 优化建议

| # | 问题 | 位置 | 修复状态 |
|---|------|------|----------|
| 11 | CLI `inspect` 绕过 `sync_state`，直接查 Milvus | `cli.py:179` | ✅ 已使用 sync_state 校验存在性/展示状态与标题 |
| 12 | 缺少 `/health` 端点 | `web_server.py` | ✅ 已新增无外部依赖的 liveness 端点 |
| 13 | `VectorizeWorker` 直接调用 `SyncService._vectorize_page` | `vectorize_worker.py:114` | ✅ 已改为公开 `SyncService.vectorize_state()` |
| 14 | `NotionClient.enumerate_pages` 对每个页面做两次 API 调用 | `notion/client.py` | ✅ blocks 请求复用；sync_fetch 同时复用遍历得到的 metadata |
| 15 | 缺少结构化 tracing / correlation id | 全链路 | ⏸️ 未开始 |
| 16 | 速率限制与熔断缺失 | Notion/DashScope 调用 | ⏸️ 未开始 |
| 17 | `AppContext` 过早创建 `MilvusStore` | `app_context.py` | ✅ e2e 已通过；仍为 eager 初始化，但不再是阻塞项 |

---

## 二、当前已完成的工作

### 已修改文件

| 文件 | 变更说明 |
|------|----------|
| `src/rag_notion_kb/app_context.py` | **新增**：统一依赖容器，管理 Settings、所有 services/stores 的生命周期，提供 sync/async 上下文管理器 |
| `src/rag_notion_kb/cli.py` | 接入 `AppContext`；移除重复 import 与手动装配；`inspect` 增加 page_id 校验与 sync_state 联动 |
| `src/rag_notion_kb/mcp_server.py` | 新增 `MCPServer.from_context(ctx)`，并适配 `sync()` 的 `(SyncResult, VectorizeResult)` 返回值 |
| `src/rag_notion_kb/web/web_server.py` | `create_app` 改为优先接收 `AppContext`；新增 `/health`；shutdown 统一关闭资源 |
| `src/rag_notion_kb/notion/client.py` | 新增 `close()`；`get_page_markdown()` 支持复用已有 metadata，减少重复 `pages.retrieve` |
| `src/rag_notion_kb/embedding/reranker.py` | `urllib.request` 改为 `httpx.Client`；新增 `close()`；保留重试与错误处理 |
| `src/rag_notion_kb/services/sync_service.py` | fetch 阶段预下载图片；interleave 按 source offset；Refresh 使用三类哈希；零向量结果不再覆盖旧索引；远程图片 URL 保留 |
| `src/rag_notion_kb/models.py` / `processing/chunking.py` / `processing/images.py` | 记录原始 Markdown offset；文本型 fence 与普通文本合并；新增 `CHUNKING_VERSION` |
| `src/rag_notion_kb/embedding/qwen_vl.py` | 新增 `EMBEDDING_VERSION`，Embedding 配置参与 Refresh 指纹 |
| `src/rag_notion_kb/storage/sync_state.py` | 新增 `embedding_hash` / `vector_stage` 迁移；状态与三类哈希原子写入 |
| `src/rag_notion_kb/utils/image_cache.py` / `pyproject.toml` | 内容寻址缓存、图片去重清理、Pillow 压缩、下载重试、远程/本地 URL 双向保存 |
| `src/rag_notion_kb/web/web_server.py` / `web/static/index.html` | 列表页进度卡片显示阶段、百分比与细分 Embedding 批次进度；图片 chunk 卡片清洗签名 alt 并显示缩略图 |
| `src/rag_notion_kb/storage/milvus_store.py` | filter 安全校验、page 方法统一过滤、整页替换快照回滚、统计返回实际向量维度 |
| `src/rag_notion_kb/services/sync_progress.py` | 新增 SQLite 可选持久化、跨实例恢复与 TTL 清理 |
| `src/rag_notion_kb/services/vectorize_worker.py` | 调用公开 `vectorize_state()`；已有原子 claim 增加回归测试 |
| `tests/*` | 更新阶段化 sync 返回值断言；新增注入、回滚、持久化、图片预下载、健康检查、元数据复用测试 |
| `tests/e2e/test_cli.py` | 更新 mock patch 路径以适配 `AppContext`；`serve` 测试改为 mock `MCPServer.from_context` |
| `tests/integration/test_reranker.py` | 从 urllib mock 迁移到 httpx mock；新增 close/连接错误覆盖 |

### 已验证测试

- `tests/unit` + `tests/e2e`：164 passed
- 图片链路相关 integration：32 passed
- 全量集成仍有一个与图片链路无关的旧 `sync_service` 删除语义测试未通过
- 旧 `test_sync_no_root_ids` 阻塞已解除，全部 e2e 通过。

---

## 三、已解除的阻塞项

### 1. `test_sync_no_root_ids` 已通过
- e2e 测试已覆盖 `AppContext` 全部重依赖 patch，当前无需再对重型资源做延迟初始化；如后续要求 `config`/`inspect` 在 Milvus 不可用时也能启动，可再评估 lazy store。

### 2. Subagent 不稳定性
- 本轮改为按边界串行修改，未再依赖并行 subagent。该问题仅作为后续工作方式建议保留。

---

## 四、下一步任务（建议执行顺序）

1. **剩余低优先级项**
   - 1.1 引入结构化 tracing / correlation id，统一贯穿 Notion、DashScope、Milvus 与 Web API 日志。
   - 1.2 为 Notion/DashScope 调用增加速率限制、并发上限与熔断状态。
   - 1.3 可选：将 `AppContext.store` 改为延迟初始化，使不依赖 Milvus 的 CLI 命令在 Milvus 暂不可用时也能启动。

---

## 五、推荐修复策略

- **继续处理 tracing/限流时按层拆分**：请求上下文与日志层独立修改，避免与 Notion/Milvus 服务改动混在同一批。
- **Milvus Lite 集成测试需要在可绑定本地端口的进程中运行**；代码沙箱下会因端口绑定失败报错，需在放开网络/端口权限后执行。
- **完整回归命令**：`PYTHONPATH=src python -m pytest tests/e2e tests/integration tests/unit`
