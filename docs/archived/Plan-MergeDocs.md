# RAG Notion 知识库系统 — 代码/文档/数据一致性治理计划

> **版本**：v1.0  
> **日期**：2026-08-14  
> **目标**：建立代码、文档、数据的强一致性，以代码为唯一 Source of Truth，文档忠实反映代码实现，数据 Schema 与代码模型对齐  
> **范围**：文档治理、一致性校验、自动化工具、文档重组

---

## 1. 现状诊断

### 1.1 文档碎片化问题

当前 `docs/` 目录存在**多份并行 Plan 文档**，内容交叉重叠，版本号混乱：

| 文档 | 版本 | 日期 | 内容主题 | 问题 |
|------|------|------|----------|------|
| `Plan.md` | v1.2 | 2026-08-11 | 系统架构总览 | 已过时，未包含后续新增功能 |
| `Plan-AsyncEmbedding.md` | v1.0 | 2026-08-12 | 向量开关 + 后台异步向量化 | 功能已实现，未归档 |
| `Plan-RootManagement.md` | v1.0 | 2026-08-12 | Root 管理 + 选择性同步 | 功能已实现，未归档 |
| `Plan-SearchDebug_v1.0.md` | v1.0 | 2026-08-12 | 检索调试 | 被 v1.1 替代 |
| `Plan-SearchDebug_v1.1.md` | v1.1 | 2026-08-12 | 检索调试 + 检索历史 | 功能已实现，未归档 |
| `Plan-UIRefactor.md` | v1.0 | 2026-08-12 | Vue 前端重构 | 待实现 |
| `Backend.md` | v1.2 | 2026-08-11 | 后端技术实现 | 需与代码对齐 |
| `Frontend.md` | v1.1 | 2026-08-11 | 前端技术实现 | 需与代码对齐 |
| `Spec.md` | v1.2 | 2026-08-11 | 需求规格 | 需与代码对齐 |
| `Stand.md` | v1.1 | 2026-08-11 | 验收标准 | 需与代码对齐 |

### 1.2 代码与文档不对齐的典型表现

| 维度 | 文档描述 | 代码实际 | 不一致等级 |
|------|----------|----------|------------|
| **数据模型** | `PageSyncState` 字段列表 | `models.py:66-86` 已新增 `last_vectorized_time`, `content_hash`, `chunking_hash` | 🔴 高 |
| **SQLite Schema** | `sync_state.py` DDL | `sync_state.py:14-38` 已包含 15 列，含向量相关字段 | 🟡 中 |
| **API 路由** | `Plan-RootManagement.md` 描述 `/api/roots` | 代码中尚未实现（仅 Plan 阶段） | 🔴 高 |
| **CLI 命令** | `Plan-SearchDebug.md` 描述检索历史命令 | `cli.py` 无 `search` 子命令 | 🟡 中 |
| **前端架构** | `Frontend.md` 描述单文件 HTML | `Plan-UIRefactor.md` 规划 Vue SPA | 🔴 高 |
| **配置项** | `Backend.md` 配置列表 | `config.py` 已新增 `VectorizeConfig` | 🟡 中 |

### 1.3 数据层现状

运行时数据目录 `~/.rag_kb/`：
- `sync_state.db` (1.8MB)：SQLite，含 `page_sync_state` 表
- `milvus.db`：向量数据库
- `images/`：图片缓存

**风险**：`sync_state.db.corrupted` 存在，说明有历史数据损坏记录，文档未提及容灾策略。

---

## 2. 核心治理原则

### 2.1 单向溯源：代码 → 文档 → 数据

```
┌─────────────┐    抽取/生成     ┌─────────────┐    校验/对齐     ┌─────────────┐
│   代码       │ ───────────────→ │   文档       │ ───────────────→ │   数据       │
│ (Source of  │                  │ (忠实反映)   │                  │ (Schema 对齐) │
│  Truth)     │                  │              │                  │              │
└─────────────┘                  └─────────────┘                  └─────────────┘
```

**规则**：
1. **代码是唯一的 Source of Truth**。任何功能以代码实现为准。
2. **文档必须滞后于代码，但不超过 1 个迭代周期**。
3. **数据 Schema 由代码模型驱动**，禁止手动修改数据库结构。
4. **Plan 文档在功能实现后必须归档**，不能长期作为"活跃文档"。

### 2.2 文档分层架构（重组后）

