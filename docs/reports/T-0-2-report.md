# T-0-2 共享模型、异常、配置 + 测试 — 执行报告

## 任务信息

- **任务 ID**：T-0-2
- **任务名称**：共享模型、异常、配置 + 测试
- **所属 Phase**：P0 基础设施
- **状态**：已完成
- **执行时间**：2026-08-10
- **运行环境**：系统 Python 3.14.6（miniconda 已安装但 PyPI/Anaconda DNS 不可达）

## 已完成工作

1. 实现 `src/rag_notion_kb/models.py`
   - `ChunkType` 枚举
   - `ChunkMetadata`、`Chunk`、`ImageDoc`、`EmbeddingItem`、`RankCandidate`
   - `SearchHit`、`SearchResult`、`PageSyncState`、`SyncResult`
2. 实现 `src/rag_notion_kb/exceptions.py`
   - 自定义异常体系（`RagKbError` 及子类）
   - `with_notion_retry` 重试装饰器（基于 `tenacity`）
3. 实现 `src/rag_notion_kb/config.py`
   - 嵌套 Pydantic Settings 配置模型
   - 环境变量前缀 `RAG_KB_`、嵌套分隔符 `__`
   - YAML 配置文件源（`~/.rag_kb/config.yaml`），使用 `pydantic_settings.sources.YamlConfigSettingsSource`
4. 编写单元测试：
   - `tests/unit/test_models.py`：模型构造、字段校验、非法输入、默认值
   - `tests/unit/test_config.py`：默认值、环境变量覆盖、YAML 配置源
   - `tests/unit/test_exceptions.py`：异常继承、基类捕获

## 测试结果

```text
test_default_values (test_config.TestConfig.test_default_values) ... ok
test_env_override (test_config.TestConfig.test_env_override) ... ok
test_yaml_source (test_config.TestConfig.test_yaml_source) ... ok
test_catch_base (test_exceptions.TestExceptions.test_catch_base) ... ok
test_inheritance (test_exceptions.TestExceptions.test_inheritance) ... ok
test_chunk_metadata_validation (test_models.TestModels.test_chunk_metadata_validation) ... ok
test_chunk_type_enum (test_models.TestModels.test_chunk_type_enum) ... ok
test_image_doc (test_models.TestModels.test_image_doc) ... ok
test_invalid_header_level (test_models.TestModels.test_invalid_header_level) ... ok
test_page_sync_state (test_models.TestModels.test_page_sync_state) ... ok
test_search_result (test_models.TestModels.test_search_result) ... ok
test_sync_result_defaults (test_models.TestModels.test_sync_result_defaults) ... ok

----------------------------------------------------------------------
Ran 12 tests in 0.012s

OK
```

## 产出物

- `src/rag_notion_kb/models.py`
- `src/rag_notion_kb/exceptions.py`
- `src/rag_notion_kb/config.py`
- `tests/unit/test_models.py`
- `tests/unit/test_config.py`
- `tests/unit/test_exceptions.py`
- 本报告

## 备注

- 由于 `pytest` 未安装，测试使用标准库 `unittest` 编写与执行；代码本身兼容 `pytest`，待依赖补齐后可无缝切换。
- YAML 配置源测试中通过临时修改 `HOME` 环境变量来避免污染真实家目录。

## 下一步

- 继续执行 T-0-3：日志与可观测性基础设施。
