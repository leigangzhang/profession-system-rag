"""E2E tests for the web server CLI command."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from rag_notion_kb.cli import app


class TestWebCommand(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def _patch_web_environment(self):
        """Return patches for the high-level collaborators used by web()."""
        mock_settings = MagicMock()
        mock_settings.return_value = MagicMock()
        mock_ctx = MagicMock()
        mock_app_ctx_cls = MagicMock()
        mock_app_ctx_cls.return_value.__enter__.return_value = mock_ctx
        return {
            "settings": mock_settings,
            "ctx": mock_ctx,
            "patches": [
                patch("rag_notion_kb.cli._build_settings", mock_settings),
                patch("rag_notion_kb.cli.AppContext", mock_app_ctx_cls),
                patch("rag_notion_kb.cli.create_app"),
                patch("rag_notion_kb.cli.uvicorn"),
            ],
        }

    def test_web_default_args(self) -> None:
        """Test that `web` starts uvicorn with default host/port."""
        env = self._patch_web_environment()
        mock_uvicorn = MagicMock()
        mock_create = MagicMock()
        mock_create.return_value = MagicMock()
        env["patches"][3] = patch("rag_notion_kb.cli.uvicorn", mock_uvicorn)
        env["patches"][2] = patch("rag_notion_kb.cli.create_app", mock_create)

        with env["patches"][0], env["patches"][1], env["patches"][2], env["patches"][3]:
            result = self.runner.invoke(app, ["web"])
            self.assertEqual(result.exit_code, 0)
            mock_uvicorn.run.assert_called_once()
            call_kwargs = mock_uvicorn.run.call_args[1]
            self.assertEqual(call_kwargs["host"], "127.0.0.1")
            self.assertEqual(call_kwargs["port"], 58000)

    def test_web_custom_args(self) -> None:
        """Test that `web --host 0.0.0.0 --port 9000` passes correctly."""
        env = self._patch_web_environment()
        mock_uvicorn = MagicMock()
        env["patches"][3] = patch("rag_notion_kb.cli.uvicorn", mock_uvicorn)
        env["patches"][2] = patch(
            "rag_notion_kb.cli.create_app", MagicMock(return_value=MagicMock())
        )

        with env["patches"][0], env["patches"][1], env["patches"][2], env["patches"][3]:
            result = self.runner.invoke(
                app, ["web", "--host", "0.0.0.0", "--port", "9000"]
            )
            self.assertEqual(result.exit_code, 0)
            call_kwargs = mock_uvicorn.run.call_args[1]
            self.assertEqual(call_kwargs["host"], "0.0.0.0")
            self.assertEqual(call_kwargs["port"], 9000)

    @patch("rag_notion_kb.cli._build_settings")
    def test_web_config_error(self, mock_settings: object) -> None:
        """Test that config loading failure exits with code 1."""
        mock_settings.side_effect = RuntimeError("No config found")
        result = self.runner.invoke(app, ["web"])
        self.assertNotEqual(result.exit_code, 0)

    def test_web_help(self) -> None:
        """Test that `web --help` shows help text."""
        result = self.runner.invoke(app, ["web", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Start the Web UI", result.output)
        self.assertIn("--host", result.output)
        self.assertIn("--port", result.output)


if __name__ == "__main__":
    unittest.main()
