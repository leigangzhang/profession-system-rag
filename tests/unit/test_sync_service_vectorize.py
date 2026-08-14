from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rag_notion_kb.config import ChunkingConfig, EmbeddingConfig
from rag_notion_kb.models import (
    Chunk,
    ChunkMetadata,
    ChunkType,
    ImageDoc,
    PageMetadata,
    PageSyncState,
)
from rag_notion_kb.processing.chunking import MarkdownProcessor
from rag_notion_kb.processing.images import ImageExtractor
from rag_notion_kb.services.sync_service import SyncService
from rag_notion_kb.utils.image_cache import pack_image_url


def _state(markdown: str, service: SyncService) -> PageSyncState:
    return PageSyncState(
        page_id="page-1",
        page_title="Page",
        page_url="https://notion.so/page-1",
        last_edited_time="2026-08-13T00:00:00Z",
        chunk_count=0,
        image_count=0,
        status="fetched",
        last_synced_time="2026-08-13T00:00:00Z",
        raw_markdown=markdown,
        vector_enabled=True,
        vector_status="pending",
        content_hash=hashlib.sha256(markdown.encode()).hexdigest(),
        chunking_hash=service._compute_chunking_hash(),
        embedding_hash=service._compute_embedding_hash(),
    )


@pytest.fixture
def service() -> SyncService:
    svc = SyncService.__new__(SyncService)
    svc.notion_client = MagicMock()
    svc.processor = MagicMock()
    svc.image_extractor = MagicMock()
    svc.embedding = MagicMock()
    svc.store = MagicMock()
    svc.state_store = MagicMock()
    svc.config = MagicMock()
    svc.config.storage.data_dir = "/tmp/rag-kb"
    svc.config.chunking = MagicMock()
    svc.config.chunking.header_levels = [1, 2]
    svc.config.chunking.max_chunk_size = 2048
    svc.config.chunking.min_chunk_size = 256
    svc.config.chunking.preserve_tables = True
    svc.config.chunking.preserve_code_blocks = True
    svc.config.chunking.image_context_window = 200
    svc.config.chunking.image_context_max_chars = 800
    svc.config.embedding = EmbeddingConfig(
        api_key="test",
        model="test-model",
        dimensions=2048,
        batch_size=8,
        max_retries=3,
    )
    return svc


def test_unchanged_page_with_zero_vectors_retries(service: SyncService) -> None:
    markdown = "# Title\n\nBody text"
    state = _state(markdown, service)

    service.store.get_page_embedding_stats.return_value = {"zero": 2}
    service.processor.split.return_value = []
    service.image_extractor.extract.return_value = []
    service.embedding.embed.return_value = []
    service.state_store.update_vector_status.return_value = None
    service.store.upsert_page.return_value = None

    with patch("rag_notion_kb.services.sync_service.download_image", return_value=None):
        result = service._vectorize_page(state)

    assert result["status"] == "indexed"
    service.processor.split.assert_called_once()


def test_unchanged_page_without_zero_vectors_skips(service: SyncService) -> None:
    markdown = "# Title\n\nBody text"
    state = _state(markdown, service)

    service.image_extractor.extract.return_value = []
    service.store.get_page_embedding_stats.return_value = {
        "total": 0,
        "nonzero": 0,
        "zero": 0,
        "dim": 2048,
    }

    result = service._vectorize_page(state)

    assert result["status"] == "skipped"
    service.processor.split.assert_not_called()


def test_empty_milvus_does_not_skip_matching_hashes(service: SyncService) -> None:
    markdown = "# Title\n\nBody text"
    state = _state(markdown, service)
    state.chunk_count = 2
    service.image_extractor.extract.return_value = []
    service.store.get_page_embedding_stats.return_value = {
        "total": 0,
        "nonzero": 0,
        "zero": 0,
        "dim": 2048,
    }
    service.processor.split.return_value = []
    service.embedding.embed.return_value = []

    result = service._vectorize_page(state)

    assert result["status"] == "indexed"
    service.processor.split.assert_called_once()


def test_embedding_model_change_does_not_skip(service: SyncService) -> None:
    markdown = "# Title\n\nBody text"
    state = _state(markdown, service)
    service.image_extractor.extract.return_value = []
    service.store.get_page_embedding_stats.return_value = {
        "total": 0,
        "nonzero": 0,
        "zero": 0,
        "dim": 2048,
    }
    service.processor.split.return_value = []
    service.embedding.embed.return_value = []
    service.config.embedding.model = "another-model"

    result = service._vectorize_page(state)

    assert result["status"] == "indexed"
    service.processor.split.assert_called_once()


