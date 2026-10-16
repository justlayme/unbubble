"""Make iPhone media viewable everywhere.

Chrome, Edge and Firefox can't render HEIC, so photos are converted to JPEG for display (the
original is always kept in exports). Thumbnails are cached per archive.
"""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Optional

try:
    from PIL import Image, ImageOps

    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
        HEIF = True
    except Exception:
        HEIF = False
    PIL_OK = True
except Exception:  # pragma: no cover - Pillow is a hard dependency in packaged builds
    PIL_OK = False
    HEIF = False

BROWSER_IMAGE = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/avif", "image/bmp", "image/svg+xml"}
HEIC_SUFFIXES = {".heic", ".heif", ".hif"}


def guess_mime(path: Path, declared: Optional[str]) -> str:
    if declared:
        return declared
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def is_image(path: Path, mime: Optional[str]) -> bool:
    m = guess_mime(path, mime)
    return m.startswith("image/") or path.suffix.lower() in HEIC_SUFFIXES


def needs_conversion(path: Path, mime: Optional[str]) -> bool:
    m = guess_mime(path, mime)
    return path.suffix.lower() in HEIC_SUFFIXES or (m.startswith("image/") and m not in BROWSER_IMAGE)


def browser_image(path: Path, mime: Optional[str], cache_dir: Path, max_edge: Optional[int] = None) -> Optional[tuple[Path, str]]:
    """Return a browser-safe version of an image (and its mime), converting/resizing if needed."""
    if not PIL_OK or not is_image(path, mime):
        return None
    if not needs_conversion(path, mime) and max_edge is None:
        return path, guess_mime(path, mime)
    key = hashlib.sha1(f"{path}|{path.stat().st_mtime_ns}|{max_edge}".encode()).hexdigest()[:20]
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"{key}.jpg"
    if out.exists():
        return out, "image/jpeg"
    try:
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)
            if max_edge:
                img.thumbnail((max_edge, max_edge))
            if img.mode not in ("RGB", "L"):
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode in ("RGBA", "LA", "P"):
                    img = img.convert("RGBA")
                    background.paste(img, mask=img.split()[-1])
                    img = background
                else:
                    img = img.convert("RGB")
            img.save(out, "JPEG", quality=86, optimize=True)
        return out, "image/jpeg"
    except Exception:
        return None
