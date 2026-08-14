
from __future__ import annotations

import logging
from typing import Any

from notion_client import Client
from notion_to_md import NotionToMarkdown
from pydantic import BaseModel

from rag_notion_kb.exceptions import NotionApiError, with_notion_retry
from rag_notion_kb.models import PageMetadata

logger = logging.getLogger(__name__)


class PageRef(BaseModel):
    """Reference to a child page discovered in Notion block tree."""


    page_id: str
    title: str
    parent_id: str | None = None


class NotionClient:
    """Client for reading Notion pages recursively and exporting Markdown."""

    @staticmethod
    def _is_empty_paragraph(block: dict[str, Any]) -> bool:
        """Return True for paragraph blocks with no visible text.

        Notion represents blank lines between blocks as empty paragraph blocks.
        These should not disqualify a page from being treated as a container.
        """
        if block.get("type") != "paragraph":
            return False
        rich_text = block.get("paragraph", {}).get("rich_text", [])
        if not rich_text:
            return True
        return "".join(rt.get("plain_text", "") for rt in rich_text).strip() == ""

    def __init__(self, token: str, max_depth: int = 10) -> None:
        self.token = token
        self.max_depth = max_depth
        self._client = Client(auth=token)

    @staticmethod
    def _extract_title(page: dict[str, Any]) -> str:
        """Extract plain-text title from a Notion page object."""
        title_prop = page.get("properties", {}).get("title", {})
        title_items = title_prop.get("title", []) if isinstance(title_prop, dict) else []
        return "".join(item.get("plain_text", "") for item in title_items).strip() or "Untitled"

    @staticmethod
    def _parent_id(page: dict[str, Any]) -> str | None:
        """Return parent page id if the page is nested under another page."""
        parent = page.get("parent")
        if isinstance(parent, dict) and parent.get("type") == "page_id":
            return parent.get("page_id")
        return None

    @with_notion_retry
    def _get_page(self, page_id: str) -> dict[str, Any]:
        """Fetch a raw Notion page object, retrying transient failures."""
        try:
            return self._client.pages.retrieve(page_id=page_id)
        except Exception as exc:
            raise NotionApiError(f"Failed to retrieve page {page_id}: {exc}") from exc

    def get_page_metadata(self, page_id: str) -> PageMetadata:
        """Return lightweight metadata for a single Notion page."""
        page = self._get_page(page_id)
        return PageMetadata(
            page_id=page.get("id", page_id),
            title=self._extract_title(page),
            url=page.get("url", f"https://www.notion.so/{page_id.replace('-', '')}"),
            last_edited_time=page.get("last_edited_time", ""),
            parent_id=self._parent_id(page),
        )

    def get_page_metadata_with_container(self, page_id: str) -> PageMetadata:
        """Return page metadata and detect container-only pages in one pass."""
        metadata = self.get_page_metadata(page_id)
        has_own = [True]
        try:
            blocks = self._list_child_page_blocks(page_id, out_has_own=has_own)
            metadata.is_container = bool(blocks) and not has_own[0]
        except NotionApiError:
            logger.warning(
                "Failed to detect container state for page %s; assuming it has own content",
                page_id,
                exc_info=True,
            )
        return metadata

    @with_notion_retry
    def _list_child_page_blocks(
        self, page_id: str, *, out_has_own: list[bool] | None = None
    ) -> list[dict[str, Any]]:
        """Return all direct child_page blocks under *page_id*, with pagination.

        If *out_has_own* is provided, ``out_has_own[0]`` is set to ``True`` when
        the page contains at least one non-child_page block (i.e. has its own content).
        """
        child_pages: list[dict[str, Any]] = []
        cursor: str | None = None
        if out_has_own is not None:
            out_has_own[0] = False
        try:
            while True:
                response = self._client.blocks.children.list(
                    block_id=page_id, start_cursor=cursor, page_size=100
                )
                results = response.get("results", [])
                for block in results:
                    if block.get("type") == "child_page":
                        child_pages.append(block)
                    elif out_has_own is not None and not self._is_empty_paragraph(block):
                        out_has_own[0] = True
                if not response.get("has_more"):
                    break
                cursor = response.get("next_cursor")
                if cursor is None:
                    break
        except Exception as exc:
            raise NotionApiError(f"Failed to list children of {page_id}: {exc}") from exc
        return child_pages

    def list_child_pages(self, page_id: str) -> list[PageRef]:
        """Discover immediate child pages and their titles."""
        blocks = self._list_child_page_blocks(page_id)
        refs: list[PageRef] = []
        for block in blocks:
            child = block.get("child_page", {})
            refs.append(
                PageRef(
                    page_id=block.get("id", ""),
                    title=child.get("title", "Untitled"),
                    parent_id=page_id,
                )
            )
        return refs

    @with_notion_retry
    def _fetch_markdown(self, page_id: str) -> str:
        """Export a Notion page to Markdown string."""
        try:
            # note: notion_to_md inlines sub-page content, so container pages appear content-heavy
            converter = NotionToMarkdown(self._client)
            md_blocks = converter.page_to_markdown(page_id)
            md_dict = converter.to_markdown_string(md_blocks)
            return md_dict.get("parent", "")
        except Exception as exc:
            raise NotionApiError(f"Failed to export markdown for {page_id}: {exc}") from exc

    def get_page_markdown(
        self, page_id: str, *, metadata: PageMetadata | None = None
    ) -> tuple[str, PageMetadata]:
        """Return Markdown content and metadata for a single page.

        Callers that already have metadata from traversal can pass it here to
        avoid a second Notion ``pages.retrieve`` request.
        """
        metadata = metadata or self.get_page_metadata(page_id)
        markdown = self._fetch_markdown(page_id)
        return markdown, metadata

    def enumerate_pages(
        self,
        root_page_ids: list[str],
        out_failed_page_ids: list[str] | None = None,
    ) -> list[PageMetadata]:
        """Recursively enumerate all reachable pages from *root_page_ids*.

        Uses DFS, prevents cycles via a visited set, and stops recursion at
        *max_depth*. Pages that cannot be read are logged and skipped.
        """
        if not root_page_ids:
            return []

        results: list[PageMetadata] = []
        visited: set[str] = set()
        stack: list[tuple[str, str | None, int]] = [
            (page_id, None, 1) for page_id in root_page_ids
        ]

        while stack:
            page_id, parent_id, depth = stack.pop()
            if page_id in visited:
                logger.debug("Skipping already-visited page: %s", page_id)
                continue
            if depth > self.max_depth:
                logger.warning(
                    "Max depth %d reached at page %s; stopping deeper traversal.",
                    self.max_depth,
                    page_id,
                )
                continue

            try:
                metadata = self.get_page_metadata(page_id)
            except NotionApiError:
                logger.exception("Skipping page %s due to metadata fetch failure", page_id)
                if out_failed_page_ids is not None:
                    out_failed_page_ids.append(page_id)
                continue

            if metadata.page_id in visited:
                continue
            visited.add(metadata.page_id)
            # Override parent_id with DFS traversal parent — Notion API may
            # report a different parent (e.g. workspace_id) for direct children
            # of the root, which breaks tree building.
            if parent_id is not None:
                metadata.parent_id = parent_id
            results.append(metadata)

            try:
                # Reuse the single blocks API call to also detect container pages.
                has_own = [True]  # default conservative
                blocks = self._list_child_page_blocks(page_id, out_has_own=has_own)
                children = [
                    PageRef(
                        page_id=b.get("id", ""),
                        title=b.get("child_page", {}).get("title", "Untitled"),
                        parent_id=page_id,
                    )
                    for b in blocks
                ]
                if children and not has_own[0]:
                    metadata.is_container = True
                    logger.debug("Page %s is a container (only child_page blocks)", page_id)
            except NotionApiError:
                logger.exception("Skipping children of %s due to list failure", page_id)
                continue

            for child in reversed(children):
                stack.append((child.page_id, metadata.page_id, depth + 1))

        return results

    def close(self) -> None:
        """Close the underlying Notion HTTP client."""
        try:
            self._client.close()
        except Exception:
            logger.exception("Failed to close NotionClient")