```
docs/
├── README.md                 # 项目总览，Quick Start
├── Spec.md                   # 需求规格（用户视角，少变更）
├── Architecture.md           # 系统架构（合并原 Plan.md，总览级）
├── Backend.md                # 后端实现（与代码对齐，API 文档）
├── Frontend.md               # 前端实现（与代码对齐）
├── Stand.md                  # 验收标准（与代码测试对齐）
├── Manual.md                 # 用户手册
├── archived/                 # 归档目录（实现后的 Plan）
│   ├── Plan-AsyncEmbedding.md
│   ├── Plan-RootManagement.md
│   ├── Plan-SearchDebug_v1.1.md
│   └── ...
├── dev/                      # 开发文档
│   └── Plan-UIRefactor.md    # 当前待实现的 Plan
└── consistency-report.md     # 一致性校验报告（自动生成）
```

**规则**：
- `archived/`：功能已实现并验收后，Plan 文档移入此处，冻结不再修改。
- `dev/`：仅存放**待实现**的 Plan，实现后移入 `archived/` 并更新正式文档。
- 活跃文档（根目录下）不超过 7 份。

---

## 3. 一致性校验矩阵

定义代码与文档需要对齐的维度：

### 3.1 数据模型 ↔ 文档

| 校验项 | 代码位置 | 文档位置 | 校验方法 |
|--------|----------|----------|----------|
| Pydantic 模型字段 | `models.py` | `Backend.md` 第 5 章 | 脚本比对字段名+类型 |
| SQLite 表结构 | `storage/sync_state.py` DDL | `Backend.md` 第 8.1 节 | 脚本 `PRAGMA table_info` 比对 |
| Milvus Collection Schema | `storage/schema.py` | `Backend.md` 第 8.2 节 | 脚本 `describe_collection` 比对 |
| 配置项 | `config.py` | `Backend.md` 第 6 章 | 脚本比对类属性 |
| 异常类型 | `exceptions.py` | `Backend.md` 第 7 章 | 脚本比对类名 |

### 3.2 API 路由 ↔ 文档

| 校验项 | 代码位置 | 文档位置 | 校验方法 |
|--------|----------|----------|----------|
| FastAPI 路由 | `web/web_server.py` | `Backend.md` 第 13 章 / 各 Plan | 脚本提取 `@app.*` 装饰器 |
| 请求/响应模型 | `models.py` | `Backend.md` 第 5 章 | 脚本比对 Pydantic 模型 |
| MCP Tools | `mcp_server.py` | `Backend.md` 第 13 节 | 脚本提取 tool 注册 |

### 3.3 CLI 命令 ↔ 文档

| 校验项 | 代码位置 | 文档位置 | 校验方法 |
|--------|----------|----------|----------|
| Typer 命令 | `cli.py` `@app.command()` | `Backend.md` 第 14 章 | 脚本提取命令名+参数 |

### 3.4 前端 ↔ 文档

| 校验项 | 代码位置 | 文档位置 | 校验方法 |
|--------|----------|----------|----------|
| 页面/组件 | `web/static/` 文件列表 | `Frontend.md` | 脚本比对文件名 |
| API 调用 | `web/static/` 中的 `fetch` | `Backend.md` API 章节 | 脚本提取 `fetch` URL |

---

## 4. 自动化校验工具设计

### 4.1 `scripts/consistency_check.py`

一个 Python 脚本，扫描代码并生成一致性报告。

```python
#!/usr/bin/env python3
"""Consistency checker: compare code against docs."""

import ast
import json
import re
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DOCS_DIR = PROJECT_ROOT / "docs"
SRC_DIR = PROJECT_ROOT / "src" / "rag_notion_kb"
DATA_DIR = Path.home() / ".rag_kb"

class ConsistencyChecker:
    def check_models(self) -> list[dict]:
        """Extract Pydantic models from models.py and compare with Backend.md."""
        ...

    def check_sqlite_schema(self) -> list[dict]:
        """Compare sync_state.py DDL with actual DB schema."""
        ...

    def check_api_routes(self) -> list[dict]:
        """Extract FastAPI routes from web_server.py."""
        ...

    def check_cli_commands(self) -> list[dict]:
        """Extract Typer commands from cli.py."""
        ...

    def check_mcp_tools(self) -> list[dict]:
        """Extract MCP tool names from mcp_server.py."""
        ...

    def generate_report(self) -> str:
        """Generate Markdown report."""
        ...
```

### 4.2 校验输出格式

