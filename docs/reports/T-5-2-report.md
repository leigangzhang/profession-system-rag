# T-5-2 Search Service 开发测试报告

> 任务：实现 Backend.md 第 12.2 节定义的 `SearchService`
> 日期：2026-08-11
> 执行者：Codex

## 实现文件

- `src/rag_notion_kb/services/__init__.py`
- `src/rag_notion_kb/services/search_service.py`

## 测试文件

- `tests/integration/test_search_service.py`

## 实现要点

1. **完整检索 Pipeline**：
   - Query Embedding → Dense ANN 检索 → BM25 Sparse 检索 → RRF 合并 → ReRanker → 上下文扩展 → Token 截断。
2. **参数缺省补齐**：`top_k`、`expand_to_level`、`max_tokens` 使用 `Settings.retrieval` 默认值。
3. **过滤条件**：透传 `page_ids` / `header_level` / `edited_after` 给 Milvus。
4. **ReRanker 降级**：`RetrievalError` 时回退到 RRF 排序结果。
5. **附加接口**：
   - `stats()`：返回总 chunks、总页面数、同步/失败页面数、最近同步时间。
   - `get_page_detail()`：按 `chunk_index` 拼接页面所有 chunks，返回完整文本与首个 chunk 元数据。

## 测试覆盖

| 用例 | 说明 | 结果 |
| --- | --- | --- |
| test_search_returns_ranked_results | 返回排序后的 `SearchResult` 列表 | PASSED |
| test_search_with_page_filter | `page_ids` 过滤仅返回指定页面 | PASSED |
| test_reranker_failure_falls_back_to_rrf | ReRanker 抛 `RetrievalError` 时降级为 RRF | PASSED |
| test_stats_returns_counts | stats 返回 chunks / pages / synced / failed / last_synced_time | PASSED |
| test_get_page_detail_reconstructs_text | 按 chunk_index 拼接页面文本 | PASSED |
| test_get_page_detail_missing_page | 不存在的页面返回 None | PASSED |

## 执行命令

```bash
export TMPDIR=/Users/ray/Workspace/warehouse-profession-system/.tmp
PYTHONPATH=src python3 -m pytest tests/integration/test_search_service.py -v
```

## 结果

6 passed, 0 failed
