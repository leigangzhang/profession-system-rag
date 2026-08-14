# T-6-1 MCP Server E2E 测试报告

> 任务：实现 Backend.md 第 13 节定义的 MCP Server，4 个 Tool，stdio 传输
> 日期：2026-08-11
> 执行者：Codex（子代理 Godel）

## 实现文件

- `src/rag_notion_kb/mcp_server.py`（141 行，重构）
- `tests/e2e/test_mcp_server.py`（291 行，新建）

## 关键设计决策

| # | 决策 | 说明 |
|---|---|---|
| 1 | FastMCP 替代 Server | 原 `mcp.server.Server` 不支持 `.tool()` 装饰器；切换至 `mcp.server.FastMCP`，同时改用 `run_stdio_async()` 启动 |
| 2 | 测试方案 | 不通过子进程 stdio 通信，而是直接实例化 `MCPServer` + MagicMock 服务，通过 `FastMCP.call_tool()` 调用 handler，避免 stdio 协议复杂度 |
| 3 | 结果格式化 | `rag_search` 返回 Markdown 格式（标题/来源链接/相关度分数/header_path/原文匹配）；图片结果渲染为 `![alt](url)` |

## 架构

```
MCPServer
├── _register_tools()          # 用 @server.tool() 注册 4 个 handler
├── rag_search(query, ...)     # → SearchService.search() → Markdown 格式化
├── rag_sync(root, full)       # → SyncService.sync() → 计数摘要
├── rag_stats()                # → SearchService.stats() → 多行列表
├── rag_page_detail(page_id)   # → SearchService.get_page_detail() → 全文
├── run_async()                # run_stdio_async() 启动 stdio 传输
└── run()                      # asyncio.run() 同步包装
```

## 测试用例详情

| # | 用例 | 覆盖内容 | 结果 |
|---|------|----------|------|
| 1 | `test_rag_search_returns_results` | 正常检索返回 Markdown 包含标题/分数/header_path | PASSED |
| 2 | `test_rag_search_empty_results` | 空结果返回 "_未找到相关结果。_" | PASSED |
| 3 | `test_rag_search_passes_page_ids_filter` | page_ids 过滤器正确透传 | PASSED |
| 4 | `test_rag_search_passes_header_level_filter` | header_level 过滤器正确透传 | PASSED |
| 5 | `test_rag_search_missing_optional_params_uses_defaults` | top_k/expand_to_level/max_tokens 缺省使用默认值 | PASSED |
| 6 | `test_rag_sync_returns_summary` | 同步摘要包含 added/updated/removed/skipped/failed 计数 | PASSED |
| 7 | `test_rag_sync_passes_root_comma_separated` | `--root` 逗号分隔解析为 `list[str]` | PASSED |
| 8 | `test_rag_sync_passes_full_true` | `full=True` 映射为 `force_full=True` | PASSED |
| 9 | `test_rag_stats_includes_all_fields` | 输出包含 total_chunks/total_pages/synced_pages/failed_pages/last_synced_time | PASSED |
| 10 | `test_rag_page_detail_valid_page_id` | 有效 page_id 返回页面标题/URL/编辑时间/正文 | PASSED |
| 11 | `test_rag_page_detail_invalid_page_id` | 无效 page_id 返回 "页面 xxx 未找到。" | PASSED |

## 覆盖的 AC 映射

| AC | 对应测试 |
|----|----------|
| AC-10-01 (rag_search Tool) | test 1-5 |
| AC-10-02 (rag_sync Tool) | test 6-8 |
| AC-10-03 (rag_stats Tool) | test 9 |
| AC-10-04 (rag_page_detail Tool) | test 10-11 |

## 执行命令

```bash
export TMPDIR=/Users/ray/Workspace/warehouse-profession-system/.tmp
PYTHONPATH=src python3 -m pytest tests/e2e/test_mcp_server.py -v --tb=short
```

## 结果

**11 passed, 0 failed**（1.31s）
