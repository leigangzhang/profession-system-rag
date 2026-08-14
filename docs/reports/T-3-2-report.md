# T-3-2 Report: Reranker Service

## 任务信息

- **任务 ID**: T-3-2
- **任务名称**: Reranker Service TDD
- **前置依赖**: T-0-2
- **对应 AC**: AC-7-01 ~ AC-7-03
- **状态**: 完成

## 实现文件

- 实现: /Users/ray/Workspace/warehouse-profession-system/src/rag_notion_kb/embedding/__init__.py
- 实现: /Users/ray/Workspace/warehouse-profession-system/src/rag_notion_kb/embedding/reranker.py
- 配置补充: /Users/ray/Workspace/warehouse-profession-system/src/rag_notion_kb/config.py（RerankerConfig 增加 base_url）
- 测试: /Users/ray/Workspace/warehouse-profession-system/tests/integration/test_reranker.py

## TDD 循环

1. **测试设计**: Mock urllib.request.urlopen，验证重排顺序、图片 candidate 入参、空列表、HTTP 错误转 RetrievalError、无效响应、Authorization Header。
2. **核心实现**: 使用标准库 urllib.request 封装 DashScope-compatible rerank API，用 tenacity 做重试，失败抛出 RetrievalError。
3. **集成验证**: 6/6 测试通过。

## 运行结果

命令: PYTHONPATH=src python3 -m unittest tests/integration/test_reranker.py -v

结果:

```text
Ran 6 tests in 4.086s
OK
```

| 测试用例 | 说明 |
| --- | --- |
| test_rerank_changes_order | 重排后顺序按 relevance_score 降序 |
| test_image_candidates_in_payload | 图片 URL 与 text 均进入请求体 |
| test_empty_candidates | 空列表直接返回空，不调用 API |
| test_http_error_raises_retrieval_error | HTTP 503 转为 RetrievalError |
| test_invalid_response_raises_retrieval_error | 缺失字段触发 RetrievalError |
| test_authorization_header | 正确携带 Bearer Token |

## 关键设计点

- 不依赖 openai 或第三方 HTTP 库，仅使用 urllib.request + tenacity。
- base_url 纳入配置，便于切换环境。
- 响应格式按 output.results[].{index, relevance_score} 解析，与 DashScope 结构一致。

## 真实 API 集成测试

### 测试文件

- `tests/integration/test_reranker_real.py`

### 测试目的

在真实 DashScope 环境上验证 `RerankerService.rerank()`：
- 使用 `DASHSCOPE_API_KEY` 环境变量中的密钥。
- 调用 `https://dashscope.aliyuncs.com/compatible-mode/v1/rerank`。
- 模型为 `qwen3-vl-reranker`。
- 候选文档 3 条，查询为中文 "什么是向量数据库"。
- 断言返回结果数量、索引覆盖全部候选、分数降序且在 `[0, 1]` 区间。

### 执行命令

```bash
PYTHONPATH=src python3 -m pytest tests/integration/test_reranker_real.py -v --tb=short
```

### 执行结果

```text
platform darwin -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
rootdir: /Users/ray/Workspace/warehouse-profession-system
collected 1 item

tests/integration/test_reranker_real.py::test_reranker_real_dashscope SKIPPED [100%]

============================== 1 skipped in 0.04s ==============================
```

**跳过原因**：当前执行环境中未设置 `DASHSCOPE_API_KEY` 环境变量，无法访问 DashScope 真实接口。请在外部终端执行以下命令验证：

```bash
export DASHSCOPE_API_KEY=<your_key>
cd /Users/ray/Workspace/warehouse-profession-system
PYTHONPATH=src python3 -m pytest tests/integration/test_reranker_real.py -v --tb=short
```

### 结论

- Mock 集成测试：6/6 通过。
- 真实 API 集成测试：因缺少环境变量被跳过，实现代码已就绪，配置正确的密钥后即可运行。

## 阻塞说明

T-3-1 Embedding Service 已实现并测试通过。单元/集成测试使用 `unittest.mock` 替换 `openai.OpenAI.embeddings.create`，原因：(1) 真实 DashScope API Key 是机密，测试环境未提供；(2) 沙箱可能限制外网访问。实现本身不依赖特定运行时，只要安装 `openai` 并配置有效 Key 即可调用真实接口。

T-3-2 Reranker Service 已实现并测试通过。使用标准库 `urllib.request` + `tenacity` 封装 DashScope-compatible rerank API，测试中对 HTTP 响应进行 Mock，原因同上（无真实 API Key / 网络限制）。实现已完成，可独立运行。

SearchService（T-5-2）及下游编排（T-6-1 MCP Server、T-6-2 CLI）可以基于抽象的 `VectorStore` / `EmbeddingService` 接口继续开发，并通过最小化 Stub 进行单元测试；但完整的生产级端到端集成仍需要真实 Milvus 实例和有效 DashScope API Key。

当前仍缺失、导致无法直接做真实端到端集成的 Python 包为：`tiktoken`、`langchain-text-splitters`、`notion-client`、`notion-to-md-py`。这些包只影响对应功能的真实运行，不影响已完成的单元/集成测试。
