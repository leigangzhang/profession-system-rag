
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from rag_notion_kb.exceptions import NotionApiError
from rag_notion_kb.models import PageMetadata
from rag_notion_kb.notion.client import NotionClient, PageRef


def _make_page(page_id: str, title: str, parent_id: str | None = None) -> dict:
    return {
        "object": "page",
        "id": page_id,
        "url": f"https://www.notion.so/{page_id.replace('-', '')}",
        "last_edited_time": "2026-08-10T10:00:00.000Z",
        "parent": {"type": "page_id", "page_id": parent_id} if parent_id else {"type": "workspace"},
        "properties": {
            "title": {
                "title": [{"plain_text": title}],
            },
        },
    }


def _make_child_block(block_id: str, title: str) -> dict:
    return {
        "object": "block",
        "id": block_id,
        "type": "child_page",
        "child_page": {"title": title},
    }


class TestNotionClient(unittest.TestCase):
    def setUp(self) -> None:
        self.token = "test-token"

    def _mock_client(self, mock_client_class: MagicMock) -> MagicMock:
        client = MagicMock()
        mock_client_class.return_value = client
        return client

    def _mock_converter(self, mock_converter_class: MagicMock, markdown: str) -> MagicMock:
        converter = MagicMock()
        converter.page_to_markdown.return_value = []
        converter.to_markdown_string.return_value = {"parent": markdown}
        mock_converter_class.return_value = converter
        return converter

    @patch("rag_notion_kb.notion.client.NotionToMarkdown")
    @patch("rag_notion_kb.notion.client.Client")
    def test_get_page_metadata(self, mock_client_class: MagicMock, mock_converter_class: MagicMock) -> None:
        client = self._mock_client(mock_client_class)
        client.pages.retrieve.return_value = _make_page("page-1", "Hello World")

        notion = NotionClient(self.token)
        metadata = notion.get_page_metadata("page-1")

        self.assertEqual(metadata.page_id, "page-1")
        self.assertEqual(metadata.title, "Hello World")
        self.assertEqual(metadata.parent_id, None)
        client.pages.retrieve.assert_called_once_with(page_id="page-1")

    @patch("rag_notion_kb.notion.client.NotionToMarkdown")
    @patch("rag_notion_kb.notion.client.Client")
    def test_list_child_pages(self, mock_client_class: MagicMock, mock_converter_class: MagicMock) -> None:
        client = self._mock_client(mock_client_class)
        client.blocks.children.list.return_value = {
            "results": [
                _make_child_block("child-1", "Child One"),
                _make_child_block("child-2", "Child Two"),
            ],
            "has_more": False,
        }

        notion = NotionClient(self.token)
        refs = notion.list_child_pages("page-1")

        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0], PageRef(page_id="child-1", title="Child One", parent_id="page-1"))
        self.assertEqual(refs[1], PageRef(page_id="child-2", title="Child Two", parent_id="page-1"))

    @patch("rag_notion_kb.notion.client.NotionToMarkdown")
    @patch("rag_notion_kb.notion.client.Client")
    def test_get_page_markdown(self, mock_client_class: MagicMock, mock_converter_class: MagicMock) -> None:
        client = self._mock_client(mock_client_class)
        client.pages.retrieve.return_value = _make_page("page-1", "Test Page")
        md = "# Test Page\n\n- item 1\n- item 2\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```python\nprint('hi')\n```"
        self._mock_converter(mock_converter_class, md)

        notion = NotionClient(self.token)
        markdown, metadata = notion.get_page_markdown("page-1")

        self.assertEqual(metadata.title, "Test Page")
        self.assertIn("# Test Page", markdown)
        self.assertIn("- item 1", markdown)
        self.assertIn("| a | b |", markdown)
        self.assertIn("```python", markdown)

    @patch("rag_notion_kb.notion.client.NotionToMarkdown")
    @patch("rag_notion_kb.notion.client.Client")
    def test_get_page_markdown_reuses_metadata(
        self, mock_client_class: MagicMock, mock_converter_class: MagicMock
    ) -> None:
        client = self._mock_client(mock_client_class)
        self._mock_converter(mock_converter_class, "# Test Page")
        metadata = PageMetadata(
            page_id="page-1",
            title="Test Page",
            url="https://www.notion.so/page1",
            last_edited_time="2026-08-10T10:00:00Z",
        )

        notion = NotionClient(self.token)
        markdown, returned_metadata = notion.get_page_markdown(
            "page-1", metadata=metadata
        )

        self.assertEqual(markdown, "# Test Page")
        self.assertEqual(returned_metadata, metadata)
        client.pages.retrieve.assert_not_called()

    @patch("rag_notion_kb.notion.client.NotionToMarkdown")
    @patch("rag_notion_kb.notion.client.Client")
    def test_enumerate_pages_recursive(self, mock_client_class: MagicMock, mock_converter_class: MagicMock) -> None:
        client = self._mock_client(mock_client_class)
        page_data = {
            "root": _make_page("root", "Root", None),
            "c1": _make_page("c1", "Child 1", "root"),
            "c2": _make_page("c2", "Child 2", "root"),
            "c3": _make_page("c3", "Child 3", "root"),
            "g1": _make_page("g1", "Grandchild 1", "c1"),
            "g2": _make_page("g2", "Grandchild 2", "c1"),
        }
        client.pages.retrieve.side_effect = lambda **kwargs: page_data[kwargs["page_id"]]
        client.blocks.children.list.side_effect = [
            {"results": [_make_child_block("c1", "Child 1"), _make_child_block("c2", "Child 2"), _make_child_block("c3", "Child 3")], "has_more": False},
            {"results": [_make_child_block("g1", "Grandchild 1"), _make_child_block("g2", "Grandchild 2")], "has_more": False},
            {"results": [], "has_more": False},
            {"results": [], "has_more": False},
            {"results": [], "has_more": False},
            {"results": [], "has_more": False},
        ]

        notion = NotionClient(self.token)
        pages = notion.enumerate_pages(["root"])

        self.assertEqual(len(pages), 6)
        ids = {p.page_id for p in pages}
        self.assertEqual(ids, {"root", "c1", "c2", "c3", "g1", "g2"})

    @patch("rag_notion_kb.notion.client.NotionToMarkdown")
    @patch("rag_notion_kb.notion.client.Client")
    def test_enumerate_pages_skips_permission_denied(self, mock_client_class: MagicMock, mock_converter_class: MagicMock) -> None:
        client = self._mock_client(mock_client_class)
        client.pages.retrieve.side_effect = [
            _make_page("root", "Root", None),
            NotionApiError("Permission denied"),
            _make_page("c2", "Child 2", "root"),
        ]
        client.blocks.children.list.return_value = {
            "results": [
                _make_child_block("c1", "Child 1"),
                _make_child_block("c2", "Child 2"),
            ],
            "has_more": False,
        }

        notion = NotionClient(self.token)
        pages = notion.enumerate_pages(["root"])

        ids = {p.page_id for p in pages}
        self.assertEqual(ids, {"root", "c2"})

    @patch("rag_notion_kb.notion.client.NotionToMarkdown")
    @patch("rag_notion_kb.notion.client.Client")
    def test_enumerate_pages_cycle_detection(self, mock_client_class: MagicMock, mock_converter_class: MagicMock) -> None:
        client = self._mock_client(mock_client_class)
        client.pages.retrieve.side_effect = [
            _make_page("a", "A", None),
            _make_page("b", "B", "a"),
            _make_page("a", "A", "b"),  # cycle back to a
        ]
        client.blocks.children.list.side_effect = [
            {"results": [_make_child_block("b", "B")], "has_more": False},
            {"results": [_make_child_block("a", "A")], "has_more": False},
        ]

        notion = NotionClient(self.token)
        pages = notion.enumerate_pages(["a"])

        self.assertEqual(len(pages), 2)
        self.assertEqual([p.page_id for p in pages], ["a", "b"])

    @patch("rag_notion_kb.notion.client.NotionToMarkdown")
    @patch("rag_notion_kb.notion.client.Client")
    def test_enumerate_pages_max_depth(self, mock_client_class: MagicMock, mock_converter_class: MagicMock) -> None:
        client = self._mock_client(mock_client_class)
        client.pages.retrieve.side_effect = [
            _make_page("root", "Root", None),
            _make_page("c1", "Child 1", "root"),
            _make_page("g1", "Grandchild 1", "c1"),
        ]
        client.blocks.children.list.side_effect = [
            {"results": [_make_child_block("c1", "Child 1")], "has_more": False},
            {"results": [_make_child_block("g1", "Grandchild 1")], "has_more": False},
            {"results": [], "has_more": False},
        ]

        notion = NotionClient(self.token, max_depth=2)
        pages = notion.enumerate_pages(["root"])

        ids = {p.page_id for p in pages}
        self.assertEqual(ids, {"root", "c1"})


    def _make_paragraph_block(self, text: str) -> dict:
        return {
            "object": "block",
            "id": "para-1",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": text}]},
        }

    def _make_empty_paragraph_block(self) -> dict:
        return {
            "object": "block",
            "id": "para-empty",
            "type": "paragraph",
            "paragraph": {"rich_text": []},
        }

    @patch("rag_notion_kb.notion.client.NotionToMarkdown")
    @patch("rag_notion_kb.notion.client.Client")
    def test_enumerate_marks_container_page_only_child_pages(self, mock_client_class: MagicMock, mock_converter_class: MagicMock) -> None:
        client = self._mock_client(mock_client_class)
        client.pages.retrieve.side_effect = [
            _make_page("root", "Root", None),
            _make_page("c1", "Child 1", "root"),
        ]
        client.blocks.children.list.side_effect = [
            {"results": [_make_child_block("c1", "Child 1")], "has_more": False},
            {"results": [], "has_more": False},
        ]

        notion = NotionClient(self.token)
        pages = notion.enumerate_pages(["root"])

        self.assertEqual(len(pages), 2)
        root = next(p for p in pages if p.page_id == "root")
        self.assertTrue(root.is_container)
        child = next(p for p in pages if p.page_id == "c1")
        self.assertFalse(child.is_container)

    @patch("rag_notion_kb.notion.client.NotionToMarkdown")
    @patch("rag_notion_kb.notion.client.Client")
    def test_enumerate_marks_container_page_when_only_empty_paragraphs_and_child_pages(
        self, mock_client_class: MagicMock, mock_converter_class: MagicMock
    ) -> None:
        """Notion represents blank lines as empty paragraph blocks; ignore them."""
        client = self._mock_client(mock_client_class)
        client.pages.retrieve.side_effect = [
            _make_page("root", "Root", None),
            _make_page("c1", "Child 1", "root"),
            _make_page("c2", "Child 2", "root"),
        ]
        client.blocks.children.list.side_effect = [
            {
                "results": [
                    self._make_empty_paragraph_block(),
                    _make_child_block("c1", "Child 1"),
                    _make_child_block("c2", "Child 2"),
                    self._make_empty_paragraph_block(),
                ],
                "has_more": False,
            },
            {"results": [], "has_more": False},
            {"results": [], "has_more": False},
        ]

        notion = NotionClient(self.token)
        pages = notion.enumerate_pages(["root"])

        root = next(p for p in pages if p.page_id == "root")
        self.assertTrue(root.is_container)

    @patch("rag_notion_kb.notion.client.NotionToMarkdown")
    @patch("rag_notion_kb.notion.client.Client")
    def test_enumerate_does_not_mark_page_with_own_content_as_container(self, mock_client_class: MagicMock, mock_converter_class: MagicMock) -> None:
        client = self._mock_client(mock_client_class)
        client.pages.retrieve.side_effect = [
            _make_page("root", "Root", None),
            _make_page("c1", "Child 1", "root"),
        ]
        client.blocks.children.list.side_effect = [
            {
                "results": [
                    self._make_paragraph_block("Some actual content"),
                    _make_child_block("c1", "Child 1"),
                ],
                "has_more": False,
            },
            {"results": [], "has_more": False},
        ]

        notion = NotionClient(self.token)
        pages = notion.enumerate_pages(["root"])

        root = next(p for p in pages if p.page_id == "root")
        self.assertFalse(root.is_container)

    @patch("rag_notion_kb.notion.client.NotionToMarkdown")
    @patch("rag_notion_kb.notion.client.Client")
    def test_enumerate_does_not_mark_leaf_page_as_container(self, mock_client_class: MagicMock, mock_converter_class: MagicMock) -> None:
        client = self._mock_client(mock_client_class)
        client.pages.retrieve.return_value = _make_page("root", "Root", None)
        client.blocks.children.list.return_value = {"results": [], "has_more": False}

        notion = NotionClient(self.token)
        pages = notion.enumerate_pages(["root"])

        self.assertEqual(len(pages), 1)
        self.assertFalse(pages[0].is_container)

    @patch("rag_notion_kb.notion.client.NotionToMarkdown")
    @patch("rag_notion_kb.notion.client.Client")
    def test_get_page_metadata_with_container(
        self, mock_client_class: MagicMock, mock_converter_class: MagicMock
    ) -> None:
        client = self._mock_client(mock_client_class)
        client.pages.retrieve.return_value = _make_page("root", "Root", None)
        client.blocks.children.list.return_value = {
            "results": [_make_child_block("c1", "Child 1")],
            "has_more": False,
        }

        notion = NotionClient(self.token)
        metadata = notion.get_page_metadata_with_container("root")

        self.assertTrue(metadata.is_container)

if __name__ == "__main__":
    unittest.main()
