# T-0-3 日志与可观测性基础设施 — 执行报告

## 任务信息

- **任务 ID**：T-0-3
- **任务名称**：日志与可观测性基础设施
- **所属 Phase**：P0 基础设施
- **状态**：已完成
- **执行时间**：2026-08-10
- **运行环境**：系统 Python 3.14.6

## 已完成工作

1. 实现 `src/rag_notion_kb/logging_setup.py`
   - `setup_logging(level, fmt, stream)`：配置根 logger
   - `JsonFormatter`：将日志输出为 JSONL，字段包括 `timestamp`、`level`、`module`、`message`、`page_id`
   - text 格式用于本地开发调试
   - 支持通过 `extra={"page_id": "..."}` 注入业务上下文
2. 编写单元测试 `tests/unit/test_logging_setup.py`
   - JSON 格式解析与字段校验
   - text 格式内容校验
   - 重复调用 `setup_logging` 会替换旧 handler，避免重复输出
   - `page_id` extra 字段注入

## 测试结果

```text
test_json_format (test_logging_setup.TestLoggingSetup.test_json_format) ... ok
test_page_id_extra (test_logging_setup.TestLoggingSetup.test_page_id_extra) ... ok
test_setup_replaces_handlers (test_logging_setup.TestLoggingSetup.test_setup_replaces_handlers) ... ok
test_text_format (test_logging_setup.TestLoggingSetup.test_text_format) ... ok
...
Ran 16 tests in 0.012s
OK
```

（16 个测试中 4 个为日志相关，其余 12 个来自 T-0-2。）

## 产出物

- `src/rag_notion_kb/logging_setup.py`
- `tests/unit/test_logging_setup.py`
- 本报告

## 备注

- 为方便单元测试，`setup_logging` 增加了可选 `stream` 参数；生产调用不传入时默认使用 `sys.stdout`。
- JSON 日志中 `timestamp` 使用 UTC ISO 8601 格式。

## 下一步

- 继续执行 T-1-1：SQLite Sync State Store TDD。
