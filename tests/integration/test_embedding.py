from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

import httpx

from rag_notion_kb.config import EmbeddingConfig
from rag_notion_kb.embedding.qwen_vl import EmbeddingService
from rag_notion_kb.exceptions import ConfigError, EmbeddingError, EmbeddingServiceError
from rag_notion_kb.models import EmbeddingItem


def _make_config(batch_size: int = 2, dimensions: int = 8) -> EmbeddingConfig:
    return EmbeddingConfig(
        api_key="test-key",
        model="qwen3-vl-embedding-2b",
        batch_size=batch_size,
        dimensions=dimensions,
        max_retries=3,
    )


def _mock_response(embeddings: list[list[float]]) -> MagicMock:
    """Build a mock httpx.Response with DashScope-format JSON body."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = {
        "output": {
            "embeddings": [
                {"embedding": emb, "text_index": i}
                for i, emb in enumerate(embeddings)
            ]
        }
    }
    return resp


def _mock_error_response(status_code: int = 400) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = json.dumps({"code": "error", "message": "bad request"})
    return resp


class TestEmbeddingService(unittest.TestCase):
    def test_empty_api_key_raises_config_error(self) -> None:
        with self.assertRaises(ConfigError):
            EmbeddingService(EmbeddingConfig(api_key=""))

    @patch("rag_notion_kb.embedding.qwen_vl.httpx.Client")
    def test_embed_text_items(self, mock_client_cls: MagicMock) -> None:
        client = MagicMock()
        client.post.return_value = _mock_response([[0.1] * 8, [0.2] * 8])
        mock_client_cls.return_value = client

        service = EmbeddingService(_make_config(batch_size=2))
        items = [EmbeddingItem(type="text", text="a"), EmbeddingItem(type="text", text="b")]
        result = service.embed(items)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], [0.1] * 8)
        self.assertEqual(result[1], [0.2] * 8)
        client.post.assert_called_once()

        # Verify native DashScope payload format
        payload = client.post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "qwen3-vl-embedding-2b")
        self.assertEqual(len(payload["input"]["contents"]), 2)
        self.assertEqual(payload["parameters"]["dimension"], 8)

    @patch("rag_notion_kb.embedding.qwen_vl.httpx.Client")
    def test_batch_splitting(self, mock_client_cls: MagicMock) -> None:
        client = MagicMock()
        client.post.side_effect = [
            _mock_response([[0.1] * 8, [0.2] * 8]),
            _mock_response([[0.3] * 8, [0.4] * 8]),
            _mock_response([[0.5] * 8]),
        ]
        mock_client_cls.return_value = client

        service = EmbeddingService(_make_config(batch_size=2))
        items = [EmbeddingItem(type="text", text=f"item-{i}") for i in range(5)]
        result = service.embed(items)

        self.assertEqual(len(result), 5)
        self.assertEqual(client.post.call_count, 3)

    @patch("rag_notion_kb.embedding.qwen_vl.httpx.Client")
    def test_batch_progress_callback(self, mock_client_cls: MagicMock) -> None:
        client = MagicMock()
        client.post.side_effect = [
            _mock_response([[0.1] * 8, [0.2] * 8]),
            _mock_response([[0.3] * 8, [0.4] * 8]),
            _mock_response([[0.5] * 8]),
        ]
        mock_client_cls.return_value = client

        service = EmbeddingService(_make_config(batch_size=2))
        items = [EmbeddingItem(type="text", text=f"item-{i}") for i in range(5)]
        updates: list[tuple[int, int]] = []
        service.embed(
            items,
            progress_callback=lambda completed, total: updates.append((completed, total)),
        )

        self.assertEqual(updates, [(2, 5), (4, 5), (5, 5)])

    @patch("rag_notion_kb.embedding.qwen_vl.httpx.Client")
    def test_image_input_format(self, mock_client_cls: MagicMock) -> None:
        client = MagicMock()
        client.post.return_value = _mock_response([[0.9] * 8])
        mock_client_cls.return_value = client

        service = EmbeddingService(_make_config())
        items = [
            EmbeddingItem(
                type="image_url",
                image_url="https://example.com/img.png",
                text="describe",
            )
        ]
        service.embed(items)

        payload = client.post.call_args.kwargs["json"]
        content = payload["input"]["contents"][0]
        self.assertEqual(content["image"], "https://example.com/img.png")
        self.assertEqual(content["text"], "describe")

    @patch("rag_notion_kb.embedding.qwen_vl.httpx.Client")
    def test_matryoshka_truncation(self, mock_client_cls: MagicMock) -> None:
        client = MagicMock()
        client.post.return_value = _mock_response([[0.1] * 16])
        mock_client_cls.return_value = client

        service = EmbeddingService(_make_config(dimensions=8))
        result = service.embed([EmbeddingItem(type="text", text="x")])

        self.assertEqual(len(result[0]), 8)
        self.assertEqual(result[0], [0.1] * 8)

    @patch("rag_notion_kb.embedding.qwen_vl.httpx.Client")
    def test_api_error_returns_zero_vectors(self, mock_client_cls: MagicMock) -> None:
        client = MagicMock()
        # Simulate HTTP error on post (tenacity will retry 3 times then raise)
        error_resp = _mock_error_response(400)
        http_error = httpx.HTTPStatusError(
            "Bad Request", request=MagicMock(), response=error_resp
        )
        client.post.side_effect = http_error
        mock_client_cls.return_value = client

        service = EmbeddingService(_make_config(batch_size=2, dimensions=4))
        service.config.max_retries = 1  # speed up test
        # Patch retry to only try once
        with patch.object(service, "_embed_batch", side_effect=EmbeddingError("mock error")):
            items = [EmbeddingItem(type="text", text="a"), EmbeddingItem(type="text", text="b")]
            result = service.embed(items)

            self.assertEqual(len(result), 2)
            self.assertEqual(result[0], [0.0] * 4)
            self.assertEqual(result[1], [0.0] * 4)

    @patch("rag_notion_kb.embedding.qwen_vl.httpx.Client")
    def test_retry_then_success(self, mock_client_cls: MagicMock) -> None:
        client = MagicMock()
        error_resp = _mock_error_response(429)
        http_error = httpx.HTTPStatusError(
            "Rate limited", request=MagicMock(), response=error_resp
        )
        client.post.side_effect = [
            http_error,
            _mock_response([[0.7] * 8]),
        ]
        mock_client_cls.return_value = client

        service = EmbeddingService(_make_config())
        result = service.embed([EmbeddingItem(type="text", text="x")])

        self.assertEqual(result[0], [0.7] * 8)
        self.assertEqual(client.post.call_count, 2)

    @patch("rag_notion_kb.embedding.qwen_vl.httpx.Client")
    def test_service_unavailable_propagates_fatal_error(self, mock_client_cls: MagicMock) -> None:
        """Fatal errors (401/403/DNS) must propagate through embed() as EmbeddingServiceError."""
        client = MagicMock()
        error_resp = _mock_error_response(401)
        http_error = httpx.HTTPStatusError(
            "Unauthorized", request=MagicMock(), response=error_resp
        )
        client.post.side_effect = http_error
        mock_client_cls.return_value = client

        service = EmbeddingService(_make_config())
        service.config.max_retries = 1  # speed up
        with self.assertRaises(EmbeddingServiceError):
            service.embed([EmbeddingItem(type="text", text="x")])

    @patch("rag_notion_kb.embedding.qwen_vl.httpx.Client")
    def test_connect_error_propagates(self, mock_client_cls: MagicMock) -> None:
        """DNS/connect failures propagate as EmbeddingServiceError."""
        client = MagicMock()
        client.post.side_effect = httpx.ConnectError("DNS lookup failed")
        mock_client_cls.return_value = client

        service = EmbeddingService(_make_config())
        service.config.max_retries = 1
        with self.assertRaises(EmbeddingServiceError):
            service.embed([EmbeddingItem(type="text", text="x")])

    @patch("rag_notion_kb.embedding.qwen_vl.httpx.Client")
    def test_partial_batch_failure_continues(self, mock_client_cls: MagicMock) -> None:
        """Non-fatal batch errors (400/429) use zero vectors and continue.

        The tenacity decorator retries 3 times, so a per-batch failure consumes
        3 HTTP error responses before the EmbeddingError propagates to embed().
        """
        client = MagicMock()
        error_resp = _mock_error_response(400)
        http_error = httpx.HTTPStatusError(
            "Bad Request", request=MagicMock(), response=error_resp
        )
        client.post.side_effect = [
            _mock_response([[0.1] * 8, [0.2] * 8]),  # batch 0 succeeds
            http_error, http_error, http_error,       # batch 1: 3 retries → EmbeddingError
            _mock_response([[0.5] * 8]),              # batch 2 succeeds
        ]
        mock_client_cls.return_value = client

        service = EmbeddingService(_make_config(batch_size=2, dimensions=4))
        items = [EmbeddingItem(type="text", text=f"item-{i}") for i in range(5)]
        result = service.embed(items)

        self.assertEqual(len(result), 5)
        self.assertEqual(result[0], [0.1] * 4)  # batch 0 ok
        self.assertEqual(result[1], [0.2] * 4)  # batch 0 ok
        self.assertEqual(result[2], [0.0] * 4)  # batch 1 degraded (zero vectors)
        self.assertEqual(result[3], [0.0] * 4)  # batch 1 degraded (zero vectors)
        self.assertEqual(result[4], [0.5] * 4)  # batch 2 ok
        self.assertEqual(client.post.call_count, 5)  # 1 batch0 + 3 batch1 retries + 1 batch2


if __name__ == "__main__":
    unittest.main()
