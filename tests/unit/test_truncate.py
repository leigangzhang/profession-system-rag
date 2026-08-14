from __future__ import annotations

import unittest

from rag_notion_kb.retrieval.truncate import TokenTruncator

# tiktoken may be installed but unable to download encoding data in a
# network-restricted sandbox. Determine actual usability once at import time.
try:
    import tiktoken

    tiktoken.get_encoding("cl100k_base")
    _TIKTOKEN_USABLE = True
except Exception:
    _TIKTOKEN_USABLE = False


class TestTokenTruncator(unittest.TestCase):
    def _fallback_truncator(self) -> TokenTruncator:
        """Return a truncator that uses the character-level fallback."""
        truncator = TokenTruncator()
        truncator._encoder = None
        return truncator

    def test_short_text_unchanged(self) -> None:
        truncator = TokenTruncator()
        text = "Short text."
        result = truncator.truncate(text, max_tokens=100)
        self.assertEqual(result, text)

    def test_long_text_truncated(self) -> None:
        truncator = TokenTruncator()
        text = "A" * 1000
        result = truncator.truncate(text, max_tokens=100)
        # Budget must be respected whether tiktoken or character fallback is active.
        self.assertLessEqual(truncator.count(result), 100)
        self.assertIn("(...部分内容省略...)", result)

    def test_head_and_tail_preserved(self) -> None:
        truncator = self._fallback_truncator()
        head = "HEAD " * 20
        tail = "TAIL " * 20
        text = head + "MIDDLE " * 100 + tail
        result = truncator.truncate(text, max_tokens=200)
        self.assertIn("HEAD", result)
        self.assertIn("TAIL", result)
        self.assertNotIn("MIDDLE", result)

    def test_chinese_text(self) -> None:
        truncator = TokenTruncator()
        text = "开头" * 100 + "中间" * 400 + "结尾" * 100
        result = truncator.truncate(text, max_tokens=300)
        # tiktoken counts CJK as multiple tokens; when unavailable the character
        # fallback still produces a result within the requested budget.
        self.assertLessEqual(truncator.count(result), 300)
        self.assertIn("(...部分内容省略...)", result)

    @unittest.skipUnless(
        _TIKTOKEN_USABLE,
        "tiktoken encoding data not available (network/DNS blocked)",
    )
    def test_tiktoken_encoder_loaded(self) -> None:
        truncator = TokenTruncator()
        self.assertIsNotNone(truncator._encoder)

    @unittest.skipUnless(
        _TIKTOKEN_USABLE,
        "tiktoken encoding data not available (network/DNS blocked)",
    )
    def test_count_english_known_tokens(self) -> None:
        truncator = TokenTruncator()
        # cl100k_base encodes each of these English words as a single token.
        self.assertEqual(truncator.count("hello"), 1)
        self.assertEqual(truncator.count("world"), 1)
        self.assertEqual(truncator.count("hello world"), 2)
        self.assertEqual(truncator.count("foo bar baz"), 3)

    def test_count_cjk_tokens(self) -> None:
        truncator = TokenTruncator()
        count = truncator.count("中文测试")
        self.assertGreater(count, 0)
        # Character fallback yields len(text); tiktoken yields <= len(text).
        self.assertLessEqual(count, len("中文测试"))

    def test_encoder_fallback_when_network_blocked(self) -> None:
        # Even when tiktoken cannot fetch encodings, the constructor must not
        # crash and the component must remain usable.
        truncator = self._fallback_truncator()
        self.assertIsNotNone(truncator)
        self.assertEqual(truncator.count("abc"), 3)
        self.assertLessEqual(truncator.count("hello world"), 11)


if __name__ == "__main__":
    unittest.main()
