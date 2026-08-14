from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from PIL import Image

from rag_notion_kb.utils.image_cache import (
    cleanup_image_cache,
    download_image,
    local_image_url,
    pack_image_url,
    remote_image_url,
)


def test_download_deduplicates_identical_content_across_urls(tmp_path: Path) -> None:
    with patch(
        "rag_notion_kb.utils.image_cache._download_bytes",
        return_value=(b"image-bytes", "image/png"),
    ):
        first = download_image("https://example.com/signed-a.png", tmp_path)
        second = download_image("https://example.com/signed-b.png", tmp_path)

    assert first == second
    assert len(list(tmp_path.iterdir())) == 1


def test_download_retries_transient_errors(tmp_path: Path) -> None:
    failing_client = MagicMock()
    failing_client.__enter__.return_value = failing_client
    failing_client.get.side_effect = httpx.ConnectError("offline")
    ok_client = MagicMock()
    ok_client.__enter__.return_value = ok_client
    response = MagicMock()
    response.content = b"image-bytes"
    response.headers = {"content-type": "image/png"}
    ok_client.get.return_value = response

    with patch(
        "rag_notion_kb.utils.image_cache.httpx.Client",
        side_effect=[failing_client, ok_client],
    ) as client_cls:
        result = download_image("https://example.com/image.png", tmp_path)

    assert result is not None
    assert client_cls.call_count == 2


def test_large_image_is_resized_and_compressed(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    image = Image.effect_noise((3000, 2400), 100).convert("RGB")
    image.save(buffer, format="PNG")

    with patch(
        "rag_notion_kb.utils.image_cache._download_bytes",
        return_value=(buffer.getvalue(), "image/png"),
    ):
        result = download_image("https://example.com/large.png", tmp_path)

    assert result is not None
    with Image.open(result) as stored:
        assert max(stored.size) <= 1280
        assert Path(result).stat().st_size <= 1_500_000


def test_cleanup_removes_only_unreferenced_images(tmp_path: Path) -> None:
    keep = tmp_path / "keep.png"
    stale = tmp_path / "stale.png"
    keep.write_bytes(b"keep")
    stale.write_bytes(b"stale")

    removed = cleanup_image_cache(tmp_path, {"keep.png"})

    assert removed == 1
    assert keep.exists()
    assert not stale.exists()


def test_pack_and_unpack_image_urls() -> None:
    packed = pack_image_url(
        "/images/page/file.png",
        "https://example.com/signed.png?X-Amz-Signature=abc",
    )

    assert local_image_url(packed) == "/images/page/file.png"
    assert remote_image_url(packed) == "https://example.com/signed.png?X-Amz-Signature=abc"


def test_legacy_plain_urls_are_preserved() -> None:
    value = "https://example.com/signed.png?X-Amz-Signature=abc"

    assert local_image_url(value) == value
    assert remote_image_url(value) == value
