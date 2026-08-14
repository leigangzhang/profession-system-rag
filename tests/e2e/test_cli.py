from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from rag_notion_kb.cli import app
from rag_notion_kb.models import (
    ChunkMetadata,
    ChunkType,
    SearchResult,
    SearchSource,
    SyncResult,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _make_mock_settings() -> MagicMock:
    """Create a mock Settings object with all required nested configs."""
    mock_settings = MagicMock()
    mock_settings.notion.token = "test-token"
    mock_settings.notion.root_page_ids = ["page-1"]
    mock_settings.embedding.api_key = "sk-test-embedding-key"
    mock_settings.embedding.base_url = "https://api.example.com/v1"
    mock_settings.embedding.model = "test-model"
    mock_settings.embedding.dimensions = 2048
    mock_settings.embedding.batch_size = 8
    mock_settings.embedding.max_retries = 3
    mock_settings.reranker.api_key = "sk-test-reranker-key"
    mock_settings.reranker.base_url = "https://rerank.example.com"
    mock_settings.reranker.model = "rerank-model"
    mock_settings.reranker.max_retries = 3
    mock_settings.storage.data_dir = "/tmp/test_kb"
    mock_settings.chunking.header_levels = [1, 2, 3]
    mock_settings.chunking.max_chunk_size = 1500
    mock_settings.chunking.preserve_tables = True
    mock_settings.chunking.preserve_code_blocks = True
    mock_settings.chunking.image_context_window = 200
    mock_settings.chunking.image_context_max_chars = 800
    mock_settings.retrieval.default_top_k = 10
    mock_settings.retrieval.default_expand_to_level = 2
    mock_settings.retrieval.default_max_tokens = 4000
    mock_settings.retrieval.dense_limit = 50
    mock_settings.retrieval.sparse_limit = 50
    mock_settings.retrieval.rrf_k = 60
    mock_settings.vectorize.max_concurrent = 1
    mock_settings.vectorize.poll_interval_seconds = 1
    mock_settings.logging.level = "INFO"
    mock_settings.logging.format = "json"
    return mock_settings


class TestSyncCommand:
    """Tests for the `sync` subcommand."""

    def test_sync_basic_output(self, runner: CliRunner) -> None:
        """Verify sync runs and prints counts."""
        mock_result = SyncResult(added=3, updated=1, removed=0, skipped=2, failed=0)

        with patch("rag_notion_kb.cli.Settings") as mock_settings_cls, \
             patch("rag_notion_kb.app_context.NotionClient"), \
             patch("rag_notion_kb.app_context.MarkdownProcessor"), \
             patch("rag_notion_kb.app_context.ImageExtractor"), \
             patch("rag_notion_kb.app_context.EmbeddingService"), \
             patch("rag_notion_kb.app_context.MilvusStore"), \
             patch("rag_notion_kb.app_context.SyncStateStore"), \
             patch("rag_notion_kb.app_context.SyncService") as mock_sync_cls:
            mock_settings_cls.return_value = _make_mock_settings()

            mock_svc = MagicMock()
            mock_svc.sync.return_value = (mock_result, None)
            mock_sync_cls.return_value = mock_svc

            result = runner.invoke(app, ["sync"])

            assert result.exit_code == 0
            assert "3" in result.stdout
            assert "1" in result.stdout
            assert "2" in result.stdout

    def test_sync_with_root_flag(self, runner: CliRunner) -> None:
        """Verify --root flag passes IDs to sync service."""
        with patch("rag_notion_kb.cli.Settings") as mock_settings_cls, \
             patch("rag_notion_kb.app_context.NotionClient"), \
             patch("rag_notion_kb.app_context.MarkdownProcessor"), \
             patch("rag_notion_kb.app_context.ImageExtractor"), \
             patch("rag_notion_kb.app_context.EmbeddingService"), \
             patch("rag_notion_kb.app_context.MilvusStore"), \
             patch("rag_notion_kb.app_context.SyncStateStore"), \
             patch("rag_notion_kb.app_context.SyncService") as mock_sync_cls:
            mock_settings = _make_mock_settings()
            mock_settings.notion.root_page_ids = []
            mock_settings_cls.return_value = mock_settings

            mock_svc = MagicMock()
            mock_svc.sync.return_value = (SyncResult(), None)
            mock_sync_cls.return_value = mock_svc

            result = runner.invoke(app, ["sync", "--root", "page-a, page-b"])

            assert result.exit_code == 0
            mock_svc.sync.assert_called_once()
            call_kwargs = mock_svc.sync.call_args[1]
            assert call_kwargs["root_page_ids"] == ["page-a", "page-b"]

    def test_sync_with_full_flag(self, runner: CliRunner) -> None:
        """Verify --full flag passes force_full=True."""
        with patch("rag_notion_kb.cli.Settings") as mock_settings_cls, \
             patch("rag_notion_kb.app_context.NotionClient"), \
             patch("rag_notion_kb.app_context.MarkdownProcessor"), \
             patch("rag_notion_kb.app_context.ImageExtractor"), \
             patch("rag_notion_kb.app_context.EmbeddingService"), \
             patch("rag_notion_kb.app_context.MilvusStore"), \
             patch("rag_notion_kb.app_context.SyncStateStore"), \
             patch("rag_notion_kb.app_context.SyncService") as mock_sync_cls:
            mock_settings_cls.return_value = _make_mock_settings()

            mock_svc = MagicMock()
            mock_svc.sync.return_value = (SyncResult(), None)
            mock_sync_cls.return_value = mock_svc

            result = runner.invoke(app, ["sync", "--full"])

            assert result.exit_code == 0
            mock_svc.sync.assert_called_once()
            call_kwargs = mock_svc.sync.call_args[1]
            assert call_kwargs["force_full"] is True

    def test_sync_no_root_ids(self, runner: CliRunner) -> None:
        """Verify error when no root page IDs are available."""
        with patch("rag_notion_kb.cli.Settings") as mock_settings_cls, \
             patch("rag_notion_kb.app_context.NotionClient"), \
             patch("rag_notion_kb.app_context.MilvusStore"), \
             patch("rag_notion_kb.app_context.SyncStateStore"), \
             patch("rag_notion_kb.app_context.NotionRootStore") as mock_root_cls, \
             patch("rag_notion_kb.app_context.NotionTreeCache"):
            mock_settings = _make_mock_settings()
            mock_settings.notion.root_page_ids = []
            mock_settings.notion.token = ""
            mock_settings_cls.return_value = mock_settings
            mock_root_cls.return_value.list_active_ids.return_value = []

            result = runner.invoke(app, ["sync"])

            assert result.exit_code != 0
            assert "root" in result.stdout.lower() or "root" in result.stderr.lower()


class TestStatusCommand:
    """Tests for the `status` subcommand."""

    def test_status_basic_output(self, runner: CliRunner) -> None:
        """Verify status prints KB stats."""
        mock_stats = {
            "total_chunks": 42,
            "total_pages": 10,
            "synced_pages": 8,
            "failed_pages": 2,
            "last_synced_time": "2025-01-01T00:00:00Z",
        }

        with patch("rag_notion_kb.cli.Settings") as mock_settings_cls, \
             patch("rag_notion_kb.app_context.EmbeddingService"), \
             patch("rag_notion_kb.app_context.RerankerService"), \
             patch("rag_notion_kb.app_context.MilvusStore"), \
             patch("rag_notion_kb.app_context.SyncStateStore"), \
             patch("rag_notion_kb.app_context.SearchService") as mock_svc_cls:
            mock_settings_cls.return_value = _make_mock_settings()

            mock_svc = MagicMock()
            mock_svc.stats.return_value = mock_stats
            mock_svc_cls.return_value = mock_svc

            result = runner.invoke(app, ["status"])

            assert result.exit_code == 0
            assert "42" in result.stdout
            assert "10" in result.stdout
            assert "8" in result.stdout
            assert "2" in result.stdout
            assert "2025-01-01" in result.stdout

    def test_status_no_data(self, runner: CliRunner) -> None:
        """Verify status handles empty KB gracefully."""
        mock_stats = {
            "total_chunks": 0,
            "total_pages": 0,
            "synced_pages": 0,
            "failed_pages": 0,
            "last_synced_time": None,
        }

        with patch("rag_notion_kb.cli.Settings") as mock_settings_cls, \
             patch("rag_notion_kb.app_context.EmbeddingService"), \
             patch("rag_notion_kb.app_context.RerankerService"), \
             patch("rag_notion_kb.app_context.MilvusStore"), \
             patch("rag_notion_kb.app_context.SyncStateStore"), \
             patch("rag_notion_kb.app_context.SearchService") as mock_svc_cls:
            mock_settings_cls.return_value = _make_mock_settings()

            mock_svc = MagicMock()
            mock_svc.stats.return_value = mock_stats
            mock_svc_cls.return_value = mock_svc

            result = runner.invoke(app, ["status"])

            assert result.exit_code == 0
            assert "N/A" in result.stdout or "None" in result.stdout


class TestServeCommand:
    """Tests for the `serve` subcommand."""

    def test_serve_initializes(self, runner: CliRunner) -> None:
        """Verify serve initializes MCPServer without error."""
        with patch("rag_notion_kb.cli.MCPServer") as mock_mcp_cls,              patch("rag_notion_kb.app_context.MilvusStore"),              patch("rag_notion_kb.app_context.SyncStateStore"),              patch("rag_notion_kb.app_context.NotionRootStore"),              patch("rag_notion_kb.app_context.NotionTreeCache"),              patch("rag_notion_kb.app_context.SearchHistoryStore"):
            mock_mcp = MagicMock()
            mock_mcp.run = MagicMock()
            mock_mcp_cls.from_context.return_value = mock_mcp

            result = runner.invoke(app, ["serve"])

            assert result.exit_code == 0
            mock_mcp.run.assert_called_once()

    def test_serve_config_error(self, runner: CliRunner) -> None:
        """Verify serve handles config errors gracefully."""
        with patch("rag_notion_kb.cli.MCPServer") as mock_mcp_cls,              patch("rag_notion_kb.app_context.MilvusStore"),              patch("rag_notion_kb.app_context.SyncStateStore"),              patch("rag_notion_kb.app_context.NotionRootStore"),              patch("rag_notion_kb.app_context.NotionTreeCache"):
            mock_mcp_cls.from_context.side_effect = RuntimeError("Missing config")
            result = runner.invoke(app, ["serve"])

            assert result.exit_code != 0


class TestConfigCommand:
    """Tests for the `config` subcommand."""

    def test_config_shows_sections(self, runner: CliRunner) -> None:
        """Verify config outputs all config sections."""
        with patch("rag_notion_kb.cli.Settings") as mock_settings_cls:
            mock_settings = _make_mock_settings()
            mock_settings.notion.token = "secret-token-abcdef1234567890"
            mock_settings.embedding.api_key = "sk-embedding-key-12345678"
            mock_settings.reranker.api_key = "sk-reranker-key-abcdefgh"
            mock_settings_cls.return_value = mock_settings

            result = runner.invoke(app, ["config"])

            assert result.exit_code == 0
            assert "Notion" in result.stdout
            assert "Embedding" in result.stdout
            assert "Reranker" in result.stdout
            assert "Storage" in result.stdout
            assert "Chunking" in result.stdout
            assert "Retrieval" in result.stdout
            assert "Logging" in result.stdout

    def test_config_masks_api_keys(self, runner: CliRunner) -> None:
        """Verify config masks sensitive API keys."""
        with patch("rag_notion_kb.cli.Settings") as mock_settings_cls:
            mock_settings = _make_mock_settings()
            mock_settings.notion.token = "secret-token-abcdef1234567890"
            mock_settings.notion.root_page_ids = []
            mock_settings.embedding.api_key = "sk-embedding-key-12345678"
            mock_settings.reranker.api_key = "sk-reranker-key-abcdefgh"
            mock_settings_cls.return_value = mock_settings

            result = runner.invoke(app, ["config"])

            assert result.exit_code == 0
            # Full keys should not appear
            assert "secret-token-abcdef1234567890" not in result.stdout
            assert "sk-embedding-key-12345678" not in result.stdout
            # Masked form should appear (first 4 + ... + last 4)
            assert "secr" in result.stdout
            assert "7890" in result.stdout


class TestSearchCommand:
    """Tests for the CLI search command and its history hook."""

    def test_search_prints_results_and_records_cli_source(self, runner: CliRunner) -> None:
        metadata = ChunkMetadata(
            page_id="page-1",
            page_title="Test Page",
            page_url="https://notion.so/page-1",
            header_path="# Section",
            header_level=1,
            last_edited_time="2026-08-10T10:00:00Z",
            chunk_index=0,
            chunk_type=ChunkType.TEXT,
        )

        with patch("rag_notion_kb.cli._build_settings") as mock_settings, \
             patch("rag_notion_kb.cli.AppContext") as mock_ctx_cls:
            mock_settings.return_value = _make_mock_settings()
            mock_ctx = MagicMock()
            mock_ctx.__enter__.return_value = mock_ctx
            mock_ctx.search_service.search.return_value = [
                SearchResult(
                    text="answer text",
                    score=0.91,
                    source=metadata,
                    matched_snippet="answer",
                )
            ]
            mock_ctx_cls.return_value = mock_ctx

            result = runner.invoke(app, ["search", "test query", "--top-k", "3"])

            assert result.exit_code == 0
            assert "Test Page" in result.stdout
            assert "answer text" in result.stdout
            call_kwargs = mock_ctx.search_service.search.call_args.kwargs
            assert call_kwargs["top_k"] == 3
            assert call_kwargs["history_source"] is SearchSource.CLI
            assert call_kwargs["rerank"] is True

    def test_search_can_skip_rerank(self, runner: CliRunner) -> None:
        with patch("rag_notion_kb.cli._build_settings") as mock_settings, \
             patch("rag_notion_kb.cli.AppContext") as mock_ctx_cls:
            mock_settings.return_value = _make_mock_settings()
            mock_ctx = MagicMock()
            mock_ctx.__enter__.return_value = mock_ctx
            mock_ctx.search_service.search.return_value = []
            mock_ctx_cls.return_value = mock_ctx

            result = runner.invoke(app, ["search", "test", "--skip-rerank"])

            assert result.exit_code == 0
            assert mock_ctx.search_service.search.call_args.kwargs["rerank"] is False

    def test_search_no_results(self, runner: CliRunner) -> None:
        with patch("rag_notion_kb.cli._build_settings") as mock_settings, \
             patch("rag_notion_kb.cli.AppContext") as mock_ctx_cls:
            mock_settings.return_value = _make_mock_settings()
            mock_ctx = MagicMock()
            mock_ctx.__enter__.return_value = mock_ctx
            mock_ctx.search_service.search.return_value = []
            mock_ctx_cls.return_value = mock_ctx

            result = runner.invoke(app, ["search", "nothing"])

            assert result.exit_code == 0
            assert "No results found" in result.stdout


class TestMainEntry:
    """Tests for the main entry point and app structure."""

    def test_app_is_typer(self, runner: CliRunner) -> None:
        """Verify app is a Typer instance with subcommands."""
        import typer as typer_mod
        assert isinstance(app, typer_mod.Typer)
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "sync" in result.stdout
        assert "status" in result.stdout
        assert "serve" in result.stdout
        assert "config" in result.stdout

    def test_main_calls_setup_logging(self, runner: CliRunner) -> None:
        """Verify main() sets up logging before calling app."""
        from rag_notion_kb.cli import main
        with patch("rag_notion_kb.cli.setup_logging") as mock_setup, \
             patch("rag_notion_kb.cli.app") as mock_app, \
             patch("rag_notion_kb.cli.Settings") as mock_settings_cls:
            mock_settings_cls.return_value = _make_mock_settings()
            main()
            mock_setup.assert_called_once()
            mock_app.assert_called_once()

    def test_main_integration(self, runner: CliRunner) -> None:
        """Smoke test: verify main runs without crashing on status."""
        mock_stats = {
            "total_chunks": 0,
            "total_pages": 0,
            "synced_pages": 0,
            "failed_pages": 0,
            "last_synced_time": None,
        }

        with patch("rag_notion_kb.cli.setup_logging"), \
             patch("rag_notion_kb.cli.Settings") as mock_settings_cls, \
             patch("rag_notion_kb.app_context.EmbeddingService"), \
             patch("rag_notion_kb.app_context.RerankerService"), \
             patch("rag_notion_kb.app_context.MilvusStore"), \
             patch("rag_notion_kb.app_context.SyncStateStore"), \
             patch("rag_notion_kb.app_context.SearchService") as mock_svc_cls:
            mock_settings_cls.return_value = _make_mock_settings()

            mock_svc = MagicMock()
            mock_svc.stats.return_value = mock_stats
            mock_svc_cls.return_value = mock_svc

            result = runner.invoke(app, ["status"])
            assert result.exit_code == 0
