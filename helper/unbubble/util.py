"""Small shared helpers: Apple timestamps, address normalisation, per-platform paths."""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

APPLE_EPOCH_OFFSET = 978_307_200  # seconds between 1970-01-01 and 2001-01-01

APP_NAME = "Unbubble"


def apple_to_unix_ms(value: Optional[float]) -> Optional[int]:
    """Convert a Messages timestamp to Unix milliseconds.

    Since iOS 11 / macOS 10.13 the value is nanoseconds since 2001-01-01; older databases store
    seconds. Zero and NULL mean "not set".
    """
    if not value:
        return None
    seconds = value / 1e9 if abs(value) > 1e11 else float(value)
    return int(round((seconds + APPLE_EPOCH_OFFSET) * 1000))


def iso_from_ms(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


_NON_DIGIT = re.compile(r"\D+")


def address_key(address: Optional[str]) -> str:
    """A stable matching key for a phone number or email.

    Emails are lower-cased. Phone numbers are reduced to digits and matched on their last ten
    digits, which absorbs country-code and formatting differences between the Messages database
    and the address book without pulling in carrier-specific rules.
    """
    if not address:
        return ""
    address = address.strip()
    if "@" in address:
        return address.lower()
    digits = _NON_DIGIT.sub("", address)
    if not digits:
        return address.lower()
    return digits[-10:] if len(digits) > 10 else digits


def pretty_address(address: Optional[str], region: str = "US") -> str:
    """Format a phone number for display; leave emails and short codes alone."""
    if not address:
        return "Unknown"
    if "@" in address:
        return address
    try:
        import phonenumbers

        parsed = phonenumbers.parse(address, region)
        if phonenumbers.is_possible_number(parsed):
            fmt = (
                phonenumbers.PhoneNumberFormat.NATIONAL
                if phonenumbers.region_code_for_number(parsed) == region
                else phonenumbers.PhoneNumberFormat.INTERNATIONAL
            )
            return phonenumbers.format_number(parsed, fmt)
    except Exception:
        pass
    return address


def data_dir() -> Path:
    """Where captured archives live. Created with owner-only permissions."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "unbubble"
    override = os.environ.get("GM_DATA_DIR")
    if override:
        base = Path(override)
    base.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    return base


def default_export_dir() -> Path:
    from .windows_support import documents_dir

    return documents_dir() / "Unbubble Exports"


_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_filename(name: str, limit: int = 80) -> str:
    """A file or folder name that's valid on Windows, macOS and Linux."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned[:limit].rstrip(" .")  # Windows drops trailing dots and spaces
    if not cleaned:
        return "Untitled"
    if cleaned.split(".")[0].upper() in _RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned
