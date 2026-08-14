# T-4-1 Report: RRF 混合检索与去重

## 任务信息

- **任务 ID**: T-4-1
- **任务名称**: RRF 混合检索与去重 TDD
- **前置依赖**: T-1-3, T-3-1
- **对应 AC**: AC-6-01 ~ AC-6-03
- **状态**: 完成

## 实现文件

- 实现: /Users/ray/Workspace/warehouse-profession-system/src/rag_notion_kb/retrieval/hybrid_search.py
- 测试: /Users/ray/Workspace/warehouse-profession-system/tests/unit/test_hybrid_search.py

## TDD 循环

1. **测试设计**: 在 `tests/unit/test_hybrid_search.py` 中覆盖去重合并、分数排序、空输入、单一来源、不同 k 值影响等场景。
2. **核心实现**: 实现 `reciprocal_rank_fusion(dense_hits, sparse_hits, k=60)`，使用 RRF 公式对两组命中结果合并去重，按融合分降序返回。
3. **单元验证**: 5/5 测试通过，覆盖核心算法与边界条件。

## 运行结果

命令: `PYTHONPATH=src python3 -m pytest tests/unit/test_hybrid_search.py -v`

结果:

```text
5 passed in 0.01s
```

| 测试用例 | 说明 |
| --- | --- |
| test_deduplicates_overlapping_hits | Dense 与 Sparse 结果重叠时去重并合并 |
| test_score_ordering | 等分情况下保持稳定排序 |
| test_empty_inputs | 空输入返回空列表或仅返回有值来源 |
| test_single_source_dominates | 单一来源直接返回 |
| test_k_constant_affects_order | 不同 k 值影响最终排序 |

## 关键设计点

- 使用 `scores[hit.id]` 累加 RRF 分数，天然实现去重。
- `hits_by_id` 保留命中元数据，避免重复构造。
- `k=60` 作为默认平滑常数，与 `Backend.md` 第 11.1 节保持一致。
- 函数为纯计算，无外部依赖，便于单元测试和后续集成。

## 集成阻塞说明

完整集成验证需要真实 Milvus 实例提供 `search_dense` 与 `search_sparse` 结果。当前沙箱无法绑定 `127.0.0.1` 启动 Milvus Lite，该阻塞已记录在 [reports/T-1-2-report.md](/Users/ray/Workspace/warehouse-profession-system/reports/T-1-2-report.md)。

