from __future__ import annotations

import unittest

from rag_notion_kb.models import ChunkType, PageMetadata
from rag_notion_kb.processing.images import ImageExtractor


def _page_metadata() -> PageMetadata:
    return PageMetadata(
        page_id="page-1",
        title="Test Page",
        url="https://notion.so/page-1",
        last_edited_time="2026-08-10T10:00:00Z",
        parent_id="root-1",
    )


class TestImageExtractor(unittest.TestCase):
    def test_extracts_three_images(self) -> None:
        markdown = """# Gallery

Here are some diagrams.

![Architecture](https://example.com/arch.png)

## Details

![Detail A](https://example.com/a.png)

More text.

![Detail B](https://example.com/b.png)
"""
        docs = ImageExtractor(context_window=200).extract(markdown, _page_metadata())
        self.assertEqual(len(docs), 3)
        urls = [doc.image_url for doc in docs]
        self.assertEqual(
            urls,
            [
                "https://example.com/arch.png",
                "https://example.com/a.png",
                "https://example.com/b.png",
            ],
        )
        for doc in docs:
            self.assertEqual(doc.metadata.chunk_type, ChunkType.IMAGE)
            self.assertEqual(doc.metadata.page_id, "page-1")

    def test_context_stops_at_heading_boundary(self) -> None:
        markdown = """# Section A

Context for A.

![Image A](https://example.com/a.png)

# Section B

Context for B.
"""
        docs = ImageExtractor(context_window=1000).extract(markdown, _page_metadata())
        self.assertEqual(len(docs), 1)
        context = docs[0].context_text
        self.assertNotIn("Context for B", context)
        self.assertIn("Context for A", context)

    def test_no_images_returns_empty(self) -> None:
        markdown = """# Notes

Just plain text here.
"""
        docs = ImageExtractor().extract(markdown, _page_metadata())
        self.assertEqual(docs, [])

    def test_context_text_format(self) -> None:
        markdown = """# Intro

Before the diagram.

![Diagram](https://example.com/diagram.png)

After the diagram.
"""
        docs = ImageExtractor(context_window=200).extract(markdown, _page_metadata())
        self.assertEqual(len(docs), 1)
        self.assertIn("[Image: Diagram]", docs[0].context_text)
        self.assertNotIn("https://example.com/diagram.png", docs[0].context_text)
        self.assertIn("Before the diagram", docs[0].context_text)
        self.assertIn("After the diagram", docs[0].context_text)

    def test_header_path_and_level(self) -> None:
        markdown = """# Root

## Subsection

![Deep](https://example.com/deep.png)
"""
        docs = ImageExtractor().extract(markdown, _page_metadata())
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].metadata.header_level, 2)
        self.assertIn("# Root", docs[0].metadata.header_path)
        self.assertIn("## Subsection", docs[0].metadata.header_path)

    def test_source_offsets_are_in_document_order(self) -> None:
        markdown = """# Gallery

![First](https://example.com/first.png)

Text between.

![Second](https://example.com/second.png)
"""
        docs = ImageExtractor(context_window=200).extract(markdown, _page_metadata())

        offsets = [doc.source_offset for doc in docs]
        self.assertEqual(offsets, sorted(offsets))
        self.assertEqual(docs[0].source_offset, markdown.find("![First]"))
        self.assertEqual(docs[1].source_offset, markdown.find("![Second]"))

    def test_cleans_signed_url_alt_text(self) -> None:
        markdown = """# Gallery

![image-20210717182324592.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc](https://example.com/a.png)
"""
        docs = ImageExtractor(context_window=200).extract(markdown, _page_metadata())

        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].alt, "image-20210717182324592.png")
        self.assertNotIn("X-Amz", docs[0].context_text)

    def test_context_excludes_pure_code_block(self) -> None:
        markdown = """# Section

Before code.

```sql
select 1;
```

![Diagram](https://example.com/a.png)

After code.
"""
        docs = ImageExtractor(context_window=200).extract(markdown, _page_metadata())

        self.assertEqual(len(docs), 1)
        self.assertNotIn("select 1;", docs[0].context_text)
        self.assertIn("After code.", docs[0].context_text)

    def test_context_excludes_table(self) -> None:
        markdown = """# Section

Before table.

| Col | Value |
|-----|-------|
| a   | 1     |

![Diagram](https://example.com/a.png)

After table.
"""
        docs = ImageExtractor(context_window=200).extract(markdown, _page_metadata())

        self.assertEqual(len(docs), 1)
        self.assertNotIn("| a   | 1", docs[0].context_text)
        self.assertIn("After table.", docs[0].context_text)

    def test_context_includes_text_like_code_block(self) -> None:
        markdown = """# Section

```text
Text-like code context.
```

![Diagram](https://example.com/a.png)
"""
        docs = ImageExtractor(context_window=200).extract(markdown, _page_metadata())

        self.assertEqual(len(docs), 1)
        self.assertIn("Text-like code context.", docs[0].context_text)
        self.assertNotIn("```text", docs[0].context_text)


if __name__ == "__main__":
    unittest.main()
