from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from rag_notion_kb.config import Settings


class TestConfig(unittest.TestCase):
    def test_default_values(self) -> None:
        from rag_notion_kb.config import ChunkingConfig, EmbeddingConfig, RetrievalConfig
        # Check class-level defaults (not instance values, which can be overridden by .env)
        self.assertEqual(
            EmbeddingConfig.model_fields["model"].default,
            "qwen3-vl-embedding",
        )
        self.assertEqual(
            RetrievalConfig.model_fields["default_top_k"].default,
            10,
        )
        self.assertEqual(
            ChunkingConfig.model_fields["image_context_max_chars"].default,
            800,
        )
        # Smoke test: Settings() should instantiate without error
        settings = Settings()
        self.assertIsInstance(settings.retrieval.default_top_k, int)

    def test_env_override(self) -> None:
        os.environ["RAG_KB_EMBEDDING__MODEL"] = "custom-model"
        os.environ["RAG_KB_EMBEDDING__DIMENSIONS"] = "1024"
        try:
            settings = Settings()
            self.assertEqual(settings.embedding.model, "custom-model")
            self.assertEqual(settings.embedding.dimensions, 1024)
        finally:
            del os.environ["RAG_KB_EMBEDDING__MODEL"]
            del os.environ["RAG_KB_EMBEDDING__DIMENSIONS"]

    def test_yaml_source(self) -> None:
        with tempfile.TemporaryDirectory() as cwd_tmp:
            # Create a temporary cwd with NO .env file so yaml is the only source
            old_cwd = os.getcwd()
            os.chdir(cwd_tmp)
            try:
                config_dir = Path(cwd_tmp) / ".rag_kb"
                config_dir.mkdir()
                config_path = config_dir / "config.yaml"
                config_path.write_text(
                    "retrieval:\n  default_top_k: 25\n",
                    encoding="utf-8",
                )
                old_home = os.environ.get("HOME")
                os.environ["HOME"] = str(cwd_tmp)
                try:
                    settings = Settings()
                    self.assertEqual(settings.retrieval.default_top_k, 25)
                finally:
                    if old_home is None:
                        os.environ.pop("HOME", None)
                    else:
                        os.environ["HOME"] = old_home
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
