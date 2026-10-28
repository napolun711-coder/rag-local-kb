"""Conservative token estimation shared by context builders."""

from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """Estimate tokens as one per CJK character and four per other character."""
    value = str(text or "")
    cjk = 0
    other = 0
    for char in value:
        if _is_cjk(char):
            cjk += 1
        else:
            other += 1
    return cjk + (other + 3) // 4


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x3040 <= code <= 0x30FF
        or 0xAC00 <= code <= 0xD7AF
    )


__all__ = ["estimate_tokens"]