def test_chunking_version_change_does_not_skip(
    service: SyncService, monkeypatch: pytest.MonkeyPatch
) -> None:
    markdown = "# Title\n\nBody text"
    state = _state(markdown, service)
    service.image_extractor.extract.return_value = []
    service.store.get_page_embedding_stats.return_value = {
        "total": 0,
        "nonzero": 0,
        "zero": 0,
        "dim": 2048,
    }
    service.processor.split.return_value = []
    service.embedding.embed.return_value = []
    monkeypatch.setattr("rag_notion_kb.services.sync_service.CHUNKING_VERSION", 99)

    result = service._vectorize_page(state)

    assert result["status"] == "indexed"
    service.processor.split.assert_called_once()


def test_content_hash_ignores_image_url_but_detects_image_bytes(
    service: SyncService, tmp_path: Path
) -> None:
    image_path = tmp_path / "diagram.png"
    image_path.write_bytes(b"image-v1")
    image = ImageDoc(
        image_url="https://example.com/signed-1.png",
        local_path=str(image_path),
        alt="diagram",
        context_text="",
        metadata=ChunkMetadata(
            page_id="page-1",
            page_title="Page",
            page_url="https://notion.so/page-1",
            header_path="# Page",
            header_level=1,
            last_edited_time="2026-08-13T00:00:00Z",
            chunk_index=0,
            chunk_type=ChunkType.IMAGE,
        ),
    )
    first = service._compute_content_hash(
        "![diagram](https://example.com/signed-1.png)",
        [image],
    )
    image.image_url = "https://example.com/signed-2.png"
    second = service._compute_content_hash(
        "![diagram](https://example.com/signed-2.png)",
        [image],
    )
    assert first == second

    image_path.write_bytes(b"image-v2")
    changed = service._compute_content_hash(
        "![diagram](https://example.com/signed-2.png)",
        [image],
    )
    assert changed != first


def test_zero_embedding_does_not_overwrite_existing_vectors(service: SyncService) -> None:
    markdown = "# Title\n\nBody text"
    state = _state(markdown, service)
    state.chunking_hash = "stale"
    service.image_extractor.extract.return_value = []
    service.store.get_page_embedding_stats.return_value = {
        "total": 0,
        "nonzero": 0,
        "zero": 0,
        "dim": 2048,
    }
    chunk = Chunk(
        text="Body text",
        metadata=ChunkMetadata(
            page_id="page-1",
            page_title="Page",
            page_url="https://notion.so/page-1",
            header_path="# Title",
            header_level=1,
            last_edited_time="2026-08-13T00:00:00Z",
            chunk_index=0,
            chunk_type=ChunkType.TEXT,
        ),
    )
    service.processor.split.return_value = [chunk]
    service.embedding.embed.return_value = [[0.0] * 2048]

    result = service._vectorize_page(state)

    assert result["status"] == "failed"
    service.store.upsert_page.assert_not_called()
    service.state_store.update_vector_status.assert_any_call(
        "page-1",
        vector_status="failed",
        vector_stage="failed",
        vector_error_message="1 embedding item(s) returned zero vectors",
    )


def test_missing_local_image_falls_back_to_text(service: SyncService) -> None:
    image = ImageDoc(
        image_url="https://example.com/signed-image.png",
        alt="diagram",
        context_text="context",
        metadata=ChunkMetadata(
            page_id="page-1",
            page_title="Page",
            page_url="https://notion.so/page-1",
            header_path="# Page",
            header_level=1,
            last_edited_time="2026-08-13T00:00:00Z",
            chunk_index=0,
            chunk_type=ChunkType.IMAGE,
        ),
    )

    service._embed_chunks_and_images([], [image])

    item = service.embedding.embed.call_args.args[0][0]
    assert item.type == "text"
    assert item.image_url is None
    assert "diagram" in (item.text or "")


