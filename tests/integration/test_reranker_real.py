
from __future__ import annotations

import os

import pytest

from rag_notion_kb.config import RerankerConfig
from rag_notion_kb.embedding.reranker import RerankerService
from rag_notion_kb.models import RankCandidate


@pytest.mark.skipif(
    not os.environ.get("DASHSCOPE_API_KEY"),
    reason="DASHSCOPE_API_KEY is not set",
)
def test_reranker_real_dashscope() -> None:
    config = RerankerConfig(api_key=os.environ["DASHSCOPE_API_KEY"])
    # Uses default base_url and model from RerankerConfig.
    service = RerankerService(config)

    query = "什么是向量数据库"
    candidates = [
        RankCandidate(text="向量数据库是一种专门用于存储和检索向量嵌入的数据库。"),
        RankCandidate(text="关系型数据库使用表格来存储结构化数据。"),
        RankCandidate(text="Milvus 是一款开源的向量数据库，支持近似最近邻搜索。"),
    ]

    results = service.rerank(query, candidates)

    assert isinstance(results, list)
    assert len(results) == len(candidates)

    scores = [score for _, score in results]
    assert scores == sorted(scores, reverse=True), "scores should be sorted descending"
    assert all(0.0 <= score <= 1.0 for score in scores), "scores should be in [0, 1]"

    indices = [idx for idx, _ in results]
    assert sorted(indices) == list(range(len(candidates))), "indices should cover all candidates"
