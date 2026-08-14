from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rag_notion_kb.config import Settings
from rag_notion_kb.models import (
    Chunk,
    ChunkMetadata,
    ChunkType,
    PageMetadata,
    PageSyncState,
)
from rag_notion_kb.services.sync_service import SyncService
from rag_notion_kb.storage.milvus_store import MilvusStore
from rag_notion_kb.storage.sync_state import SyncStateStore


def _make_service(deps: dict) -> SyncService:
    return SyncService(
        notion_client=deps["notion_client"],
        processor=deps["processor"],
        image_extractor=deps["image_extractor"],
        embedding=deps["embedding"],
        store=deps["store"],
        state_store=deps["state_store"],
        config=deps["config"],
    )


def _make_chunk(page_id: str, text: str, chunk_index: int) -> Chunk:
    return Chunk(
        text=text,
        metadata=ChunkMetadata(
            page_id=page_id,
            page_title="Test Page",
            page_url=f"https://notion.so/{page_id}",
            header_path="# Test Page",
            header_level=1,
            last_edited_time="2026-08-10T10:00:00Z",
            chunk_index=chunk_index,
            chunk_type=ChunkType.TEXT,
            image_url=None,
        ),
    )


def _markdown(title: str) -> str:
    return (
        f"# {title}\n\n"
        "This is a sufficiently detailed page body used for integration testing. "
        "It contains enough meaningful words to avoid the light-content skip path.\n"
    )


@pytest.fixture
def deps(tmp_path: Path) -> dict:
    store = MilvusStore(uri=tmp_path / "test.db", dim=2048)
    store.init_collection()
    state_store = SyncStateStore(db_path=tmp_path / "sync.db")
    config = Settings()

    notion_client = MagicMock()
    processor = MagicMock()
    image_extractor = MagicMock()
    embedding = MagicMock()

    processor.split.return_value = []
    image_extractor.extract.return_value = []
    embedding.embed.return_value = []

    try:
        yield {
            "notion_client": notion_client,
            "processor": processor,
            "image_extractor": image_extractor,
            "embedding": embedding,
            "store": store,
            "state_store": state_store,
            "config": config,
        }
    finally:
        state_store.close()
        store.close()


def test_full_sync_fetches_pages_without_vectorizing(deps: dict) -> None:
    pages = [
        PageMetadata(
            page_id="p1",
            title="Page 1",
            url="https://notion.so/p1",
            last_edited_time="2026-08-10T10:00:00Z",
        ),
        PageMetadata(
            page_id="p2",
            title="Page 2",
            url="https://notion.so/p2",
            last_edited_time="2026-08-10T10:00:00Z",
        ),
    ]
    deps["notion_client"].enumerate_pages.return_value = pages
    deps["notion_client"].get_page_markdown.side_effect = (
        lambda page_id, *, metadata=None: (_markdown(metadata.title), metadata)
    )

    service = _make_service(deps)
    result, _ = service.sync(root_page_ids=["root"])

    assert result.added == 2
    assert result.updated == 0
    assert result.skipped == 0
    assert result.removed == 0
    assert result.failed == 0

    states = deps["state_store"].list_all()
    assert len(states) == 2
    assert all(state.status == "fetched" for state in states)
    deps["processor"].split.assert_not_called()
    deps["embedding"].embed.assert_not_called()
    assert deps["store"].stats()["total_chunks"] == 0


def test_incremental_sync_skips_unchanged(deps: dict) -> None:
    page = PageMetadata(
        page_id="p1",
        title="Page 1",
        url="https://notion.so/p1",
        last_edited_time="2026-08-10T10:00:00Z",
    )
    deps["notion_client"].enumerate_pages.return_value = [page]
    deps["notion_client"].get_page_markdown.side_effect = (
        lambda page_id, *, metadata=None: (_markdown(metadata.title), metadata)
    )

    service = _make_service(deps)
    first, _ = service.sync(root_page_ids=["root"])
    second, _ = service.sync(root_page_ids=["root"])

    assert first.added == 1
    assert second.added == 0
    assert second.updated == 0
    assert second.skipped == 1
    assert deps["notion_client"].get_page_markdown.call_count == 1


def test_force_full_reprocesses_unchanged(deps: dict) -> None:
    page = PageMetadata(
        page_id="p1",
        title="Page 1",
        url="https://notion.so/p1",
        last_edited_time="2026-08-10T10:00:00Z",
    )
    deps["notion_client"].enumerate_pages.return_value = [page]
    deps["notion_client"].get_page_markdown.side_effect = (
        lambda page_id, *, metadata=None: (_markdown(metadata.title), metadata)
    )

    service = _make_service(deps)
    service.sync(root_page_ids=["root"])
    result, _ = service.sync(root_page_ids=["root"], force_full=True)

    assert result.updated == 1
    assert result.skipped == 0
    assert deps["notion_client"].get_page_markdown.call_count == 2


