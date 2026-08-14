# T-1-3 任务执行报告

## 任务信息

- 任务编号：T-1-3
- 任务名称：Milvus page-level write/delete/search TDD
- 需求来源：Backend.md §8.2 / Tasks.md DAG
- 验收标准：AC-5-01 ~ AC-5-04
- 依赖任务：T-0-2 共享模型、T-1-1 同步状态、T-1-2 Milvus Lite 本地运行时
- 执行时间：2026-08-11
- 执行结果：完成

## 实现文件

继承并扩展了既有实现，未新增独立模块文件：

- src/rag_notion_kb/storage/milvus_store.py
- src/rag_notion_kb/storage/schema.py
- src/rag_notion_kb/storage/vector_store.py

本次新增的测试文件：

- tests/integration/test_milvus_store_local.py

## TDD 执行过程

1. 红：编写真实集成测试，使用临时 Milvus Lite DB 通过 MilvusStore 调用 init_collection、upsert_page、search_dense、search_sparse、get_page_chunks、stats、delete_by_page_id。直接运行后，test_stats_and_delete_by_page_id 失败：
   AttributeError: 'MilvusClient' object has no attribute 'num_entities'
2. 绿：修复 MilvusStore.stats()，将 self.client.num_entities(...) 改为 self.client.get_collection_stats(...) 并读取 row_count。
3. 重构：同步更新 tests/unit/test_milvus_store.py 中的 Mock，由 num_entities 改为 get_collection_stats，保持单元测试与实现一致。

## 测试结果

执行命令（需提升沙箱权限以允许 Milvus Lite 绑定 127.0.0.1）：

    PYTHONPATH=src python3 -m pytest \
      tests/unit/test_milvus_store.py \
      tests/unit/test_milvus_schema.py \
      tests/unit/test_milvus_filters.py \
      tests/integration/test_milvus_store_local.py \
      -v --tb=short

结果：

    ============================== 22 passed in 0.82s ===============================

用例统计：

- TestMilvusStore：6 passed
- TestMilvusSchema：5 passed
- TestMilvusFilters：5 passed
- TestMilvusStoreLocal：5 passed
- 合计：22 passed

## 关键设计点

- 页面级原子替换：upsert_page 内部先调用 delete_by_page_id(page_id)，再批量 insert，保证同一页面多次同步不会产生重复向量。
- Schema 对齐：COLLECTION_SCHEMA 包含 12 个字段，sparse_vector 由 chunk_text 通过 BM25 Function 自动生成，无需在插入时提供。
- 过滤表达式：_build_filter_expr 支持 page_ids、header_level、edited_after 三类条件，可组合使用。
- 统一返回模型：所有查询方法均返回 SearchHit，检索层可直接消费，无需再次适配 Milvus 原始返回结构。
- 图片与普通文本统一处理：ImageDoc 与 Chunk 在写入时通过 _build_rows 合并为同一行格式，chunk_type 标记为 image，image_url 字段保留。

## 阻塞与风险

- 无当前任务级阻塞。
- 下游 T-5-2 SearchService 集成测试仍依赖真实 Milvus Lite，需在可绑定 127.0.0.1 的环境中执行。

## 下一步

按 DAG 继续执行 T-2-1 Notion Client TDD、T-2-2 Markdown 分块 TDD、T-2-3 图片提取 TDD。
