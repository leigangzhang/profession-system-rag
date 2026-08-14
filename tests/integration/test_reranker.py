from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

import httpx

from rag_notion_kb.config import RerankerConfig
from rag_notion_kb.embedding.reranker import RerankerService
from rag_notion_kb.exceptions import ConfigError, RetrievalError
from rag_notion_kb.models import RankCandidate


def _make_config() -> RerankerConfig:
    return RerankerConfig(api_key="test-key", model="test-reranker", max_retries=2)


def _mock_response(results: list[dict]) -> MagicMock:
    """Build a mock httpx.Response with DashScope-format JSON body."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = {"output": {"results": results}}
    return resp


def _mock_error_response(status_code: int = 500) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = json.dumps({"code": "error", "message": "service unavailable"})
    return resp


class TestRerankerService(unittest.TestCase):
    def test_empty_api_key_raises_config_error(self) -> None:
        with self.assertRaises(ConfigError):
            RerankerService(RerankerConfig(api_key=""))

    @patch("rag_notion_kb.embedding.reranker.httpx.Client")
    def test_rerank_changes_order(self, mock_client_cls: MagicMock) -> None:
        client = MagicMock()
        client.post.return_value = _mock_response(
            [
                {"index": 2, "relevance_score": 0.95},
                {"index": 0, "relevance_score": 0.80},
                {"index": 1, "relevance_score": 0.60},
            ]
        )
        mock_client_cls.return_value = client

        service = RerankerService(_make_config())
        candidates = [
            RankCandidate(text="first"),
            RankCandidate(text="second"),
            RankCandidate(text="third"),
        ]
        result = service.rerank("query", candidates)
        self.assertEqual(len(result), 3)
        indices = [idx for idx, _ in result]
        self.assertEqual(indices, [2, 0, 1])
        scores = [score for _, score in result]
        self.assertEqual(scores, [0.95, 0.80, 0.60])

    @patch("rag_notion_kb.embedding.reranker.httpx.Client")
    def test_image_candidates_in_payload(self, mock_client_cls: MagicMock) -> None:
        client = MagicMock()
        client.post.return_value = _mock_response([{"index": 0, "relevance_score": 0.99}])
        mock_client_cls.return_value = client

        service = RerankerService(_make_config())
        candidates = [
            RankCandidate(text="diagram", image_url="https://example.com/diagram.png"),
        ]
        service.rerank("describe image", candidates)

        payload = client.post.call_args.kwargs["json"]
        self.assertEqual(payload["input"]["query"], {"text": "describe image"})
        self.assertEqual(payload["input"]["documents"][0]["image"], "https://example.com/diagram.png")
        self.assertEqual(payload["input"]["documents"][0]["text"], "diagram")
        self.assertEqual(payload["parameters"]["top_n"], 1)

    @patch("rag_notion_kb.embedding.reranker.httpx.Client")
    def test_empty_candidates(self, mock_client_cls: MagicMock) -> None:
        service = RerankerService(_make_config())
        result = service.rerank("query", [])
        self.assertEqual(result, [])
        client = mock_client_cls.return_value
        client.post.assert_not_called()

    @patch("rag_notion_kb.embedding.reranker.httpx.Client")
    def test_http_error_raises_retrieval_error(self, mock_client_cls: MagicMock) -> None:
        client = MagicMock()
        error_resp = _mock_error_response(503)
        client.post.side_effect = httpx.HTTPStatusError(
            "Service Unavailable",
            request=MagicMock(),
            response=error_resp,
        )
        mock_client_cls.return_value = client

        service = RerankerService(_make_config())
        with self.assertRaises(RetrievalError):
            service.rerank("query", [RankCandidate(text="doc")])

    @patch("rag_notion_kb.embedding.reranker.httpx.Client")
    def test_invalid_response_raises_retrieval_error(self, mock_client_cls: MagicMock) -> None:
        client = MagicMock()
        client.post.return_value = _mock_response([{"bad": "item"}])
        mock_client_cls.return_value = client

        service = RerankerService(_make_config())
        with self.assertRaises(RetrievalError):
            service.rerank("query", [RankCandidate(text="doc")])

    @patch("rag_notion_kb.embedding.reranker.httpx.Client")
    def test_authorization_header(self, mock_client_cls: MagicMock) -> None:
        client = MagicMock()
        client.post.return_value = _mock_response([{"index": 0, "relevance_score": 0.5}])
        mock_client_cls.return_value = client

        service = RerankerService(_make_config())
        service.rerank("query", [RankCandidate(text="doc")])

        headers = mock_client_cls.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer test-key")

    @patch("rag_notion_kb.embedding.reranker.httpx.Client")
    def test_connect_error_raises_retrieval_error(self, mock_client_cls: MagicMock) -> None:
        client = MagicMock()
        client.post.side_effect = httpx.ConnectError("DNS lookup failed")
        mock_client_cls.return_value = client

        service = RerankerService(_make_config())
        with self.assertRaises(RetrievalError):
            service.rerank("query", [RankCandidate(text="doc")])

    @patch("rag_notion_kb.embedding.reranker.httpx.Client")
    def test_close_closes_client(self, mock_client_cls: MagicMock) -> None:
        client = MagicMock()
        mock_client_cls.return_value = client

        service = RerankerService(_make_config())
        service.close()
        client.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
