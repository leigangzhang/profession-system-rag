# T-1-2 Report: Milvus Collection Schema & Init

## 任务信息

- **任务 ID**: T-1-2
- **任务名称**: Milvus Collection Schema 与初始化 TDD
- **前置依赖**: T-0-2
- **对应 AC**: AC-5-01
- **状态**: 完成

## 环境说明

- `pymilvus==3.0.1` 与 `milvus_lite==3.2.0` 已安装并可导入。
- 默认 Codex 沙箱仍禁止绑定 `127.0.0.1`，因此本地 Milvus Lite 测试需要在**沙箱外**或**提权执行**下运行。

## 实现文件

- Schema: /Users/ray/Workspace/warehouse-profession-system/src/rag_notion_kb/storage/schema.py
- Store: /Users/ray/Workspace/warehouse-profession-system/src/rag_notion_kb/storage/milvus_store.py
- 抽象接口: /Users/ray/Workspace/warehouse-profession-system/src/rag_notion_kb/storage/vector_store.py
- 单元测试: /Users/ray/Workspace/warehouse-profession-system/tests/unit/test_milvus_schema.py
- 单元测试: /Users/ray/Workspace/warehouse-profession-system/tests/unit/test_milvus_store.py
- 单元测试: /Users/ray/Workspace/warehouse-profession-system/tests/unit/test_milvus_filters.py
- 真实集成测试: /Users/ray/Workspace/warehouse-profession-system/tests/integration/test_milvus_local.py

## 真实集成测试结果

```bash
PYTHONPATH=src python3 -m pytest tests/integration/test_milvus_local.py -v --tb=short
```

结果:

```text
4 passed in 0.88s
```

| 测试用例 | 说明 |
| --- | --- |
| test_create_collection | 创建 Collection，验证 13 个字段、BM25 Function、`dense_vector` dim=2048 |
| test_insert_and_dense_search | 插入 2 条记录，dense 检索命中 top-1 |
| test_sparse_search | BM25 sparse 检索命中含关键词记录 |
| test_delete_and_cleanup | 删除全部记录并清理 Collection |

## 单元测试结果

```bash
PYTHONPATH=src python3 -m pytest tests/unit/test_milvus_schema.py tests/unit/test_milvus_store.py tests/unit/test_milvus_filters.py -v
```

结果:

```text
17 passed
```

## 关键设计点

- Schema 严格对齐 Backend.md，包含 13 个字段和 BM25 Function。
- `MilvusStore` 实现 `VectorStore` 抽象接口，支持 init/upsert/delete/dense/sparse/query/stats。
- 页面级写入采用“先删后插”实现原子替换。
- `_build_filter_expr` 支持 `page_ids`、`header_level`、`edited_after`。

## 运行方式

在 Codex 沙箱内，本地 Milvus Lite 集成测试会被自动跳过；请在沙箱外执行：

```bash
cd /Users/ray/Workspace/warehouse-profession-system
PYTHONPATH=src python3 -m pytest tests/integration/test_milvus_local.py -v --tb=short
```

## 下游影响

T-1-2 已解除阻塞，T-1-3 及后续依赖真实 Milvus 的集成任务可以继续执行。
