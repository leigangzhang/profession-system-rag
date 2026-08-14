from __future__ import annotations

from pymilvus import CollectionSchema, DataType, FieldSchema, Function, FunctionType

DENSE_DIM = 2048

FIELDS = [
    FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
    FieldSchema(
        name="chunk_text",
        dtype=DataType.VARCHAR,
        max_length=65535,
        enable_analyzer=True,
        enable_match=True,
    ),
    FieldSchema(name="dense_vector", dtype=DataType.FLOAT_VECTOR, dim=DENSE_DIM),
    FieldSchema(name="sparse_vector", dtype=DataType.SPARSE_FLOAT_VECTOR),
    FieldSchema(name="page_id", dtype=DataType.VARCHAR, max_length=64),
    FieldSchema(name="page_title", dtype=DataType.VARCHAR, max_length=512),
    FieldSchema(name="page_url", dtype=DataType.VARCHAR, max_length=2048),
    FieldSchema(name="header_path", dtype=DataType.VARCHAR, max_length=2048),
    FieldSchema(name="header_level", dtype=DataType.INT8),
    FieldSchema(name="last_edited_time", dtype=DataType.VARCHAR, max_length=32),
    FieldSchema(name="chunk_index", dtype=DataType.INT32),
    FieldSchema(name="chunk_type", dtype=DataType.VARCHAR, max_length=16),
    FieldSchema(
        name="image_url",
        dtype=DataType.VARCHAR,
        max_length=65535,
        nullable=True,
    ),
]

BM25_FUNCTION = Function(
    name="chunk_text_bm25_emb",
    function_type=FunctionType.BM25,
    input_field_names="chunk_text",
    output_field_names="sparse_vector",
)

COLLECTION_SCHEMA = CollectionSchema(
    FIELDS,
    "RAG Notion KB chunks",
    functions=[BM25_FUNCTION],
    enable_dynamic_field=False,
)
