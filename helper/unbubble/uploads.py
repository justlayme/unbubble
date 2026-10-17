"""Videos dropped onto the page, streamed to this PC.

A browser can't give the helper a dropped file's path, so the page streams the file here. Each
upload needs a one-time ticket, which the page can only get over its already-approved connection
(the origin-checked WebSocket or the paired relay), so nothing else on the machine or the web can
push files in. Tickets expire after ten minutes; finished uploads are deleted once read.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .util import data_dir

MAX_BYTES = 8 * 1024 * 1024 * 1024        # an hour of 60 fps screen recording is ~4-6 GB
TICKET_TTL = 600
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}


@dataclass
class Upload:
    token: str
    suffix: str
    name: str
    size: int
    expires: float
    path: Optional[Path] = None
    done: bool = False


_LOCK = threading.Lock()
_UPLOADS: dict[str, Upload] = {}


def _folder() -> Path:
    folder = data_dir() / "tmp"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def new_ticket(name: str, size: int) -> Upload:
    suffix = Path(name).suffix.lower()
    if suffix not in VIDEO_SUFFIXES:
        raise ValueError("Drop the screen recording itself, an .mp4 or .mov video.")
    if size <= 0 or size > MAX_BYTES:
        raise ValueError("That video is too large to read.")
    up = Upload(secrets.token_urlsafe(24), suffix, Path(name).name[:200], size, time.time() + TICKET_TTL)
    with _LOCK:
        _sweep()
        _UPLOADS[up.token] = up
    return up


def claim(token: str) -> Optional[Upload]:
    """The ticket for an upload that is starting; each ticket uploads once."""
    with _LOCK:
        up = _UPLOADS.get(token)
        if up is None or up.path is not None or up.expires < time.time():
            return None
        up.path = _folder() / f"drop-{up.token[:12]}{up.suffix}"
        return up


def finished(token: str) -> Optional[Path]:
    with _LOCK:
        up = _UPLOADS.get(token)
        return up.path if up and up.done and up.path and up.path.exists() else None


def forget(token: str) -> None:
    with _LOCK:
        up = _UPLOADS.pop(token, None)
    if up and up.path:
        up.path.unlink(missing_ok=True)


def _sweep() -> None:
    now = time.time()
    for token, up in list(_UPLOADS.items()):
        if up.expires < now - 3600:
            _UPLOADS.pop(token, None)
            if up.path:
                up.path.unlink(missing_ok=True)


def looks_like_video(head: bytes) -> bool:
    """MP4 and QuickTime files open with an `ftyp` (or, in old QuickTime, `moov`/`wide`/`mdat`) box."""
    return len(head) >= 12 and head[4:8] in (b"ftyp", b"moov", b"wide", b"mdat", b"free", b"skip")
