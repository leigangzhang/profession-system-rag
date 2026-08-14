from __future__ import annotations

import unittest

from rag_notion_kb.config import ChunkingConfig
from rag_notion_kb.models import ChunkType, PageMetadata
from rag_notion_kb.processing.chunking import MarkdownProcessor


def _page_metadata() -> PageMetadata:
    return PageMetadata(
        page_id="page-1",
        title="Test Page",
        url="https://notion.so/page-1",
        last_edited_time="2026-08-10T10:00:00Z",
        parent_id="root-1",
    )


class TestMarkdownProcessor(unittest.TestCase):
    def test_header_splitting(self) -> None:
        markdown = """# Title

Intro paragraph.

## Section A

Text A.

### Sub A

Text sub A.

## Section B

Text B.
"""
        config = ChunkingConfig(header_levels=[1, 2, 3], min_chunk_size=0)
        processor = MarkdownProcessor(config)
        chunks = processor.split(markdown, _page_metadata())

        # Expected chunks: Title, Section A, Sub A, Section B
        self.assertEqual(len(chunks), 4)
        self.assertTrue(all(chunk.metadata.page_id == "page-1" for chunk in chunks))
        self.assertEqual(
            [chunk.metadata.header_level for chunk in chunks],
            [1, 2, 3, 2],
        )
        self.assertEqual(chunks[2].metadata.header_path, "# Title > ## Section A > ### Sub A")

    def test_table_preserved_as_single_chunk(self) -> None:
        markdown = """# Title

Intro paragraph.

| Col1 | Col2 |
|------|------|
| a    | b    |
| c    | d    |

After table.
"""
        config = ChunkingConfig(header_levels=[1, 2], preserve_tables=True, min_chunk_size=0)
        processor = MarkdownProcessor(config)
        chunks = processor.split(markdown, _page_metadata())

        table_chunks = [chunk for chunk in chunks if chunk.metadata.chunk_type == ChunkType.TABLE]
        self.assertEqual(len(table_chunks), 1)
        table_chunk = table_chunks[0]
        self.assertIn("| Col1 | Col2 |", table_chunk.text)
        self.assertIn("|---", table_chunk.text)
        self.assertIn("| a    | b    |", table_chunk.text)
        self.assertEqual(table_chunk.metadata.header_level, 1)

    def test_code_block_preserved_as_single_chunk(self) -> None:
        markdown = """# Title

```python
def add(a: int, b: int) -> int:
    return a + b
```

After code.
"""
        config = ChunkingConfig(header_levels=[1, 2], preserve_code_blocks=True, min_chunk_size=0)
        processor = MarkdownProcessor(config)
        chunks = processor.split(markdown, _page_metadata())

        code_chunks = [chunk for chunk in chunks if chunk.metadata.chunk_type == ChunkType.CODE]
        self.assertEqual(len(code_chunks), 1)
        code_chunk = code_chunks[0]
        self.assertTrue(code_chunk.text.startswith("```python"))
        self.assertIn("return a + b", code_chunk.text)
        self.assertEqual(code_chunk.metadata.header_level, 1)

    def test_text_fenced_blocks_are_merged_as_text(self) -> None:
        markdown = """# Title

Before.

```text
line one
line two
```

After.
"""
        config = ChunkingConfig(header_levels=[1, 2], min_chunk_size=0)
        processor = MarkdownProcessor(config)
        chunks = processor.split(markdown, _page_metadata())

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].metadata.chunk_type, ChunkType.TEXT)
        self.assertIn("line one\nline two", chunks[0].text)
        self.assertNotIn("```text", chunks[0].text)

    def test_similar_text_fences_are_merged_as_text(self) -> None:
        config = ChunkingConfig(header_levels=[1, 2], min_chunk_size=0)
        processor = MarkdownProcessor(config)
        for language in ("text", "plaintext", "plain", "txt", "markdown", "md", "plain text"):
            with self.subTest(language=language):
                markdown = f"""# Title

```{language}
content
```
"""
                chunks = processor.split(markdown, _page_metadata())
                self.assertEqual([chunk.metadata.chunk_type for chunk in chunks], [ChunkType.TEXT])

    def test_text_fence_preserves_bullets_and_line_breaks(self) -> None:
        markdown = """# Orders

```text
• 离线数据：Kafka -> Hive -> Clickhouse
• 在线数据：Kafka -> Flink -> MySQL -> Clickhouse
• Merge：借助VIEW将离线和在线表UNION到一起来查询分析使用。
```
"""
        config = ChunkingConfig(header_levels=[1, 2], min_chunk_size=0)
        processor = MarkdownProcessor(config)
        chunks = processor.split(markdown, _page_metadata())

        self.assertEqual(len(chunks), 1)
        self.assertIn("• 离线数据：Kafka -> Hive -> Clickhouse\n", chunks[0].text)
        self.assertIn("• 在线数据：Kafka -> Flink -> MySQL -> Clickhouse\n", chunks[0].text)
        self.assertIn("• Merge：借助VIEW将离线和在线表UNION到一起来查询分析使用。", chunks[0].text)

    def test_text_fence_preserves_paragraph_line_breaks(self) -> None:
        markdown = """# Online

```plaintext
Muise功能：只支持ClickHouse Sink功能，需先在CTT建表，后连接Flink写入数据，开启快照应该可以支持At-Least-Once语义。
JFlink应用：Java Flink通过JDBC或者HTTP方式，实时计算并写入ClickHouse Sink，可实现At-Least-Once语义。
```
"""
        config = ChunkingConfig(header_levels=[1, 2], min_chunk_size=0)
        processor = MarkdownProcessor(config)
        chunks = processor.split(markdown, _page_metadata())

        self.assertEqual(len(chunks), 1)
        self.assertIn("Muise功能：只支持ClickHouse Sink功能，需先在CTT建表，后连接Flink写入数据，开启快照应该可以支持At-Least-Once语义。\n", chunks[0].text)
        self.assertIn("JFlink应用：Java Flink通过JDBC或者HTTP方式，实时计算并写入ClickHouse Sink，可实现At-Least-Once语义。", chunks[0].text)

    def test_image_tag_retained(self) -> None:
        markdown = """# Title

![Diagram](https://example.com/diagram.png)

More text.
"""
        config = ChunkingConfig(header_levels=[1, 2], min_chunk_size=0)
        processor = MarkdownProcessor(config)
        chunks = processor.split(markdown, _page_metadata())

        # Image tags are now protected blocks — they become standalone chunks
        # but the image URL is preserved intact and not counted against chunk size budget.
        all_text = "".join(c.text for c in chunks)
        self.assertIn("![Diagram](https://example.com/diagram.png)", all_text)
        # Verify no chunk has an unclosed image tag
        for c in chunks:
            if "![" in c.text:
                self.assertEqual(c.text.count("!["), c.text.count(")"),
                                 f"Unclosed image tag in chunk: {c.text[:80]}")
        # The image chunk itself should be classified as TEXT
        img_chunks = [c for c in chunks if "![Diagram]" in c.text]
        self.assertTrue(len(img_chunks) > 0)
        self.assertEqual(img_chunks[0].metadata.chunk_type, ChunkType.TEXT)

    def test_chunk_index_order(self) -> None:
        markdown = """# A

Line A.

## B

Line B.
"""
        config = ChunkingConfig(header_levels=[1, 2], min_chunk_size=0)
        processor = MarkdownProcessor(config)
        chunks = processor.split(markdown, _page_metadata())

        self.assertEqual([chunk.metadata.chunk_index for chunk in chunks], [0, 1])

    def test_source_offsets_preserve_repeated_code_fences(self) -> None:
        markdown = """# A

```sql
select 1;
```

Paragraph A.

## B

```sql
select 2;
```

Paragraph B.
"""
        config = ChunkingConfig(header_levels=[1, 2], min_chunk_size=0)
        processor = MarkdownProcessor(config)
        chunks = processor.split(markdown, _page_metadata())
        code_chunks = [chunk for chunk in chunks if chunk.metadata.chunk_type == ChunkType.CODE]

        self.assertEqual(len(code_chunks), 2)
        offsets = [chunk.source_offset for chunk in chunks]
        self.assertTrue(all(offset is not None for offset in offsets))
        self.assertEqual(offsets, sorted(offsets))
        code_offsets = [chunk.source_offset for chunk in code_chunks]
        self.assertEqual(
            [markdown[offset:offset + 6] for offset in code_offsets if offset is not None],
            ["```sql", "```sql"],
        )

    def test_oversized_table_is_split_within_hard_limit(self) -> None:
        rows = ["| id | value |", "|----|-------|"]
        for i in range(1200):
            rows.append(f"| {i} | {'x' * 60} |")
        markdown = "# Huge Table\n\n" + "\n".join(rows) + "\n"
        config = ChunkingConfig(header_levels=[1, 2], preserve_tables=True, min_chunk_size=0)
        processor = MarkdownProcessor(config)
        chunks = processor.split(markdown, _page_metadata())
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c.text) <= 10000 for c in chunks))
        # Split table pieces should still be classified as tables
        table_pieces = [c for c in chunks if c.metadata.chunk_type == ChunkType.TABLE]
        self.assertGreater(len(table_pieces), 1)

    def test_oversized_code_block_is_split_within_hard_limit(self) -> None:
        lines = ["```python"]
        for i in range(1200):
            lines.append(f"x{i} = {'y' * 50}")
        lines.append("```")
        markdown = "# Huge Code\n\n" + "\n".join(lines) + "\n"
        config = ChunkingConfig(header_levels=[1, 2], preserve_code_blocks=True, min_chunk_size=0)
        processor = MarkdownProcessor(config)
        chunks = processor.split(markdown, _page_metadata())
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c.text) <= 10000 for c in chunks))
        code_pieces = [c for c in chunks if c.metadata.chunk_type == ChunkType.CODE]
        self.assertGreater(len(code_pieces), 1)


if __name__ == "__main__":
    unittest.main()
