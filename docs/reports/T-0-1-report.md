# T-0-1 项目脚手架与构建配置 — 执行报告

## 任务信息

- **任务 ID**：T-0-1
- **任务名称**：项目脚手架与构建配置
- **所属 Phase**：P0 基础设施
- **状态**：已完成（环境安装遇到外部网络阻塞）
- **执行时间**：2026-08-10

## 已完成工作

1. 创建 `src/rag_notion_kb/__init__.py`，声明包版本 `0.1.0`。
2. 创建测试目录结构：
   - `tests/__init__.py`
   - `tests/unit/__init__.py`
   - `tests/integration/__init__.py`
   - `tests/e2e/__init__.py`
3. 创建 `reports/` 目录与 `.gitkeep`，用于后续任务报告输出。
4. `pyproject.toml` 中已包含项目依赖声明（含 `langchain-text-splitters`、`tenacity`）。

## 环境与依赖情况

- **miniconda 安装**：失败。
  - 尝试从 `https://repo.anaconda.com/miniconda/Miniconda3-latest-MacOSX-arm64.sh` 下载安装程序。
  - 命令执行后长时间无响应，最终通过 `curl` 单独验证确认：**当前环境无法解析 `repo.anaconda.com`（DNS 失败）**。
- **pypi.org 访问**：失败。
  - `curl -I https://pypi.org` 同样返回 `Could not resolve host`，说明外部 PyPI 也无法访问。
- **已可用依赖**（系统 Python 已安装）：
  - `pydantic`、`pydantic_settings`、`yaml`、`tenacity`、`typer`、`mcp`、`rich`
- **缺失关键依赖**：
  - `pytest`、`pymilvus`、`openai`、`tiktoken`、`langchain_text_splitters`、`aiohttp`、`notion_client`、`notion_to_md_py`

## 应对措施

- 由于无法安装 miniconda 及缺失依赖，后续任务将：
  1. 优先使用系统 Python 中已可用的依赖进行开发。
  2. 使用标准库 `unittest` 替代 `pytest` 执行测试（`pytest` 缺失）。
  3. 对因依赖缺失而无法继续的任务（如 Milvus、OpenAI、Notion Client 相关任务）输出阻塞报告，并停止在该任务上无限重试。

## 下一步

- 继续执行 T-0-2：共享模型、异常、配置 + 测试。
## 环境更新（2026-08-10 后续）

- 用户通过 `brew` 手动安装了 miniconda（`/opt/homebrew/bin/conda`）。
- 尝试在 miniconda 中创建隔离环境并安装依赖，但 **当前沙箱仍然无法解析 `pypi.org` 与 `repo.anaconda.com`（DNS 失败）**，因此无法通过 pip/conda 安装缺失包。
- 为继续推进任务，T-0-2 及后续可执行的任务将回退到 **系统 Python 3.14.6** 运行；系统 Python 中已存在 `pydantic`、`pydantic_settings`、`yaml`、`tenacity`、`typer`、`mcp`、`rich`，足以支撑基础设施与部分核心模块的 TDD。

*** Add File: src/rag_notion_kb/models.py
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ChunkType(str, Enum):
    TEXT = "text"
    TABLE = "table"
    CODE = "code"
    IMAGE = "image"


class ChunkMetadata(BaseModel):
    page_id: str
    page_title: str
    page_url: str
    header_path: str
    header_level: int = Field(..., ge=1, le=6)
    last_edited_time: str
    chunk_index: int = Field(..., ge=0)
    chunk_type: ChunkType
    image_url: str | None = None


class Chunk(BaseModel):
    text: str
    metadata: ChunkMetadata


class ImageDoc(BaseModel):
    image_url: str
    alt: str
    context_text: str
    metadata: ChunkMetadata


class EmbeddingItem(BaseModel):
    type: Literal["text", "image_url"]
    text: str | None = None
    image_url: str | None = None


class RankCandidate(BaseModel):
    text: str | None = None
    image_url: str | None = None


class SearchHit(BaseModel):
    id: int
    chunk_text: str
    score: float
    metadata: ChunkMetadata


class SearchResult(BaseModel):
    text: str
    score: float
    source: ChunkMetadata
    matched_snippet: str | None = None


class PageSyncState(BaseModel):
    page_id: str
    page_title: str
    page_url: str
    last_edited_time: str
    parent_id: str | None = None
    chunk_count: int
    image_count: int
    status: Literal["synced", "failed", "skipped"]
    error_message: str | None = None
    last_synced_time: str


class SyncResult(BaseModel):
    added: int = 0
    updated: int = 0
    removed: int = 0
    skipped: int = 0
    failed: int = 0
