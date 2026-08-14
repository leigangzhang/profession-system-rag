# T-4-2 Report: 上下文扩展

## 任务信息

- **任务 ID**: T-4-2
- **任务名称**: 上下文扩展 TDD
- **前置依赖**: T-1-3, T-2-2
- **对应 AC**: AC-8-01 ~ AC-8-04
- **状态**: 完成

## 实现文件

- 实现: /Users/ray/Workspace/warehouse-profession-system/src/rag_notion_kb/retrieval/context_expand.py
- 抽象接口: /Users/ray/Workspace/warehouse-profession-system/src/rag_notion_kb/storage/vector_store.py
- 测试: /Users/ray/Workspace/warehouse-profession-system/tests/unit/test_context_expand.py

## TDD 循环

1. **测试设计**: 在 `tests/unit/test_context_expand.py` 中构建内存 Fake `VectorStore`，覆盖扩展到父标题、扩展到根、图片渲染、无兄弟时返回原文、扩展层级受命中层级限制等场景。
2. **核心实现**: 实现 `ContextExpander.expand(hit, expand_to_level)`，解析 `header_path`，按目标层级前缀过滤同页 chunks，拼接为扩展上下文。
3. **单元验证**: 5/5 测试通过。

## 运行结果

命令: `PYTHONPATH=src python3 -m pytest tests/unit/test_context_expand.py -v`

结果:

```text
5 passed in 0.01s
```

| 测试用例 | 说明 |
| --- | --- |
| test_expand_to_parent_heading | 命中 h3 时扩展到 h2，包含同节兄弟 |
| test_expand_to_root | expand_to_level=1 时包含整页同级内容 |
| test_image_rendered | 图片 chunk 渲染为 `![image](url)` |
| test_no_siblings_returns_original | 无兄弟时返回原文 |
| test_expand_to_level_capped_by_hit_level | 扩展层级不超过命中 chunk 自身层级 |

## 关键设计点

- `target_level = min(hit.header_level, expand_to_level)`，防止向上越界。
- `header_path` 解析为正则 `(#+)\s+(.*)`，生成 `[(level, title), ...]` 前缀。
- 依赖 `VectorStore.get_page_chunks(page_id)` 抽象接口，便于 Fake/Stub 测试和真实后端替换。
- 图片 chunk 优先使用 `image_url` 渲染，文本 chunk 保留 `chunk_text`。

## 集成阻塞说明

完整集成验证需要真实 Milvus 实例支持 `get_page_chunks`。当前沙箱无法绑定 `127.0.0.1` 启动 Milvus Lite，该阻塞已记录在 [reports/T-1-2-report.md](/Users/ray/Workspace/warehouse-profession-system/reports/T-1-2-report.md)。

