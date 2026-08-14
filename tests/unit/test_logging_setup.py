from __future__ import annotations

import io
import json
import logging
import unittest

from rag_notion_kb.logging_setup import setup_logging


class TestLoggingSetup(unittest.TestCase):
    def test_json_format(self) -> None:
        buf = io.StringIO()
        setup_logging(level="INFO", fmt="json", stream=buf)
        logger = logging.getLogger("test_json")
        logger.info("hello json")
        output = buf.getvalue().strip()
        record = json.loads(output)
        self.assertEqual(record["level"], "INFO")
        self.assertEqual(record["message"], "hello json")
        self.assertIn("module", record)
        self.assertIn("timestamp", record)

    def test_text_format(self) -> None:
        buf = io.StringIO()
        setup_logging(level="INFO", fmt="text", stream=buf)
        logger = logging.getLogger("test_text")
        logger.warning("hello text")
        output = buf.getvalue().strip()
        self.assertIn("WARNING", output)
        self.assertIn("hello text", output)

    def test_setup_replaces_handlers(self) -> None:
        buf = io.StringIO()
        setup_logging(level="INFO", fmt="json", stream=buf)
        root = logging.getLogger()
        first_handler = root.handlers[0]
        setup_logging(level="DEBUG", fmt="text", stream=buf)
        self.assertEqual(len(root.handlers), 1)
        self.assertIsNot(root.handlers[0], first_handler)

    def test_page_id_extra(self) -> None:
        buf = io.StringIO()
        setup_logging(level="INFO", fmt="json", stream=buf)
        logger = logging.getLogger("test_extra")
        logger.info("with context", extra={"page_id": "page-1"})
        output = buf.getvalue().strip()
        record = json.loads(output)
        self.assertEqual(record["page_id"], "page-1")


if __name__ == "__main__":
    unittest.main()