def test_image_context_uses_configured_max_chars(
    service: SyncService, tmp_path: Path
) -> None:
    service.config.chunking.image_context_max_chars = 10
    markdown = "# Title\n\n![A](https://example.com/a.png)"
    state = _state(markdown, service)
    state.chunking_hash = "stale"
    image = ImageDoc(
        image_url="https://example.com/a.png",
        alt="A",
        context_text="x" * 100,
        metadata=ChunkMetadata(
            page_id="page-1",
            page_title="Page",
            page_url="https://notion.so/page-1",
            header_path="# Title",
            header_level=1,
            last_edited_time="2026-08-13T00:00:00Z",
            chunk_index=0,
            chunk_type=ChunkType.IMAGE,
        ),
    )
    service.image_extractor.extract.return_value = [image]
    service.processor.split.return_value = []
    service.embedding.embed.return_value = [[1.0] * 2048]
    local_image = tmp_path / "image.png"
    local_image.write_bytes(b"image-bytes")

    with patch(
        "rag_notion_kb.services.sync_service.download_image",
        return_value=str(local_image),
    ):
        result = service._vectorize_page(state)

    assert result["status"] == "indexed"
    item = service.embedding.embed.call_args.args[0][0]
    assert len(item.text or "") <= 10


def test_missing_image_cache_is_refreshed_from_notion(service: SyncService) -> None:
    page = PageMetadata(
        page_id="page-1",
        title="Page",
        url="https://notion.so/page-1",
        last_edited_time="2026-08-13T00:00:00Z",
    )
    fresh_image = ImageDoc(
        image_url="https://example.com/fresh.png",
        remote_url="https://example.com/fresh.png",
        alt="fresh",
        context_text="context",
        metadata=ChunkMetadata(
            page_id="page-1",
            page_title="Page",
            page_url="https://notion.so/page-1",
            header_path="# Page",
            header_level=1,
            last_edited_time="2026-08-13T00:00:00Z",
            chunk_index=0,
            chunk_type=ChunkType.IMAGE,
        ),
    )
    service.notion_client.get_page_markdown.return_value = (
        "![fresh](https://example.com/fresh.png)",
        page,
    )
    service.image_extractor.extract.return_value = [fresh_image]

    with patch(
        "rag_notion_kb.services.sync_service.download_image",
        return_value="/tmp/rag-kb/images/page-1/fresh.png",
    ):
        refreshed_markdown, refreshed_images = service._refresh_images_from_notion(
            PageSyncState(
                page_id=page.page_id,
                page_title=page.title,
                page_url=page.url,
                last_edited_time=page.last_edited_time,
                chunk_count=0,
                image_count=0,
                status="fetched",
                last_synced_time="2026-08-13T00:00:00Z",
            ),
            page,
        )

    assert refreshed_markdown is not None
    assert refreshed_images[0].local_path == "/tmp/rag-kb/images/page-1/fresh.png"
    service.state_store.update_raw_markdown.assert_called_once()


def test_missing_image_fails_without_sending_remote_url(service: SyncService) -> None:
    markdown = "# Title\n\n![A](https://example.com/a.png)"
    state = _state(markdown, service)
    state.chunking_hash = "stale"
    image = ImageDoc(
        image_url="https://example.com/a.png",
        remote_url="https://example.com/a.png",
        alt="A",
        context_text="context",
        metadata=ChunkMetadata(
            page_id="page-1",
            page_title="Page",
            page_url="https://notion.so/page-1",
            header_path="# Title",
            header_level=1,
            last_edited_time="2026-08-13T00:00:00Z",
            chunk_index=0,
            chunk_type=ChunkType.IMAGE,
        ),
    )
    service.image_extractor.extract.return_value = [image]
    service.notion_client.get_page_markdown.side_effect = RuntimeError("notion down")

    with patch("rag_notion_kb.services.sync_service.download_image", return_value=None):
        result = service._vectorize_page(state)

    assert result["status"] == "failed"
    service.embedding.embed.assert_not_called()


def test_cache_page_images_rewrites_downloaded_urls(service: SyncService) -> None:
    page = PageMetadata(
        page_id="page-1",
        title="Page",
        url="https://notion.so/page-1",
        last_edited_time="2026-08-13T00:00:00Z",
    )
    image = ImageDoc(
        image_url="https://example.com/a.png",
        remote_url="https://example.com/a.png",
        alt="image-20210717182324592.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc",
        context_text="context",
        metadata=ChunkMetadata(
            page_id="page-1",
            page_title="Page",
            page_url="https://notion.so/page-1",
            header_path="# Page",
            header_level=1,
            last_edited_time="2026-08-13T00:00:00Z",
            chunk_index=0,
            chunk_type=ChunkType.IMAGE,
        ),
    )
    service.image_extractor.extract.return_value = [image]

    with patch(
        "rag_notion_kb.services.sync_service.download_image",
        return_value="/tmp/rag-kb/images/page-1/abcd.png",
    ) as download:
        markdown = service._cache_page_images(
            page,
            "![image-20210717182324592.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc](https://example.com/a.png)",
        )

    assert markdown == (
        "![image-20210717182324592.png]"
        f"({pack_image_url('/images/page-1/abcd.png', 'https://example.com/a.png')})"
    )
    download.assert_called_once_with(
        "https://example.com/a.png",
        Path("/tmp/rag-kb/images/page-1"),
    )


