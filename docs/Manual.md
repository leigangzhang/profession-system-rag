# RAG Notion 知识库系统 — 用户手册

> **版本**：v2.0.0  
> **日期**：2026-08-14

## 1. 安装

```bash
cd /Users/ray/Workspace/warehouse-profession-system
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

安装后入口命令为 `rag-kb`。

## 2. 配置

配置优先级：

1. 显式构造参数。
2. `RAG_KB_*` 环境变量。
3. 项目 `.env`。
4. `~/.rag_kb/config.yaml`。
5. file secrets。

最小 YAML：

```yaml
notion:
  token: "ntn_xxx"
  root_page_ids:
    - "32-char-page-id"

embedding:
  api_key: "sk-xxx"
  model: "qwen3-vl-embedding"
  dimensions: 2048

reranker:
  api_key: "sk-xxx"
  model: "qwen3-vl-rerank"

storage:
  data_dir: "~/.rag_kb"

vectorize:
  max_concurrent: 3
  poll_interval_seconds: 2
```

环境变量使用 `__` 表示嵌套，例如：

```bash
export RAG_KB_NOTION__TOKEN="ntn_xxx"
export RAG_KB_EMBEDDING__API_KEY="sk-xxx"
export RAG_KB_LOGGING__LEVEL="DEBUG"
```

查看脱敏后的当前配置：

```bash
rag-kb config
```

## 3. Root 管理

Root 是递归同步的入口页面。推荐先启动 Web UI：

```bash
rag-kb web --open-browser
```

打开 `http://127.0.0.1:58000/#/roots`：

1. 输入 Notion 页面 ID 添加 Root。
2. 展开页面树，查看缓存时间和每个页面的同步状态。
3. 勾选需要同步的页面。
4. 点击批量同步，查看 `SyncProgress`。

删除 Root 会删除该 Root 对应的页面树缓存、同步状态和 Milvus 数据。

## 4. 同步

### 4.1 命令行全量同步

```bash
# 使用配置 Root
rag-kb sync

# 指定 Root
rag-kb sync --root "page-id-1,page-id-2"

# 忽略 last_edited_time，强制重拉
rag-kb sync --full

# 拉取后同时向量化所有 enabled 页面
rag-kb sync --vectorize
```

默认 `rag-kb sync` 只执行拉取阶段。它把原始 Markdown 写入 SQLite，不立即执行 Embedding 和 Milvus 写入。

### 4.2 Web UI 页面开关

打开 `#/pages`：

- 打开页面上的向量开关，页面进入 `pending`，后台 Worker 会自动处理。
- 也可以点击“向量化”按钮手动入队。
- `pending` 表示等待，`indexing` 显示进度和阶段，`indexed` 表示完成，`failed` 显示错误。

### 4.3 查看状态

```bash
rag-kb status
rag-kb inspect "page-id"
```

`inspect` 展示页面 chunk、图片、非零向量数、维度和前 5 个向量样本。

## 5. 检索

### 5.1 CLI 检索

```bash
rag-kb search "如何设计增量同步" --top-k 5
rag-kb search "图片处理" --page-ids "page-id-1,page-id-2"
rag-kb search "安装步骤" --skip-rerank
```

主要参数：

| 参数 | 默认值 |
|------|--------|
| `--top-k` | 5 |
| `--expand-level` | 2 |
| `--max-tokens` | 4000 |
| `--dense-weight` | 0.5 |
| `--sparse-weight` | 0.5 |
| `--min-similarity` | 0.4 |
| `--context-mode` | h2 |
| `--rerank-model` | qwen3-vl-rerank |

所有 CLI 检索都会写入检索历史，来源为 `cli`。

### 5.2 Web UI 检索调试

打开 `#/search-debug`，可设置：

- Dense/Sparse 权重。
- TopK、最小相似度和过滤条件。
- ReRank 模型或跳过 ReRank。
- `none`、`parent`、`h2` 上下文模式。
- 最大 token。

结果中会显示 dense、sparse、fusion、rerank、final 分数，以及每阶段候选数量。

### 5.3 检索历史

打开 `#/search-history` 查看 Debug、MCP、CLI 三种来源的记录、成功率、零结果率、延迟和分数统计。点击记录可跳转到调试页回放；也可以删除单条或清空全部。

## 6. MCP Server

### 6.1 启动

```bash
rag-kb serve
```

该命令监听 stdio，适合接入支持 MCP 的客户端。

Claude Desktop 示例：

```json
{
  "mcpServers": {
    "rag-notion-kb": {
      "command": "rag-kb",
      "args": ["serve"]
    }
  }
}
```

### 6.2 Tools

| Tool | 用途 | 典型调用 |
|------|------|----------|
| `rag_search` | 混合检索，可过滤、扩展上下文、ReRank | `query="同步策略"` |
| `rag_sync` | 增量或全量同步 | `root="page-id"`、`full=true` |
| `rag_stats` | 查看知识库健康状态 | 无参数 |
| `rag_page_detail` | 读取单个已同步页面全文 | `page_id="page-id"` |

## 7. Web UI

页面：

| 路由 | 功能 |
|------|------|
| `#/roots` | Root 管理、页面树、选择性同步 |
| `#/pages` | 页面列表、向量开关、向量化状态 |
| `#/page/{page_id}` | Markdown 与 Chunk 详情 |
| `#/search-debug` | 检索调试 |
| `#/search-history` | 检索历史与统计 |

Web Server 默认绑定 `127.0.0.1:58000`。`--open-browser` 可在启动后打开浏览器，`--max-concurrent` 可覆盖 Worker 并发。

## 8. 数据目录

```text
~/.rag_kb/
├── config.yaml
├── sync_state.db
├── milvus.db/
├── images/
└── .milvus_addr
```

主要文件：

- `sync_state.db`：同步状态、Root、页面树缓存、同步进度与检索历史。
- `milvus.db`：Milvus Lite 本地向量库。
- `images/`：从 Notion 下载的本地图片缓存。
- `.milvus_addr`：Milvus Lite 进程共享地址；Web Server 关闭时会移除。

## 9. 日志与故障处理

默认日志为 JSON，输出到 stderr：

```bash
export RAG_KB_LOGGING__LEVEL="DEBUG"
export RAG_KB_LOGGING__FORMAT="text"
rag-kb sync
```

常见问题：

| 现象 | 处理 |
|------|------|
| 没有 Root | 用 `--root` 指定、在 Web UI 添加，或配置 `notion.root_page_ids` |
| 页面 `pending` 不变 | 检查 Web UI Worker 状态和 Embedding API 配置 |
| 页面 `failed` | 查看 `vector_error_message`；修复后重新打开开关或点“向量化” |
| Milvus 连接异常 | 停止所有进程后重试；必要时先备份并重建 `~/.rag_kb/milvus.db` |
| SQLite 存在 `.corrupted` 文件 | 停止读写；备份有效库；如主库不可用，可基于历史文件恢复或重建 |

## 10. 典型工作流

```bash
rag-kb config
rag-kb web --open-browser
# 在 Root 管理页添加 Root、选页面并同步
# 在页面列表打开向量开关
rag-kb status
rag-kb search "示例查询"
rag-kb serve
```

## 11. 修订历史

| 版本 | 日期 | 变更内容 |
|------|------|----------|
| v2.0.0 | 2026-08-14 | 补充完整 CLI、Web UI、两阶段同步、Root 管理、检索调试与历史说明 |