```markdown
# Consistency Report (Generated: 2026-08-14 10:00:00)

## Summary
| Dimension | Aligned | Mismatched | Missing in Docs | Missing in Code |
|-----------|---------|------------|-----------------|-----------------|
| Data Models | 12 | 3 | 0 | 2 |
| SQLite Schema | 15 | 0 | 0 | 0 |
| API Routes | 8 | 2 | 3 | 0 |
| CLI Commands | 4 | 1 | 0 | 0 |
| MCP Tools | 4 | 0 | 0 | 0 |

## Details

### 🔴 Mismatch: PageSyncState.vector_progress
- Code: `vector_progress: int = Field(0, ge=0, le=100)`
- Docs (Backend.md): `vector_progress: int` (缺少 ge/le 约束描述)
- Action: 更新 Backend.md 第 5 章

### 🔴 Missing in Docs: POST /api/search/debug
- Code: `web_server.py:195` `@app.post("/api/search/debug")`
- Docs: 未在任何活跃文档中描述
- Action: 更新 Backend.md API 章节

### 🟡 Mismatch: CLI `search` command
- Code: 不存在
- Docs (Plan-SearchDebug): 描述 `rag-kb search` 命令
- Action: 从文档中移除或标记为待实现
```

### 4.3 集成到 CI（可选）

```yaml
# .github/workflows/consistency.yml（未来扩展）
name: Docs Consistency Check
on: [push]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: python scripts/consistency_check.py --fail-on-mismatch
```

---

## 5. 文档更新实施步骤

### Phase 1：运行一致性校验（1h）

1. **编写并运行 `scripts/consistency_check.py`**：
   - 扫描 `models.py` → 提取所有 Pydantic 模型
   - 扫描 `storage/sync_state.py` → 提取 DDL
   - 扫描 `web/web_server.py` → 提取 API 路由
   - 扫描 `cli.py` → 提取命令
   - 扫描 `mcp_server.py` → 提取 tools
   - 生成 `docs/consistency-report.md`

2. **人工复核**：
   - 阅读报告，标记"误报"（如文档有意省略的详情）
   - 确认真正的"不一致"清单

### Phase 2：文档重组（1h）

1. **创建目录结构**：
   ```bash
   mkdir docs/archived
   mkdir docs/dev
   ```

2. **归档已实现的功能 Plan**：
   ```bash
   mv docs/Plan-AsyncEmbedding.md docs/archived/
   mv docs/Plan-RootManagement.md docs/archived/
   mv docs/Plan-SearchDebug_v1.0.md docs/archived/
   mv docs/Plan-SearchDebug_v1.1.md docs/archived/
   ```

3. **保留待实现的 Plan**：
   ```bash
   mv docs/Plan-UIRefactor.md docs/dev/
   ```

4. **重命名 `Plan.md` → `Architecture.md`**：
   - 更新内容：纳入所有已实现的架构变更（向量开关、Root 管理、检索历史等）
   - 移除过时的实现计划细节

### Phase 3：更新核心文档（3h）

按优先级更新以下文档，以代码为 Source of Truth：

1. **`Backend.md`**（最高优先级）：
   - 第 5 章：更新 `models.py` 中的所有模型定义
   - 第 6 章：更新 `config.py` 配置项（含 `VectorizeConfig`）
   - 第 8.1 章：更新 SQLite DDL（15 列完整 DDL）
   - 第 8.2 章：更新 Milvus Schema
   - 第 12 章：更新 `SyncService` 拆分后的接口（`sync_fetch`, `sync_vectorize`）
   - 新增：API 路由完整清单（从 `web_server.py` 提取）
   - 新增：`SearchService.debug_search()` 接口
   - 新增：`SearchHistoryStore` 接口

2. **`Frontend.md`**（高优先级）：
   - 更新前端架构描述（从单文件 HTML → Vue SPA）
   - 更新页面列表（Root 管理、检索调试、检索历史）
   - 更新 API 调用清单

3. **`Architecture.md`**（原 `Plan.md`）（中优先级）：
   - 更新系统架构图（纳入所有已实现模块）
   - 更新数据流图
   - 更新模块依赖图
   - 移除具体实现细节（移到 `Backend.md`）

4. **`Spec.md`**（低优先级）：
   - 仅更新已变更的需求范围（如新增检索历史、Root 管理）
   - 保持需求层面的稳定性

5. **`Stand.md`**（低优先级）：
   - 新增已实现功能的验收标准
   - 标记已完成的 AC

### Phase 4：建立版本号规范（0.5h）

**文档版本号规则**：
```
{major}.{minor}.{patch}
```
- **major**：架构级变更（如从单页到 SPA）
- **minor**：功能新增（如新增检索历史）
- **patch**：与代码对齐的修正（如字段更新、API 补充）

**版本号同步规则**：
- 所有活跃文档（根目录下）的 `major` 必须一致。
- 当代码发布新版本时，所有活跃文档同步升级 `minor`。
- 单独文档的 patch 更新无需同步其他文档。

