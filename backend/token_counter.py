from __future__ import annotations

import threading

from backend.models import ChatMessage
from backend.utils import get_logger

logger = get_logger("token_counter")

# tiktoken is heavy to import; lazy-load and cache per encoding name.
_encoder_cache: dict[str, object] = {}
_lock = threading.Lock()


def _get_encoder(encoding_name: str = "cl100k_base"):
    """Return a cached tiktoken encoder. Falls back to a rough estimator on failure."""
    with _lock:
        if encoding_name in _encoder_cache:
            return _encoder_cache[encoding_name]
        try:
            import tiktoken

            enc = tiktoken.get_encoding(encoding_name)
            _encoder_cache[encoding_name] = enc
            return enc
        except Exception as e:
            logger.warning(f"tiktoken unavailable ({e}), using char-based estimator")
            _encoder_cache[encoding_name] = None  # sentinel
            return None


def count_tokens(text: str, encoding_name: str = "cl100k_base") -> int:
    """Count tokens in a plain string."""
    enc = _get_encoder(encoding_name)
    if enc is not None:
        return len(enc.encode(text))
    # Fallback: ~4 chars per token for CJK-heavy text
    return max(1, len(text) // 2)


def count_message_tokens(messages: list[ChatMessage], encoding_name: str = "cl100k_base") -> int:
    """Estimate total tokens for a list of ChatMessages.

    Every message has overhead (~4 tokens for framing in the ChatML format).
    """
    total = 0
    for msg in messages:
        # role + separators overhead
        total += 4
        total += count_tokens(msg.role, encoding_name)
        total += count_tokens(msg.content, encoding_name)
    # Every reply is primed with <|start|>assistant<|message|>
    total += 2
    return total


def count_single_message_tokens(message: ChatMessage, encoding_name: str = "cl100k_base") -> int:
    """Tokens for one ChatMessage (including framing overhead)."""
    return 4 + count_tokens(message.role, encoding_name) + count_tokens(message.content, encoding_name)


def truncate_messages_to_token_budget(
    messages: list[ChatMessage],
    max_tokens: int,
    encoding_name: str = "cl100k_base",
) -> list[ChatMessage]:
    """Keep as many *recent* messages as fit within *max_tokens*.

    Returns a new list, newest messages first (but in chronological order).
    """
    budget = 0
    kept: list[ChatMessage] = []
    # Walk from newest to oldest
    for msg in reversed(messages):
        cost = count_single_message_tokens(msg, encoding_name)
        if budget + cost > max_tokens and kept:
            break
        kept.append(msg)
        budget += cost
    kept.reverse()
    return kept
