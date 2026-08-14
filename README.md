# RAG Notion KB

> **本地优先的 Notion 多模态知识库检索系统**
>
> 从 Notion 递归拉取页面与图片，按需向量化，支持混合检索（Dense + BM25）与重排序，通过 Web UI / CLI / MCP Server 三种方式提供服务。

---

## 核心特性

| 特性 | 说明 |
|------|------|
| 🗂️ **Notion 原生集成** | 递归拉取页面树，保留 Markdown 结构与图片 |
| ⚡ **按需向量化** | 页面级向量开关，后台异步 Embedding，支持并发 |
| 🔍 **混合检索** | Dense 语义检索 + BM25 关键词检索 + ReRank 重排序 |
| 📊 **检索调试** | 可视化 Dense/Sparse/RRF/ReRank 各阶段分数，参数实时可调 |
| 📜 **检索历史** | 自动记录 Debug / MCP / CLI 全部检索操作，支持回放 |
| 🖥️ **三种入口** | Web UI（Vue 3 SPA）、CLI（Typer）、MCP Server（stdio） |
| 🏠 **本地优先** | 所有数据存储在本地（SQLite + Milvus Lite），零外部依赖 |

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│  交互层 (Web UI / CLI / MCP Server)                          │
├─────────────────────────────────────────────────────────────┤
│  服务层 (SyncService / SearchService / VectorizeWorker)      │
├─────────────────────────────────────────────────────────────┤
│  处理层 (NotionClient / Chunking / ImageExtractor / Embed)   │
├─────────────────────────────────────────────────────────────┤
│  数据层 (SQLite sync_state + Milvus Lite + Image Cache)      │
└─────────────────────────────────────────────────────────────┘
```

详细架构图见 [`docs/Architecture.md`](docs/Architecture.md)。

---

## 快速开始

### 1. 安装

```bash
git clone <your-repo-url>
cd rag-notion-kb
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

### 2. 配置

复制模板并填写你的 Token：

```bash
cp .env.example .env
# 编辑 .env，填写 NOTION_TOKEN 和 DASHSCOPE_API_KEY
```

### 3. 首次同步

```bash
# 启动 Web UI，在浏览器中添加 Notion Root 页面并执行同步
rag-kb web --open-browser

# 或使用 CLI 直接同步
rag-kb sync --root <your-root-page-id>
```

### 4. 开启向量化

在 Web UI 的"页面列表"中，打开目标页面的向量开关，后台 Worker 会自动执行 Embedding。

### 5. 检索

```bash
# CLI
rag-kb search "你的检索问题"

# Web UI
# 打开"检索调试"页面，调整参数后查询

# MCP (Claude Code)
# 配置 claude_desktop_config.json 后，Claude 会自动调用 rag_search
```

---

## 项目结构

```
rag-notion-kb/
├── pyproject.toml              # 项目配置与依赖
├── README.md                   # 本文档
├── .env.example                # 环境变量模板
├── src/
│   └── rag_notion_kb/          # 主代码包
│       ├── cli.py              # CLI 入口
│       ├── mcp_server.py       # MCP Server
│       ├── models.py           # 共享数据模型
│       ├── config.py           # 配置管理
│       ├── exceptions.py       # 异常体系
│       ├── notion/             # Notion 数据拉取
│       ├── processing/         # Markdown 分块与图片提取
│       ├── embedding/          # Embedding / ReRank
│       ├── storage/            # SQLite + Milvus
│       ├── retrieval/          # 混合检索 + 上下文扩展
│       ├── services/           # Sync / Search / Worker
│       └── web/                # FastAPI + 静态前端
├── tests/                      # 测试（unit / integration / e2e）
├── scripts/                    # 工具脚本
└── docs/                       # 文档
    ├── Spec.md                 # 需求规格
    ├── Architecture.md         # 系统架构
    ├── Backend.md              # 后端实现细节
    ├── Frontend.md             # 前端实现细节
    ├── Stand.md                # 验收标准
    └── Manual.md               # 用户手册
```

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI + Uvicorn |
| 向量数据库 | Milvus Lite (pymilvus) |
| 关系数据库 | SQLite (标准库) |
| Embedding | Qwen3-VL-Embedding (DashScope) |
| ReRank | Qwen3-VL-Rerank (DashScope) |
| 分块 | MarkdownHeaderTextSplitter |
| CLI | Typer |
| MCP | mcp SDK (stdio) |
| 前端 | Vue 3 + Arco Design Vue (CDN) |
| Markdown 渲染 | marked.js + highlight.js |

---

## 数据目录

默认数据存储在用户家目录 `~/.rag_kb/`：

| 文件/目录 | 说明 |
|-----------|------|
| `sync_state.db` | SQLite：页面同步状态、Root 配置、检索历史 |
| `milvus.db` | Milvus Lite：向量索引 |
| `images/` | Notion 图片本地缓存 |
| `config.yaml` | 用户配置（不含敏感信息） |

---

## 文档索引

| 文档 | 内容 | 读者 |
|------|------|------|
| [`Spec.md`](docs/Spec.md) | 需求规格说明书 | 产品经理、用户 |
| [`Architecture.md`](docs/Architecture.md) | 系统架构、数据流、模块边界 | 技术负责人、开发者 |
| [`Backend.md`](docs/Backend.md) | 后端模型、Schema、API、CLI、MCP | 后端开发者 |
| [`Frontend.md`](docs/Frontend.md) | 前端架构、组件、路由 | 前端开发者 |
| [`Stand.md`](docs/Stand.md) | 验收标准与测试映射 | 测试工程师 |
| [`Manual.md`](docs/Manual.md) | 安装、配置、使用手册 | 最终用户 |

---

## 开发

### 运行测试

```bash
pytest tests/unit
pytest tests/integration
pytest tests/e2e
```

### 代码风格

```bash
ruff format src tests
ruff check src tests
mypy src
```

### 一致性检查

```bash
python scripts/consistency_check.py
```

脚本会自动比对代码中的模型、Schema、API、CLI 与文档描述的一致性。

---

## 提交前检查清单

- [ ] 代码变更已更新对应测试
- [ ] 数据模型变更已更新 `Backend.md`
- [ ] API 路由变更已更新 `Backend.md`
- [ ] CLI / MCP Tool 变更已更新 `Backend.md`
- [ ] 前端页面变更已更新 `Frontend.md`
- [ ] 运行一致性脚本无新增 mismatch

---

## License

MIT License

---

*文档版本：v2.0.0 | 最后更新：2026-08-14*