**当前建议版本**：`v2.0.0`（因前端架构从单文件 HTML 变为 Vue SPA，属于 major 变更）

### Phase 5：持续维护机制（0.5h）

1. **提交前检查清单**（添加到 `README.md` 或 `CONTRIBUTING.md`）：
   ```markdown
   ## 提交前检查清单
   - [ ] 代码变更已更新对应的单元测试
   - [ ] 数据模型变更已更新 `Backend.md` 第 5 章
   - [ ] API 路由变更已更新 `Backend.md` API 清单
   - [ ] CLI 命令变更已更新 `Backend.md` 第 14 章
   - [ ] 前端页面变更已更新 `Frontend.md`
   - [ ] 运行 `python scripts/consistency_check.py` 无新增 mismatch
   ```

2. **迭代结束时强制文档更新**：
   - 每个功能迭代（sprint）结束前，预留 0.5 天用于文档对齐。

---

## 6. 一致性报告模板

脚本生成的报告格式：

```markdown
# Consistency Report

> Generated: 2026-08-14 10:00:00  
> Code HEAD: abc1234  
> Data Dir: ~/.rag_kb

## 1. Data Models

### Aligned (12)
| Model | Field | Type | Docs Location |
|-------|-------|------|---------------|
| Chunk | text | str | Backend.md §5.1 |
| ... | ... | ... | ... |

### Mismatched (3)
| Model | Field | Issue | Code | Docs | Action |
|-------|-------|-------|------|------|--------|
| PageSyncState | vector_progress | 缺少约束描述 | `Field(0, ge=0, le=100)` | `int` | 更新 Docs |

### Missing in Docs (2)
| Model | Code Location | Suggested Docs Location |
|-------|---------------|------------------------|
| SearchHistory | models.py:120 | Backend.md §5.2 |

## 2. SQLite Schema

| Table | Column | Code (DDL) | DB (PRAGMA) | Docs | Status |
|-------|--------|------------|-------------|------|--------|
| page_sync_state | vector_enabled | INTEGER | INTEGER | ✅ | ✅ Aligned |

## 3. API Routes

| Method | Path | Handler | Docs Location | Status |
|--------|------|---------|---------------|--------|
| GET | /api/pages | list_pages | Backend.md §13 | ✅ |
| POST | /api/search/debug | debug_search | ❌ Missing | 🔴 |

## 4. CLI Commands

| Command | Params | Docs Location | Status |
|---------|--------|---------------|--------|
| sync | --root, --full, --vectorize | Backend.md §14 | ✅ |
| search | ❌ Not implemented | Plan-SearchDebug | 🟡 Phantom |

## 5. MCP Tools

| Tool | Docs Location | Status |
|------|---------------|--------|
| rag_search | Backend.md §13 | ✅ |
| rag_sync | Backend.md §13 | ✅ |

## Action Items

1. [P0] Backend.md: 补充 `/api/search/debug` 等 3 个缺失 API 路由
2. [P0] Backend.md: 更新 `PageSyncState` 字段约束描述
3. [P1] Backend.md: 新增 `SearchHistory` 模型描述
4. [P1] 移除/归档 Plan-SearchDebug 中不存在的 CLI `search` 命令
```

---

## 7. 验收标准

| # | 验收项 | 通过标准 |
|---|--------|----------|
| 1 | 文档重组完成 | `docs/` 根目录下活跃文档 ≤ 7 份；`archived/` 和 `dev/` 目录存在且内容正确 |
| 2 | 一致性脚本可运行 | `python scripts/consistency_check.py` 成功生成报告，无报错 |
| 3 | 核心文档与代码对齐 | `Backend.md` 中描述的模型/Schema/API/CLI 与代码差异 ≤ 5% |
| 4 | 版本号统一 | 所有活跃文档版本号 `major` 一致（如 v2.x.x） |
| 5 | 无"幽灵文档" | 已归档的 Plan 文档中不存在"未实现却被描述为已实现"的功能 |
| 6 | 数据 Schema 对齐 | `sync_state.db` 的 `PRAGMA table_info` 与 `sync_state.py` DDL 差异为 0 |

---

## 8. 修订历史

| 版本 | 日期 | 作者 | 变更内容 |
|------|------|------|----------|
| v1.0 | 2026-08-14 | Claude | 初始版本：诊断文档碎片化与代码文档不对齐问题，设计一致性治理方案（溯源原则、校验矩阵、自动化工具、文档重组、版本号规范、持续维护机制） |

---

*本文档为代码/文档/数据一致性治理的权威计划。治理完成后，代码是唯一的 Source of Truth，文档忠实反映代码，数据 Schema 与代码模型严格对齐。*
