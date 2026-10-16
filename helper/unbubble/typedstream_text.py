"""Recover plain text from `message.attributedBody`.

Since iOS 16 / macOS Ventura the `text` column is often NULL and the real body is only stored as
an NSAttributedString serialised with NeXTSTEP `typedstream`. We decode it with the `typedstream`
library and fall back to a byte-level reader that understands the one layout Messages uses, so a
library edge case never costs the user a message.
"""

from __future__ import annotations

from typing import Any, Optional

_MARKER = b"NSString"
# Apple appends U+FFFC (object replacement) where an attachment sits inline. Strip it.
_STRIP = "￼�"


def _walk_for_string(obj: Any, depth: int = 0) -> Optional[str]:
    if depth > 12 or obj is None:
        return None
    value = getattr(obj, "value", None)
    if isinstance(value, str) and type(obj).__name__ in ("NSString", "NSMutableString"):
        return value
    if isinstance(obj, (bytes, bytearray)):
        return None
    if isinstance(obj, str):
        return None
    contents = getattr(obj, "contents", None)
    candidates = contents if isinstance(contents, (list, tuple)) else []
    if not candidates and isinstance(obj, (list, tuple)):
        candidates = obj
    for child in candidates:
        inner = getattr(child, "value", child)
        found = _walk_for_string(inner, depth + 1)
        if found is not None:
            return found
        if inner is not child:
            found = _walk_for_string(child, depth + 1)
            if found is not None:
                return found
    for attr in ("values", "elements"):
        seq = getattr(obj, attr, None)
        if isinstance(seq, (list, tuple)):
            for child in seq:
                found = _walk_for_string(child, depth + 1)
                if found is not None:
                    return found
    return None


def _heuristic(blob: bytes) -> Optional[str]:
    """Byte-level reader: `NSString` class record, 5 bytes of class/version info, then a length.

    Lengths below 0x80 are a single byte; 0x81 prefixes a little-endian uint16 and 0x82 a uint32.
    """
    idx = blob.find(_MARKER)
    if idx < 0:
        return None
    pos = idx + len(_MARKER) + 5
    if pos >= len(blob):
        return None
    lead = blob[pos]
    if lead == 0x81:
        length = int.from_bytes(blob[pos + 1 : pos + 3], "little")
        start = pos + 3
    elif lead == 0x82:
        length = int.from_bytes(blob[pos + 1 : pos + 5], "little")
        start = pos + 5
    else:
        length = lead
        start = pos + 1
    raw = blob[start : start + length]
    if len(raw) != length:
        return None
    return raw.decode("utf-8", errors="replace")


def decode_attributed_body(blob: Optional[bytes]) -> Optional[str]:
    if not blob:
        return None
    text: Optional[str] = None
    try:
        import typedstream

        text = _walk_for_string(typedstream.unarchive_from_data(bytes(blob)))
    except Exception:
        text = None
    if text is None:
        text = _heuristic(bytes(blob))
    if text is None:
        return None
    for ch in _STRIP:
        text = text.replace(ch, "")
    return text.strip() or None


def best_text(text: Optional[str], attributed_body: Optional[bytes]) -> Optional[str]:
    """Prefer the `text` column, fall back to `attributedBody`, and drop placeholder glyphs."""
    if text:
        cleaned = text
        for ch in _STRIP:
            cleaned = cleaned.replace(ch, "")
        cleaned = cleaned.strip()
        if cleaned:
            return cleaned
    return decode_attributed_body(attributed_body)
