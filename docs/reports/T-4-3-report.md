# T-4-3 Report: Token 截断

## 任务信息

- **任务 ID**: T-4-3
- **任务名称**: Token 截断 TDD
- **前置依赖**: T-0-2
- **对应 AC**: AC-9-01 ~ AC-9-03
- **状态**: 完成

## 实现文件

- 实现: /Users/ray/Workspace/warehouse-profession-system/src/rag_notion_kb/retrieval/truncate.py
- 测试: /Users/ray/Workspace/warehouse-profession-system/tests/unit/test_truncate.py

## TDD 循环

1. **测试设计**: 在 `tests/unit/test_truncate.py` 中覆盖短文本不截断、长文本截断、首尾保留、中文文本、CJK token 计数、tiktoken 精确计数（网络可用时）、网络受限 fallback。
2. **核心实现**: 实现 `TokenTruncator`，优先使用 `tiktoken` 精确计数；未安装或无法下载编码数据时回退到字符级计数；超限时保留前 40% 和后 40%，中间插入省略标记，并在段落边界处截断。
3. **单元验证**: 6/8 测试通过，2 个 tiktoken 精确计数测试因沙箱 DNS 阻塞被跳过。

## 运行结果

命令: `PYTHONPATH=src python3 -m pytest tests/unit/test_truncate.py -v --tb=short`

结果:

```text
6 passed, 2 skipped in 0.03s
```

| 测试用例 | 状态 | 说明 |
| --- | --- | --- |
| test_short_text_unchanged | passed | 未超限时原文返回 |
| test_long_text_truncated | passed | 超长文本截断并含省略标记，预算受控 |
| test_head_and_tail_preserved | passed | 保留头部和尾部，去除中部 |
| test_chinese_text | passed | 中文长文本截断正确 |
| test_count_cjk_tokens | passed | CJK token 计数符合预期 |
| test_tiktoken_encoder_loaded | skipped | tiktoken 编码数据因网络/DNS 无法下载 |
| test_count_english_known_tokens | skipped | 同上，需真实 cl100k_base 数据 |
| test_encoder_fallback_when_network_blocked | passed | 网络受限时自动降级为字符级计数 |

## 关键设计点

- 优先使用 `tiktoken` 精确计数；未安装或无法下载编码数据时自动降级为字符数估算，保证环境可移植性。
- `_load_encoder` 捕获 `ImportError` 以及网络/IO 异常，构造函数不会崩溃。
- 截断策略保留头尾各 40%，中间用 `(...部分内容省略...)` 衔接。
- `_break_at_boundary` 在段落边界优先截断，提升可读性。
- 默认编码 `cl100k_base`，与 `Backend.md` 第 11.3 节一致。

## tiktoken 可用性说明

`tiktoken` 包已安装，但首次使用需要联网下载 `cl100k_base.tiktoken` 编码数据。当前沙箱无法解析 `openaipublic.blob.core.windows.net`，导致 `tiktoken.get_encoding("cl100k_base")` 抛 `ConnectionError`。因此相关精确 token 测试被标记为 `skipped`，组件本身通过字符级 fallback 保持可用。

### 手动恢复 tiktoken 精确计数

在沙箱外（或网络可用环境）执行以下命令，将编码文件缓存到 tiktoken 默认缓存目录：

```bash
python3 - <<'PY'
import tiktoken
# 成功下载后会写入 $TMPDIR/data-gym-cache/ 或 $TIKTOKEN_CACHE_DIR
enc = tiktoken.get_encoding("cl100k_base")
print("loaded", enc.name)
PY
```

若需指定固定缓存目录：

```bash
export TIKTOKEN_CACHE_DIR="$HOME/.tiktoken_cache"
python3 -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"
```

缓存成功后，在当前项目运行：

```bash
cd /Users/ray/Workspace/warehouse-profession-system
PYTHONPATH=src python3 -m pytest tests/unit/test_truncate.py -v
```

预期 `test_tiktoken_encoder_loaded` 与 `test_count_english_known_tokens` 由 skipped 变为 passed。

## 集成阻塞说明

`TokenTruncator` 本身无外部运行时依赖，可在任意环境独立运行。下游 `SearchService` 的完整集成验证需要真实 Milvus 实例。当前沙箱无法绑定 `127.0.0.1` 启动 Milvus Lite，该阻塞已记录在 [reports/T-1-2-report.md](/Users/ray/Workspace/warehouse-profession-system/reports/T-1-2-report.md)。
