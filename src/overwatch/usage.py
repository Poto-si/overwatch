"""Normalize event timestamps and token usage for observed sessions."""

from datetime import datetime

TOKEN_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")


def timestamp(value):
    if isinstance(value, (int, float)):
        return value / 1000 if value > 10**11 else float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def usage(value):
    if not isinstance(value, dict):
        return None
    result = {key: max(0, value.get(key, 0)) if type(value.get(key, 0)) is int else 0 for key in TOKEN_KEYS}
    # Cached input and reasoning output are subsets, not additional tokens.
    result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result
