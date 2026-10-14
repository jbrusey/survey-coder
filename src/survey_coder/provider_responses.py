"""Helpers for normalizing responses from OpenAI-compatible providers."""

from typing import Any, Optional, Tuple


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            text = _get(item, "text")
            if isinstance(text, str):
                parts.append(text)
        if parts:
            return "".join(parts)
    raise TypeError(f"Expected response content to be text, got {type(content).__name__}")


def extract_completion(response: Any) -> Tuple[str, Optional[str]]:
    """Return completion text and finish reason across SDK and server shapes.

    Some OpenWebUI versions/adapters return the completion body directly as a
    string; the OpenAI SDK instead returns an object with ``choices``.
    """
    if isinstance(response, str):
        return response, None

    choices = _get(response, "choices")
    if not isinstance(choices, (list, tuple)) or not choices:
        raise TypeError(
            "Expected an OpenAI-compatible response string or a response with "
            f"a non-empty choices list, got {type(response).__name__}"
        )

    choice = choices[0]
    message = _get(choice, "message")
    content = _get(message, "content") if message is not None else None
    if content is None:
        # Some compatible APIs expose text completions instead of chat messages.
        content = _get(choice, "text")
    if content is None:
        raise TypeError("The first response choice contains neither message content nor text")
    finish_reason = _get(choice, "finish_reason")
    return _content_text(content), finish_reason
