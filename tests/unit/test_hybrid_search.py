from __future__ import annotations

import unittest

from rag_notion_kb.models import ChunkMetadata, ChunkType, SearchHit
from rag_notion_kb.retrieval.hybrid_search import (
    reciprocal_rank_fusion,
    weighted_fusion,
)


def _hit(hit_id: int, score: float) -> SearchHit:
    return SearchHit(
        id=hit_id,
        chunk_text=f"chunk-{hit_id}",
        score=score,
        metadata=ChunkMetadata(
            page_id="page-1",
            page_title="Test",
            page_url="https://notion.so/page-1",
            header_path="# Test",
            header_level=1,
            last_edited_time="2026-08-10T10:00:00Z",
            chunk_index=hit_id,
            chunk_type=ChunkType.TEXT,
        ),
    )


class TestReciprocalRankFusion(unittest.TestCase):
    def test_deduplicates_overlapping_hits(self) -> None:
        dense = [_hit(1, 0.9), _hit(2, 0.8), _hit(3, 0.7)]
        sparse = [_hit(3, 0.6), _hit(4, 0.5)]
        merged = reciprocal_rank_fusion(dense, sparse, k=60)
        ids = [h.id for h in merged]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), {1, 2, 3, 4})

    def test_score_ordering(self) -> None:
        # Both hits have equal fused scores; stable sort preserves dense order.
        dense = [_hit(1, 0.9), _hit(2, 0.8)]
        sparse = [_hit(2, 0.7), _hit(1, 0.6)]
        merged = reciprocal_rank_fusion(dense, sparse, k=60)
        ids = [h.id for h in merged]
        self.assertEqual(ids, [1, 2])

    def test_empty_inputs(self) -> None:
        self.assertEqual(reciprocal_rank_fusion([], []), [])
        self.assertEqual(reciprocal_rank_fusion([_hit(1, 0.5)], []), [_hit(1, 0.5)])

    def test_single_source_dominates(self) -> None:
        dense = [_hit(1, 0.9)]
        sparse = []
        merged = reciprocal_rank_fusion(dense, sparse, k=60)
        self.assertEqual([h.id for h in merged], [1])

    def test_k_constant_affects_order(self) -> None:
        # Both candidates receive equal RRF weight with k=1; order is stable.
        dense = [_hit(1, 0.0)]
        sparse = [_hit(2, 0.0)]
        merged = reciprocal_rank_fusion(dense, sparse, k=1)
        self.assertEqual([h.id for h in merged], [1, 2])


class TestWeightedFusion(unittest.TestCase):
    def test_linear_score_formula(self) -> None:
        dense = [_hit(1, 0.8), _hit(2, 0.6)]
        sparse = [_hit(2, 0.9), _hit(3, 0.5)]

        merged = weighted_fusion(dense, sparse, dense_weight=0.3, sparse_weight=0.7)

        self.assertEqual([hit.id for hit in merged], [2, 1, 3])
        self.assertAlmostEqual(merged[0].score, 0.3 * 0.6 + 0.7 * 0.9)
        self.assertAlmostEqual(merged[1].score, 0.8)
        self.assertAlmostEqual(merged[2].score, 0.5)

    def test_pure_dense_matches_dense_order(self) -> None:
        dense = [_hit(1, 0.9), _hit(2, 0.8)]
        sparse = [_hit(2, 0.1), _hit(1, 0.05)]

        merged = weighted_fusion(dense, sparse, dense_weight=1.0, sparse_weight=0.0)

        self.assertEqual([hit.id for hit in merged], [1, 2])
        self.assertAlmostEqual(merged[0].score, 0.9)

    def test_zero_weight_source_is_excluded(self) -> None:
        dense = [_hit(1, 0.9)]
        sparse = [_hit(2, -2.5)]

        merged = weighted_fusion(dense, sparse, dense_weight=0.0, sparse_weight=1.0)

        self.assertEqual([hit.id for hit in merged], [2])
        self.assertAlmostEqual(merged[0].score, -2.5)

    def test_single_source_score_is_not_scaled_by_weight(self) -> None:
        dense = [_hit(1, 0.8)]
        sparse = [_hit(2, 0.6)]

        merged = weighted_fusion(dense, sparse, dense_weight=0.2, sparse_weight=0.8)

        self.assertEqual([hit.id for hit in merged], [1, 2])
        self.assertAlmostEqual(merged[0].score, 0.8)
        self.assertAlmostEqual(merged[1].score, 0.6)

    def test_empty_inputs(self) -> None:
        self.assertEqual(weighted_fusion([], [], 0.5, 0.5), [])


if __name__ == "__main__":
    unittest.main()
