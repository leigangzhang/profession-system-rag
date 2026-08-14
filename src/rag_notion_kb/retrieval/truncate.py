from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_ELLIPSIS = "(...部分内容省略...)"


class TokenTruncator:
    """Truncate text to a maximum token budget.

    Uses tiktoken when available; otherwise falls back to character-level
    counting so the component remains testable in environments without
    tiktoken installed or without network access to fetch encoding data.
    """

    def __init__(self, model: str = "cl100k_base") -> None:
        self.model = model
        self._encoder = self._load_encoder()

    def _load_encoder(self):
        try:
            import tiktoken

            return tiktoken.get_encoding(self.model)
        except ImportError:
            logger.warning("tiktoken not installed; using character-level token fallback")
            return None
        except Exception as exc:
            logger.warning(
                "tiktoken encoder %r unavailable (%s: %s); using character-level fallback",
                self.model,
                type(exc).__name__,
                exc,
            )
            return None

    def count(self, text: str) -> int:
        """Return the approximate token count for *text*."""
        if self._encoder is not None:
            return len(self._encoder.encode(text))
        return len(text)

    def truncate(self, text: str, max_tokens: int) -> str:
        """Return *text* truncated to at most *max_tokens* tokens.

        When truncation is necessary, the head and tail are preserved and an
        ellipsis marker is inserted between them.
        """
        if self.count(text) <= max_tokens:
            return text

        keep_each = int(max_tokens * 0.4)
        if self._encoder is not None:
            tokens = self._encoder.encode(text)
            head = self._encoder.decode(tokens[:keep_each])
            tail = self._encoder.decode(tokens[-keep_each:])
        else:
            head = text[:keep_each]
            tail = text[-keep_each:]

        head = self._break_at_boundary(head, from_end=True)
        tail = self._break_at_boundary(tail, from_end=False)
        return f"{head}{_ELLIPSIS}{tail}"

    def _break_at_boundary(self, text: str, from_end: bool) -> str:
        """Trim text to the nearest paragraph boundary without exceeding budget."""
        if from_end:
            idx = text.rfind("\n\n")
            if idx > 0:
                return text[:idx]
            idx = text.rfind("\n")
            if idx > 0:
                return text[:idx]
        else:
            idx = text.find("\n\n")
            if idx >= 0:
                return text[idx + 2 :]
            idx = text.find("\n")
            if idx >= 0:
                return text[idx + 1 :]
        return text
