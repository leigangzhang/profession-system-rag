from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from rag_notion_kb.config import SummarizationConfig
from rag_notion_kb.exceptions import SummarizationError
from rag_notion_kb.services.summarization import SummarizationService


def _config(**overrides: object) -> SummarizationConfig:
    values: dict[str, object] = {
        "api_key": "sk-test",
        "model": "deepseek-v4-flash",
    }
    values.update(overrides)
    return SummarizationConfig(**values)


def test_empty_api_key_raises_when_summarizing() -> None:
    with patch("rag_notion_kb.services.summarization.httpx.Client"):
        service = SummarizationService(_config(api_key=""))
        try:
            with pytest.raises(SummarizationError, match="not configured"):
                service.summarize("query", [("passage", "source")])
        finally:
            service.close()


def test_summarize_builds_request_and_returns_content() -> None:
    response = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": "summary text"}}]}

    with patch(
        "rag_notion_kb.services.summarization.httpx.Client"
    ) as client_cls:
        client = client_cls.return_value
        client.post.return_value = response
        service = SummarizationService(_config())
        try:
            content = service.summarize(
                "what is this?",
                [("first passage", "Page A")],
            )
        finally:
            service.close()

    assert content == "summary text"
    call_kwargs = client.post.call_args.kwargs
    assert client.post.call_args.args == ("/chat/completions",)
    assert call_kwargs["json"]["model"] == "deepseek-v4-flash"
    assert call_kwargs["json"]["messages"][0]["role"] == "system"
    assert call_kwargs["json"]["messages"][1]["content"].startswith("Query: what is this?")
    assert "first passage" in call_kwargs["json"]["messages"][1]["content"]


def test_connection_error_maps_to_summarization_error() -> None:
    service = SummarizationService(_config())
    try:
        with patch.object(
            service,
            "_call_api",
            side_effect=httpx.ConnectError("offline"),
        ), pytest.raises(SummarizationError, match="request failed"):
            service.summarize("query", [("passage", "source")])
    finally:
        service.close()


def test_invalid_response_maps_to_summarization_error() -> None:
    service = SummarizationService(_config())
    try:
        with patch.object(service, "_call_api", return_value={}), pytest.raises(
            SummarizationError, match="response structure"
        ):
            service.summarize("query", [("passage", "source")])
    finally:
        service.close()
