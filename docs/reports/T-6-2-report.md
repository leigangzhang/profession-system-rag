# T-6-2 CLI 开发测试报告

> 任务：实现 Backend.md 第 14 节定义的 Typer CLI，4 个子命令
> 日期：2026-08-11
> 执行者：Codex（子代理 Gibbs）

## 实现文件

- `src/rag_notion_kb/cli.py`（303 行，新建）
- `tests/e2e/test_cli.py`（317 行，新建）

## 关键设计决策

| # | 决策 | 说明 |
|---|---|---|
| 1 | Typer + Rich | Typer 做 CLI 解析，Rich 做格式化输出（Table/Panel/Console） |
| 2 | 辅助工厂函数 | `_build_settings()` / `_build_search_service()` / `_build_mcp_server()` 减少 `sync`/`status`/`serve` 间的重复代码 |
| 3 | API Key 掩码 | `config` 命令对 API Key 显示前 4 + 后 4 字符，防止泄露 |
| 4 | 测试隔离 | 使用 `CliRunner` + `unittest.mock.patch` 隔离 SyncService/SearchService/MCPServer，不依赖真实 Notion/Milvus |

## 架构

```
app = typer.Typer()
├── sync(root, full)     # → SyncService.sync() → Rich Table (added/updated/removed/skipped/failed)
├── status()             # → SearchService.stats() → Rich Table (chunks/pages/synced/failed/last_sync)
├── serve()              # → MCPServer.run() → blocking stdio (KeyboardInterrupt 优雅退出)
└── config()             # → Settings dump → Rich Tables 分节显示，API Keys 掩码

main() → setup_logging() → app()
```

## 测试用例详情

### TestSyncCommand (4 tests)
| # | 用例 | 覆盖内容 | 结果 |
|---|------|----------|------|
| 1 | `test_sync_basic_output` | 默认参数同步，输出包含 added/updated/removed/skipped/failed 计数 | PASSED |
| 2 | `test_sync_with_root_flag` | `--root "id1,id2"` 解析为 `["id1", "id2"]` 并传入 | PASSED |
| 3 | `test_sync_with_full_flag` | `--full` 映射为 `force_full=True` | PASSED |
| 4 | `test_sync_no_root_ids` | 既无 `--root` 也无配置时返回 ConfigError | PASSED |

### TestStatusCommand (2 tests)
| # | 用例 | 覆盖内容 | 结果 |
|---|------|----------|------|
| 5 | `test_status_basic_output` | 正常 KB 统计含 total_chunks/total_pages/synced_pages/failed_pages/last_synced_time | PASSED |
| 6 | `test_status_no_data` | 空 KB 时显示 N/A 而非崩溃 | PASSED |

### TestServeCommand (2 tests)
| # | 用例 | 覆盖内容 | 结果 |
|---|------|----------|------|
| 7 | `test_serve_initializes` | MCPServer 正常初始化，`run()` 被调用 | PASSED |
| 8 | `test_serve_config_error` | 配置错误时 exit_code ≠ 0，输出友好错误信息 | PASSED |

### TestConfigCommand (2 tests)
| # | 用例 | 覆盖内容 | 结果 |
|---|------|----------|------|
| 9 | `test_config_shows_sections` | 输出包含 Notion/Embedding/Reranker/Storage/Chunking/Retrieval/Logging 各节 | PASSED |
| 10 | `test_config_masks_api_keys` | API Keys 不显示完整值，仅显示前 4 + 后 4 字符 | PASSED |

### TestMainEntry (3 tests)
| # | 用例 | 覆盖内容 | 结果 |
|---|------|----------|------|
| 11 | `test_app_is_typer` | `app` 是 Typer 实例，注册了 4 个子命令 | PASSED |
| 12 | `test_main_calls_setup_logging` | `main()` 调用了 `setup_logging()` | PASSED |
| 13 | `test_main_integration` | 集成冒烟测试：子命令注册 + 无参数调用不崩溃 | PASSED |

## 覆盖的 AC 映射

| AC | 对应测试 |
|----|----------|
| AC-11-01 (sync 命令) | test 1-4 |
| AC-11-02 (status 命令) | test 5-6 |
| AC-11-03 (serve/config 命令) | test 7-10 |
| AC-11-04 (配置管理) | test 9-10 |

## 执行命令

```bash
export TMPDIR=/Users/ray/Workspace/warehouse-profession-system/.tmp
PYTHONPATH=src python3 -m pytest tests/e2e/test_cli.py -v --tb=short
```

## 结果

**13 passed, 0 failed**（1.30s）
