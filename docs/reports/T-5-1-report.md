# T-5-1 Sync Service 开发测试报告

> 任务：实现 Backend.md 第 12.1 节定义的 `SyncService`
> 日期：2026-08-11
> 执行者：Codex

## 实现文件

- `src/rag_notion_kb/services/__init__.py`
- `src/rag_notion_kb/services/sync_service.py`

## 测试文件

- `tests/integration/test_sync_service.py`

## 实现要点

1. **依赖注入**：通过构造函数接收 `NotionClient`、`MarkdownProcessor`、`ImageExtractor`、`EmbeddingService`、`MilvusStore`、`SyncStateStore`、`Settings`。
2. **同步算法**：
   - 解析 `root_page_ids`（参数优先于配置）。
   - 调用 `NotionClient.enumerate_pages()` 获取当前所有可达页面。
   - 对每个页面：增量判断、拉取 Markdown、分块、提取图片、Embedding、Milvus 页面级替换、更新同步状态。
   - 检测已删除页面并从 Milvus / SQLite 清理。
3. **错误处理**：单页面失败记录 `status="failed"` 并继续，不阻断整体同步。
4. **返回值**：`SyncResult` 包含 added / updated / removed / skipped / failed 计数。

## 测试覆盖

| 用例 | 说明 | 结果 |
| --- | --- | --- |
| test_full_sync_adds_pages | 首次全量同步，2 个页面写入 Milvus 与 SQLite | PASSED |
| test_incremental_sync_skips_unchanged | `last_edited_time` 未变时跳过，不调用分块/Embedding | PASSED |
| test_force_full_reprocesses_unchanged | `force_full=True` 强制重新处理 | PASSED |
| test_delete_detection_removes_missing_pages | Notion 中已删除页面从索引和状态表清理 | PASSED |
| test_single_page_failure_does_not_block_others | 单个页面异常不影响其他页面同步 | PASSED |

## 执行命令

```bash
export TMPDIR=/Users/ray/Workspace/warehouse-profession-system/.tmp
PYTHONPATH=src python3 -m pytest tests/integration/test_sync_service.py -v
```

## 结果

5 passed, 0 failed