def test_cache_page_images_keeps_markdown_on_extract_failure(service: SyncService) -> None:
    page = PageMetadata(
        page_id="page-1",
        title="Page",
        url="https://notion.so/page-1",
        last_edited_time="2026-08-13T00:00:00Z",
    )
    service.image_extractor.extract.side_effect = RuntimeError("extract failed")

    markdown = "![A](https://example.com/a.png)"
    assert service._cache_page_images(page, markdown) == markdown


def test_vectorize_preserves_source_order_with_repeated_code_fences() -> None:
    markdown = """# A

```sql
select 1;
```

![A](https://example.com/a.png)

## B

```sql
select 2;
```

![B](https://example.com/b.png)
"""
    service = SyncService.__new__(SyncService)
    service.notion_client = MagicMock()
    service.config = MagicMock()
    service.config.storage.data_dir = "/tmp/rag-kb"
    service.config.chunking = ChunkingConfig(header_levels=[1, 2], min_chunk_size=0)
    service.config.embedding = EmbeddingConfig(
        api_key="test",
        model="test-model",
        dimensions=2048,
        batch_size=8,
        max_retries=3,
    )
    service.processor = MarkdownProcessor(service.config.chunking)
    service.image_extractor = ImageExtractor(
        context_window=service.config.chunking.image_context_window
    )
    service.embedding = MagicMock()
    service.store = MagicMock()
    service.state_store = MagicMock()
    service.store.get_page_embedding_stats.return_value = {"zero": 0}
    def embed_with_progress(items, progress_callback=None):
        if progress_callback is not None:
            progress_callback(2, len(items))
            progress_callback(4, len(items))
            progress_callback(len(items), len(items))
        return [[1.0] * 2048 for _ in items]

    service.embedding.embed.side_effect = embed_with_progress

    page = PageMetadata(
        page_id="page-1",
        title="Page",
        url="https://notion.so/page-1",
        last_edited_time="2026-08-13T00:00:00Z",
    )
    state = PageSyncState(
        page_id=page.page_id,
        page_title=page.title,
        page_url=page.url,
        last_edited_time=page.last_edited_time,
        chunk_count=0,
        image_count=0,
        status="fetched",
        last_synced_time="2026-08-13T00:00:00Z",
        raw_markdown=markdown,
        vector_enabled=True,
        vector_status="pending",
    )

    with patch(
        "rag_notion_kb.services.sync_service.download_image",
        return_value="/tmp/rag-kb/images/page-1/image.png",
    ):
        result = service._vectorize_page(state)

    assert result["status"] == "indexed"
    stored_chunks = service.store.upsert_page.call_args.args[1]
    stored_images = service.store.upsert_page.call_args.args[2]
    combined = [
        (item.metadata.chunk_index, item.metadata.chunk_type.value, item.text if isinstance(item, Chunk) else item.alt)
        for item in sorted(
            list(stored_chunks) + list(stored_images),
            key=lambda item: item.metadata.chunk_index,
        )
    ]
    assert combined == [
        (0, "text", "# A"),
        (1, "code", "```sql\nselect 1;\n```"),
        (2, "image", "A"),
        (3, "text", "## B"),
        (4, "code", "```sql\nselect 2;\n```"),
        (5, "image", "B"),
    ]
    stages = [
        call.kwargs.get("vector_stage")
        for call in service.state_store.update_vector_status.call_args_list
    ]
    assert set(stages) >= {
        "preparing",
        "extracting_images",
        "validating",
        "chunking",
        "interleaving",
        "embedding",
        "storing",
    }
    embedding_progress = [
        call.kwargs["vector_progress"]
        for call in service.state_store.update_vector_status.call_args_list
        if call.kwargs.get("vector_stage") == "embedding"
    ]
    assert len(embedding_progress) >= 4
    assert max(embedding_progress) >= 70
