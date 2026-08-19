from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from pydantic_settings.sources import YamlConfigSettingsSource


class NotionConfig(BaseModel):
    token: str = ""
    root_page_ids: list[str] = Field(default_factory=list)


class EmbeddingConfig(BaseModel):
    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"
    model: str = "qwen3-vl-embedding"
    dimensions: int = 2048
    batch_size: int = 8
    max_retries: int = 3


class RerankerConfig(BaseModel):
    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
    model: str = "qwen3-vl-rerank"
    max_retries: int = 3


class SummarizationConfig(BaseModel):
    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-v4-flash"
    max_retries: int = 3
    timeout_seconds: int = 60
    prompt: str = (
        "You are a knowledge-base summarization assistant. "
        "Deduplicate the supplied retrieved passages, then write a single "
        "structured Markdown summary with the key facts and a clear conclusion. "
        "Keep source citations. Do not invent information. If evidence is "
        "missing or conflicting, say so explicitly."
    )


class StorageConfig(BaseModel):
    data_dir: str = "~/.rag_kb"


class ChunkingConfig(BaseModel):
    header_levels: list[int] = Field(default_factory=lambda: [1, 2, 3])
    max_chunk_size: int = 2048
    min_chunk_size: int = 256
    preserve_tables: bool = True
    preserve_code_blocks: bool = True
    image_context_window: int = 200
    image_context_max_chars: int = Field(default=800, gt=0)


class VectorizeConfig(BaseModel):
    max_concurrent: int = 3
    poll_interval_seconds: int = 2


class RetrievalConfig(BaseModel):
    default_top_k: int = 10
    default_expand_to_level: int = 2
    default_max_tokens: int = 4000
    dense_limit: int = 50
    sparse_limit: int = 50
    rrf_k: int = 60


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "json"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_prefix="RAG_KB_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    notion: NotionConfig = Field(default_factory=NotionConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    reranker: RerankerConfig = Field(default_factory=RerankerConfig)
    summarization: SummarizationConfig = Field(default_factory=SummarizationConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    vectorize: VectorizeConfig = Field(default_factory=VectorizeConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        yaml_path = Path.home() / ".rag_kb" / "config.yaml"
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=yaml_path),
            file_secret_settings,
        )