def test_missing_pages_are_removed(deps: dict) -> None:
    old_page = PageMetadata(
        page_id="p_old",
        title="Old Page",
        url="https://notion.so/p_old",
        last_edited_time="2026-08-09T10:00:00Z",
    )
    new_page = PageMetadata(
        page_id="p_new",
        title="New Page",
        url="https://notion.so/p_new",
        last_edited_time="2026-08-10T10:00:00Z",
    )

    deps["state_store"].upsert(
        PageSyncState(
            page_id=old_page.page_id,
            page_title=old_page.title,
            page_url=old_page.url,
            last_edited_time=old_page.last_edited_time,
            chunk_count=1,
            image_count=0,
            status="synced",
            last_synced_time="2026-08-09T10:00:00Z",
        )
    )
    deps["store"].upsert_page(
        old_page.page_id,
        [_make_chunk(old_page.page_id, "old content", 0)],
        [],
        [[0.0] * 2048],
    )

    deps["notion_client"].enumerate_pages.return_value = [new_page]
    deps["notion_client"].get_page_markdown.side_effect = (
        lambda page_id, *, metadata=None: (_markdown(metadata.title), metadata)
    )

    service = _make_service(deps)
    result, _ = service.sync(root_page_ids=["root"])

    assert result.added == 1
    assert result.removed == 1
    assert deps["state_store"].get(old_page.page_id) is None
    assert deps["store"].stats()["total_chunks"] == 0


def test_content_light_page_cleans_previous_state_and_vectors(deps: dict) -> None:
    page = PageMetadata(
        page_id="p1",
        title="Directory",
        url="https://notion.so/p1",
        last_edited_time="2026-08-11T10:00:00Z",
    )
    deps["state_store"].upsert(
        PageSyncState(
            page_id=page.page_id,
            page_title=page.title,
            page_url=page.url,
            last_edited_time="2026-08-10T10:00:00Z",
            chunk_count=1,
            image_count=0,
            status="synced",
            last_synced_time="2026-08-10T10:00:00Z",
        )
    )
    deps["store"].upsert_page(
        page.page_id,
        [_make_chunk(page.page_id, "old content", 0)],
        [],
        [[0.0] * 2048],
    )
    deps["notion_client"].enumerate_pages.return_value = [page]
    deps["notion_client"].get_page_markdown.return_value = (
        "[Child A](https://app.notion.com/p/a)\n"
        "[Child B](https://app.notion.com/p/b)\n",
        page,
    )

    service = _make_service(deps)
    result, _ = service.sync(root_page_ids=["root"])

    assert result.skipped == 1
    state = deps["state_store"].get(page.page_id)
    assert state is not None
    assert state.status == "skipped"
    assert state.vector_enabled is False
    assert deps["store"].stats()["total_chunks"] == 0


def test_image_only_page_is_fetched(deps: dict) -> None:
    page = PageMetadata(
        page_id="p1",
        title="Gallery",
        url="https://notion.so/p1",
        last_edited_time="2026-08-10T10:00:00Z",
    )
    deps["notion_client"].enumerate_pages.return_value = [page]
    deps["notion_client"].get_page_markdown.return_value = (
        "# Gallery\n\n![Architecture](https://example.com/arch.png)\n",
        page,
    )

    service = _make_service(deps)
    result, _ = service.sync(root_page_ids=["root"])

    assert result.added == 1
    assert deps["state_store"].get(page.page_id).status == "fetched"


def test_direct_page_sync_detects_container(deps: dict) -> None:
    page = PageMetadata(
        page_id="p1",
        title="Container",
        url="https://notion.so/p1",
        last_edited_time="2026-08-10T10:00:00Z",
        is_container=True,
    )
    deps["notion_client"].get_page_metadata_with_container.return_value = page

    service = _make_service(deps)
    result = service.sync_fetch(page_ids=[page.page_id])

    assert result.skipped == 1
    assert deps["state_store"].get(page.page_id).status == "skipped"
    assert deps["store"].stats()["total_chunks"] == 0


def test_enumeration_metadata_failures_are_counted(deps: dict) -> None:
    def enumerate(roots, out_failed_page_ids=None):
        if out_failed_page_ids is not None:
            out_failed_page_ids.append("bad-page")
        return []

    deps["notion_client"].enumerate_pages.side_effect = enumerate

    service = _make_service(deps)
    result = service.sync_fetch(root_page_ids=["root"])

    assert result.failed == 1


def test_single_page_failure_does_not_block_others(deps: dict) -> None:
    page_ok = PageMetadata(
        page_id="p_ok",
        title="OK Page",
        url="https://notion.so/p_ok",
        last_edited_time="2026-08-10T10:00:00Z",
    )
    page_fail = PageMetadata(
        page_id="p_fail",
        title="Fail Page",
        url="https://notion.so/p_fail",
        last_edited_time="2026-08-10T10:00:00Z",
    )

    deps["notion_client"].enumerate_pages.return_value = [page_ok, page_fail]

    def get_markdown(page_id: str, *, metadata=None):
        if metadata.page_id == page_fail.page_id:
            raise RuntimeError("network timeout")
        return _markdown(metadata.title), metadata

    deps["notion_client"].get_page_markdown.side_effect = get_markdown

    service = _make_service(deps)
    result, _ = service.sync(root_page_ids=["root"])

    assert result.added == 1
    assert result.failed == 1
    assert deps["state_store"].get("p_fail").status == "failed"
