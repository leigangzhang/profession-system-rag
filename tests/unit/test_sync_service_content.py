from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from rag_notion_kb.services.sync_service import SyncService


@pytest.fixture
def service() -> SyncService:
    """Create a SyncService with mocked dependencies for content checks."""
    svc = SyncService.__new__(SyncService)
    svc.notion_client = MagicMock()
    svc.processor = MagicMock()
    svc.image_extractor = MagicMock()
    svc.embedding = MagicMock()
    svc.store = MagicMock()
    svc.state_store = MagicMock()
    svc.config = MagicMock()
    return svc


class TestSubpageLinkList:
    def test_notion_link_list_is_invalid(self, service: SyncService) -> None:
        md = """
[0302-数据仓库-Hive](https://app.notion.com/p/0302-Hive-33299f1f13c14380969d980fd3ecdbe1?pvs=21)
[0303-离线计算-Spark](https://app.notion.com/p/0303-Spark-79546a3646d543e7b1d78b6f3d5191df?pvs=21)
[0304-实时计算-Flink](https://app.notion.com/p/0304-Flink-5d2af5ca5f044f47be2ee1a56a906ec8?pvs=21)
[0305-KV存储-HBASE](https://app.notion.com/p/0305-KV-HBASE-1e210967b54a40c7907193afa82b00cb?pvs=21)
"""
        assert service._is_subpage_link_list(md) is True
        assert service._is_content_light(md) is True

    def test_single_link_is_not_invalid(self, service: SyncService) -> None:
        md = "[单个页面](https://app.notion.com/p/one-page)"
        assert service._is_subpage_link_list(md) is False

    def test_links_with_real_content_is_valid(self, service: SyncService) -> None:
        md = """# 数据仓库

这是完整的数据仓库章节，包含 Hive 与 Spark 的介绍。

[Hive](https://app.notion.com/p/hive)
[Spark](https://app.notion.com/p/spark)

重点内容包括分区、分桶、SQL 优化以及实时计算场景下的应用实践。
"""
        assert service._is_subpage_link_list(md) is False

    def test_mixed_external_links_are_not_invalid(self, service: SyncService) -> None:
        md = """
[参考1](https://example.com/a)
[参考2](https://example.com/b)
"""
        assert service._is_subpage_link_list(md) is False

    def test_frontmatter_is_ignored(self, service: SyncService) -> None:
        md = """---
title: 目录
---
[页面1](https://app.notion.com/p/page-1)
[页面2](https://app.notion.com/p/page-2)
"""
        assert service._is_subpage_link_list(md) is True

    def test_image_only_page_is_not_content_light(self, service: SyncService) -> None:
        md = """# Gallery

![Architecture](https://example.com/arch.png)
"""
        assert service._is_subpage_link_list(md) is False
        assert service._is_content_light(md) is False
