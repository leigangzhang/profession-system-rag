from __future__ import annotations

from rag_notion_kb.models import SearchHit


def reciprocal_rank_fusion_scores(
    dense_hits: list[SearchHit],
    sparse_hits: list[SearchHit],
    k: int = 60,
) -> dict[int, float]:
    """Return the RRF score for every hit without mutating input hits."""
    scores: dict[int, float] = {}

    for rank, hit in enumerate(dense_hits, start=1):
        scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (k + rank)

    for rank, hit in enumerate(sparse_hits, start=1):
        scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (k + rank)

    return scores


def reciprocal_rank_fusion(
    dense_hits: list[SearchHit],
    sparse_hits: list[SearchHit],
    k: int = 60,
) -> list[SearchHit]:
    """Merge dense and sparse hits with Reciprocal Rank Fusion.

    Args:
        dense_hits: Results from dense vector search, ordered by similarity.
        sparse_hits: Results from BM25 keyword search, ordered by relevance.
        k: RRF smoothing constant.

    Returns:
        Deduplicated merged hits ordered by fused RRF score descending.
    """
    scores = reciprocal_rank_fusion_scores(dense_hits, sparse_hits, k=k)
    hits_by_id: dict[int, SearchHit] = {}

    for hit in dense_hits:
        hits_by_id[hit.id] = hit
    for hit in sparse_hits:
        hits_by_id[hit.id] = hit

    sorted_ids = sorted(scores, key=scores.get, reverse=True)
    return [hits_by_id[hit_id] for hit_id in sorted_ids]


def weighted_fusion(
    dense_hits: list[SearchHit],
    sparse_hits: list[SearchHit],
    dense_weight: float,
    sparse_weight: float,
) -> list[SearchHit]:
    """Merge dense and sparse hits with linear weighted fusion.

    The fused score is ``dense_weight * dense_score + sparse_weight *
    sparse_score`` when both sources match the same hit. A hit from only one
    active source keeps that source's original score, avoiding an extra
    weight penalty for single-source candidates.
    """
    scores: dict[int, float] = {}
    hits_by_id: dict[int, SearchHit] = {}
    dense_scores: dict[int, float] = {}
    sparse_scores: dict[int, float] = {}
    if dense_weight > 0:
        for hit in dense_hits:
            dense_scores[hit.id] = hit.score
            hits_by_id[hit.id] = hit
    if sparse_weight > 0:
        for hit in sparse_hits:
            sparse_scores[hit.id] = hit.score
            hits_by_id[hit.id] = hit

    for hit_id, hit in hits_by_id.items():
        has_dense = hit_id in dense_scores
        has_sparse = hit_id in sparse_scores
        if has_dense and has_sparse:
            scores[hit_id] = (
                dense_weight * dense_scores[hit_id]
                + sparse_weight * sparse_scores[hit_id]
            )
        elif has_dense:
            scores[hit_id] = dense_scores[hit_id]
        else:
            scores[hit_id] = sparse_scores[hit_id]

    sorted_ids = sorted(scores, key=scores.get, reverse=True)
    fused_hits = []
    for hit_id in sorted_ids:
        hit = hits_by_id[hit_id]
        hit.score = scores[hit_id]
        fused_hits.append(hit)
    return fused_hits
