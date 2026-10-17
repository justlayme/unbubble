"""Turn a screen recording of an iPhone Messages thread into a conversation.

Someone opens a thread on their iPhone, starts a screen recording from Control
Center and flicks back through the history. This reads that video on the PC,
with no network: who sent each message, what it said and the time header it
sits under, in order, once each.

How it works
------------
1. Motion pass. Every frame is shrunk and compared with the one before it.
   Phase correlation gives how far the thread scrolled between them, so every
   frame gets an offset into one long "content" coordinate. Rows that never
   move are the status bar, the contact header and the text field; the rows
   between them are the scrolling thread.
2. Key frames. Frames are chosen so every stretch of the thread is seen whole at
   least once, preferring frames where the thread was still (sharper after
   video compression).
3. Reading. In each key frame the bubbles are found by colour (blue or green
   from you, grey from them) and the frame is run through on-device OCR. Lines
   inside a bubble are its text; centred lines between bubbles are the time
   headers; small grey lines above grey bubbles are group-chat sender names.
   Anything cut off by the top or bottom of the thread is left for a frame
   where it is whole.
4. Stitching. Everything is placed at its content position. The same bubble
   seen in many frames lands in the same place and is kept once; two identical
   messages sent at different times land in different places and are both kept.

Times come from Messages' own headers ("Today 9:41 AM", "Saturday 1:30 AM",
"Sat, Oct 11 at 3:20 PM"), so every message under a header carries that time.
"""

from __future__ import annotations

import re
import unicodedata
import struct
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Optional

ProgressFn = Callable[[float, str], None]

SMALL_WIDTH = 220            # motion pass works on frames this wide
CLIP_MARGIN = 4              # px: a bubble this close to the thread's edge is cut off
MERGE_TOLERANCE = 28         # px in content space: same bubble seen in two frames
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}
OCR_SCALE = float(__import__("os").environ.get("GM_OCR_SCALE", "1.0"))      # OCR on a scaled copy of the thread
KEY_ADVANCE = float(__import__("os").environ.get("GM_KEY_ADVANCE", "0.3"))  # new thread a key frame must add


class RecordingError(Exception):
    """Something the person can act on, in their words."""


# ---------------------------------------------------------------------------------------------
# Results


@dataclass
class ReadMessage:
    text: str
    from_me: bool
    sender: str                       # "Me" or the person's name (or the thread title)
    ts: Optional[datetime]            # the header time this message sits under
    header: Optional[str] = None      # that header, as Messages showed it
    confidence: float = 1.0


@dataclass
class RecordingResult:
    title: str
    is_group: bool
    messages: list[ReadMessage]
    recorded_at: Optional[datetime]
    frames: int
    key_frames: int
    duration_s: float
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------------
# Time headers


_TIME = r"(?P<h>\d{1,2})[:.](?P<m>\d{2})\s*(?P<ampm>[AaPp]\.?\s?[Mm]\.?)?"
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_HEADER_PATTERNS = [
    re.compile(rf"^(?P<rel>today|yesterday)\s*,?\s*{_TIME}$", re.I),
    re.compile(rf"^(?P<wd>{'|'.join(_WEEKDAYS)})\s*,?\s*{_TIME}$", re.I),
    # "Sat, Oct 11 at 3:20 PM"   "Oct 11 at 3:20 PM"   "Oct 3, 2025 at 3:20 PM"
    re.compile(rf"^(?:[a-z]{{3,9}},?\s+)?(?P<mon>[a-z]{{3,9}})\.?\s+(?P<d>\d{{1,2}})(?:,\s*(?P<y>\d{{4}}))?\s*(?:at\s*)?,?\s*{_TIME}$", re.I),
    # "11 Oct at 15:20" (day-first regions)
    re.compile(rf"^(?:[a-z]{{3,9}},?\s+)?(?P<d>\d{{1,2}})\s+(?P<mon>[a-z]{{3,9}})\.?(?:\s+(?P<y>\d{{4}}))?\s*(?:at\s*)?,?\s*{_TIME}$", re.I),
]


def _normalize_header(text: str) -> str:
    """Undo OCR habits on small grey text: dropped spaces, 0 for O, l/I for 1."""
    t = text.replace(" ", " ").replace(" ", " ")
    t = re.sub(r"(?<=[A-Za-z])0(?=[a-z])|(?<![\w])0(?=ct\b|ct\d)", "O", t)    # 0ct -> Oct
    t = re.sub(r"(?<=\d)[lI|](?=\d|:)|(?<=[:.])[lI|](?=\d)|(?<=\d[:.]\d)[lI|]", "1", t)
    t = re.sub(r",(?=\S)", ", ", t)
    t = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", t)
    t = re.sub(r"(?<=\d)(?=[A-Za-z])", " ", t)
    t = re.sub(r"(?<=\d) (?=[AaPp]\.?\s?[Mm]\b)", "", t)                     # "4:32 PM" stays matchable
    t = re.sub(r"\b(at)(?=\d)", r"\1 ", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip(" ·•")


def parse_header(text: str, recorded_at: datetime) -> Optional[datetime]:
    """The moment a Messages time header stands for, relative to when it was recorded."""
    cleaned = _normalize_header(text)
    for pattern in _HEADER_PATTERNS:
        m = pattern.match(cleaned)
        if not m:
            continue
        hour, minute = int(m["h"]), int(m["m"])
        ampm = (m["ampm"] or "").lower().replace(".", "").replace(" ", "")
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            return None
        groups = m.groupdict()
        base = recorded_at.date()
        if groups.get("rel"):
            day = base - timedelta(days=0 if groups["rel"].lower() == "today" else 1)
        elif groups.get("wd"):
            target = _WEEKDAYS.index(groups["wd"].lower())
            back = (base.weekday() - target) % 7 or 7     # a weekday name is never today
            day = base - timedelta(days=back)
        else:
            mon = groups["mon"][:3].lower()
            if mon not in _MONTHS:
                return None
            year = int(groups["y"]) if groups.get("y") else base.year
            try:
                day = base.replace(year=year, month=_MONTHS.index(mon) + 1, day=int(groups["d"]))
            except ValueError:
                return None
            if not groups.get("y") and day > base:
                day = day.replace(year=year - 1)
        return datetime(day.year, day.month, day.day, hour, minute)
    return None


_STATUS = re.compile(r"^(delivered|read|sent|not delivered|delivered quietly|edited|kept)\b", re.I)


# ---------------------------------------------------------------------------------------------
# Video plumbing


def recording_created_at(path: Path) -> Optional[datetime]:
    """When the recording was made, from the QuickTime/MP4 `mvhd` atom (UTC, since 1904)."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(64 * 1024 * 1024) if path.stat().st_size > 64 * 1024 * 1024 else fh.read()
    except OSError:
        return None

    def walk(buf: bytes, start: int, end: int) -> Optional[int]:
        i = start
        while i + 8 <= end:
            size, kind = struct.unpack(">I4s", buf[i:i + 8])
            header = 8
            if size == 1 and i + 16 <= end:
                size = struct.unpack(">Q", buf[i + 8:i + 16])[0]
                header = 16
            elif size == 0:
                size = end - i
            if size < header:
                return None
            if kind == b"moov":
                return walk(buf, i + header, min(end, i + size))
            if kind == b"mvhd":
                body = buf[i + header:i + size]
                if not body:
                    return None
                return struct.unpack(">Q", body[4:12])[0] if body[0] == 1 else struct.unpack(">I", body[4:8])[0]
            i += size
        return None

    seconds = walk(data, 0, len(data))
    if not seconds or seconds < 3_000_000_000:          # unset, or before ~1999
        return None
    utc = datetime(1904, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)
    return utc.astimezone().replace(tzinfo=None)         # the PC's local time


def _cv2():
    import cv2
    return cv2


# ---------------------------------------------------------------------------------------------
# OCR


def _new_engine():
    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    # Messages text on an iPhone recording is already large, so detection never needs the default
    # upscale to a 736 px short side; cap the long side instead (much faster, same accuracy).
    for op in getattr(getattr(engine, "text_detector", None), "preprocess_op", []):
        if hasattr(op, "limit_type") and hasattr(op, "limit_side_len"):
            op.limit_type, op.limit_side_len = "max", 1280
    return engine


class _Ocr:
    """On-device OCR (RapidOCR, ONNX). Loaded once; models ship with the app."""

    _engine = None
    _lock = threading.Lock()

    @classmethod
    def lines(cls, image) -> list[tuple[float, float, float, float, str, float]]:
        with cls._lock:
            if cls._engine is None:
                from rapidocr_onnxruntime import RapidOCR
                cls._engine = _new_engine()
            result, _ = cls._engine(image, use_cls=False)
        out = []
        for box, text, score in result or []:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            out.append((min(xs), min(ys), max(xs), max(ys), str(text).strip(), float(score)))
        return out

    @classmethod
    def warm_up(cls) -> None:
        """Load the models in the background while the motion pass runs (about 3 s saved)."""
        def load() -> None:
            with cls._lock:
                if cls._engine is None:
                    cls._engine = _new_engine()
        threading.Thread(target=load, name="ocr-warm-up", daemon=True).start()

    @classmethod
    def lines_scaled(cls, image, scale: float):
        """Detect and read lines on a scaled copy (faster), in the original image's coordinates."""
        if abs(scale - 1.0) < 0.01:
            return cls.lines(image)
        cv2 = _cv2()
        small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        return [(x0 / scale, y0 / scale, x1 / scale, y1 / scale, t, s) for x0, y0, x1, y1, t, s in cls.lines(small)]

    @classmethod
    def recognize(cls, image) -> tuple[str, float]:
        """Read one line of text with no detection step (for a word too short to be detected)."""
        with cls._lock:
            if cls._engine is None:
                from rapidocr_onnxruntime import RapidOCR
                cls._engine = _new_engine()
            result = cls._engine.text_recognizer([image])[0]
        if not result:
            return "", 0.0
        text, score = result[0]
        return str(text).strip(), float(score)


# ---------------------------------------------------------------------------------------------
# Per-frame reading


@dataclass
class _Item:
    kind: str                 # "msg" | "time" | "name"
    y0: float                 # content coordinates once placed
    y1: float
    text: str
    score: float
    side: str = ""            # "me" | "them" for messages
    x0: float = 0.0


def _find_bubbles(view, dark: bool):
    """Bubble rectangles in the thread area: (x0, y0, x1, y1, side)."""
    cv2 = _cv2()
    import numpy as np

    hsv = cv2.cvtColor(view, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    blue = (h >= 98) & (h <= 125) & (s >= 110) & (v >= 140)
    green = (h >= 50) & (h <= 85) & (s >= 90) & (v >= 110)
    if dark:
        grey = (s <= 40) & (v >= 28) & (v <= 62)
    else:
        grey = (s <= 30) & (v >= 205) & (v <= 243)
    found = []
    for mask, side in ((blue | green, "me"), (grey, "them")):
        m = mask.astype(np.uint8) * 255
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        width = view.shape[1]
        for c in contours:
            x, y, w, hh = cv2.boundingRect(c)
            if w < width * 0.06 or hh < 30 or w > width * 0.92:
                continue
            if cv2.contourArea(c) < 0.55 * w * hh:     # not a filled rounded rectangle
                continue
            found.append((x, y, x + w, y + hh, side))
    return found


def _bubble_colour_mask(view, dark: bool):
    """True where a pixel is bubble-coloured (blue, green or the grey of the theme)."""
    cv2 = _cv2()
    hsv = cv2.cvtColor(view, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    blue = (h >= 98) & (h <= 125) & (s >= 110) & (v >= 140)
    green = (h >= 50) & (h <= 85) & (s >= 90) & (v >= 110)
    grey = ((s <= 40) & (v >= 28) & (v <= 62)) if dark else ((s <= 30) & (v >= 205) & (v <= 243))
    return blue | green | grey


def _on_bubble(mask, x0: float, y0: float, x1: float, y1: float) -> bool:
    """Is this text line printed on a bubble (rather than the thread background)?"""
    h, w = mask.shape
    pad = 6
    samples = []
    for xa, xb, ya, yb in (
        (x0 - pad * 2, x0 - pad, y0, y1),          # just left of the text
        (x1 + pad, x1 + pad * 2, y0, y1),          # just right of it
        (x0, x1, y0 - pad, y0 - 2),                # just above
        (x0, x1, y1 + 2, y1 + pad),                # just below
    ):
        xa, xb = int(max(0, xa)), int(min(w, xb))
        ya, yb = int(max(0, ya)), int(min(h, yb))
        if xb > xa and yb > ya:
            samples.append(mask[ya:yb, xa:xb].mean())
    return bool(samples) and sorted(samples)[len(samples) // 2] > 0.5


def _looks_like_emoji(crop, side: str) -> bool:
    """A bubble OCR found nothing in, but with colourful glyphs on it (yellow faces, hearts…)."""
    cv2 = _cv2()
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    bubble_hue = (h >= 98) & (h <= 125) if side == "me" else (s < 45)
    colourful = (s >= 120) & (v >= 120) & ~bubble_hue
    return colourful.mean() > 0.02


def _read_bubble_again(crop) -> tuple[str, float]:
    """Second look at a bubble OCR found no text in: a one-line bubble ("k", "ok") is read
    directly with the bubble's padding trimmed; anything taller is zoomed and detected again."""
    cv2 = _cv2()
    h, w = crop.shape[:2]
    dy, dx = int(h * 0.15), int(min(w * 0.2, h * 0.33))
    # A lone letter reads inconsistently depending on framing, so read a few framings and take
    # what they agree on.
    framings = [crop[dy:h - dy, dx:w - dx], crop[int(h * 0.22):h - int(h * 0.22), int(w * 0.25):w - int(w * 0.25)], crop,
                cv2.cvtColor(255 - cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)]
    votes: dict[str, list[float]] = {}
    for f in framings:
        if f.shape[0] < 8 or f.shape[1] < 8:
            continue
        t, s = _Ocr.recognize(f)
        t = unicodedata.normalize("NFKC", t).strip()
        if t:
            votes.setdefault(t, []).append(s)
    if votes:
        text, scores = max(votes.items(), key=lambda kv: (len(kv[1]), sum(kv[1])))
        # One- to three-letter replies ("k", "ok", "no") read with low confidence but correctly.
        if max(scores) >= 0.3 or (len(scores) >= 2 and len(text) <= 3 and h < 130):
            return text, max(0.3, max(scores))
    zoom = cv2.resize(crop, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    pad = cv2.copyMakeBorder(zoom, 40, 40, 60, 60, cv2.BORDER_REPLICATE)
    lines = _Ocr.lines(pad)
    lines.sort(key=lambda l: (l[1], l[0]))
    text = " ".join(l[4] for l in lines if l[4]).strip()
    return text, min((l[5] for l in lines), default=0.0)


def _fix_spaces(view, box, text: str) -> str:
    """OCR models sometimes run English words together on compressed video ("Oklet'stalktomorrow").
    The gaps between words are plain in the pixels, so when a line has more word gaps than its
    text has spaces, read each word on its own and join them."""
    import numpy as np

    cv2 = _cv2()
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    h = y1 - y0
    if h < 12 or x1 - x0 < 3 * h:
        return text
    gray = cv2.cvtColor(view[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.int16)
    bg = int(np.median(gray))
    ink = np.abs(gray - bg) > 45
    cols = ink.any(axis=0)
    # Runs of empty columns between inked ones. The spaces OCR did find calibrate how wide a
    # word space is in this line, so a gap after an apostrophe or a wide letter isn't taken for one.
    runs: list[tuple[int, int]] = []          # (start column of the gap, width)
    seen_ink, empty = False, 0
    for x, on in enumerate(cols):
        if on:
            if seen_ink and empty:
                runs.append((x - empty, empty))
            seen_ink, empty = True, 0
        else:
            empty += 1
    known = text.count(" ")
    widths = sorted((w for _, w in runs), reverse=True)
    if known and len(widths) > known:
        gap = max(5, int(widths[known - 1] * 0.75))
    else:
        gap = max(6, int(h * 0.24))
    words: list[tuple[int, int]] = []
    left = next((x for x, on in enumerate(cols) if on), 0)
    for start, width in runs:
        if width >= gap:
            words.append((left, start))
            left = start + width
    right = len(cols) - next((i for i, on in enumerate(cols[::-1]) if on), 0)
    words.append((left, right))
    if len(words) <= len(text.split()):
        return _space_after_sentence(text)
    parts = []
    for a, b in words:
        pad = max(2, h // 6)
        crop = view[y0:y1, max(0, x0 + a - pad):min(view.shape[1], x0 + b + pad)]
        word, _ = _Ocr.recognize(crop)
        word = re.sub(r"\s+", "", word)
        if word:
            parts.append(word)
    if len(parts) < 2:
        return text
    # The whole-line reading has the better letters (it reads each word in context: "I'm", not
    # "'m"); the word-by-word reading only says where the words break. Keep the line's letters
    # and add the breaks it missed.
    letters = re.sub(r"\s+", "", text)
    breaks = set()
    pos = 0
    for ch in text:
        if ch.isspace():
            breaks.add(pos)
        else:
            pos += 1
    joined = "".join(parts)
    if _similar(joined.lower(), letters.lower()) < 0.8:
        return text
    word_starts = []
    acc = 0
    for p in parts[:-1]:
        acc += len(p)
        word_starts.append(acc)
    blocks = SequenceMatcher(None, letters.lower(), joined.lower(), autojunk=False).get_matching_blocks()
    for start in word_starts:
        for blk in blocks:
            if blk.b < start < blk.b + blk.size or (blk.b == start and blk.size):
                breaks.add(blk.a + (start - blk.b))
                break
    out = []
    for i, ch in enumerate(letters):
        # Never inside a contraction ("don't", "I'm") or before punctuation.
        if i in breaks and i > 0 and "'" not in (letters[i - 1], ch) and "’" not in (letters[i - 1], ch) \
                and ch not in ".,!?;:)" and letters[i - 1] not in "(":
            out.append(" ")
        out.append(ch)
    return _space_after_sentence("".join(out))


def _space_after_sentence(text: str) -> str:
    """"tomorrow?I" -> "tomorrow? I": a capital straight after a sentence's end is a new sentence."""
    return re.sub(r"(?<=[a-z][?!.,])(?=[A-Z])", " ", text)


def _trailing_emoji(view, bubble, last_line, side: str) -> bool:
    """Colourful glyphs between the end of the bubble's last text line and its right edge."""
    bx0, by0, bx1, by1 = bubble
    x0, y0, x1, y1 = last_line
    xa, xb = int(x1) + 2, int(bx1) - 6
    ya, yb = int(max(by0, y0)), int(min(by1, y1))
    if xb - xa < 12 or yb - ya < 8:
        return False
    return _looks_like_emoji(view[ya:yb, xa:xb], side)


def _read_frame(frame, top: int, bottom: int, dark: bool) -> list[_Item]:
    """Bubbles, headers and sender names in one frame, in screen coordinates."""
    view = frame[top:bottom]
    height, width = view.shape[:2]
    bubbles = _find_bubbles(view, dark)
    lines = _Ocr.lines_scaled(view, OCR_SCALE)
    mask = _bubble_colour_mask(view, dark)
    items: list[_Item] = []
    claimed = [False] * len(lines)
    whole: list[tuple[int, int, int, int, str]] = []
    tops_visible = [b for b in bubbles if b[1] > CLIP_MARGIN]
    for bx0, by0, bx1, by1, side in bubbles:
        inside = []
        for i, (x0, y0, x1, y1, text, score) in enumerate(lines):
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if bx0 - 4 <= cx <= bx1 + 4 and by0 - 4 <= cy <= by1 + 4:
                inside.append((y0, x0, text, score, x1, y1))
                claimed[i] = True
        if by0 <= CLIP_MARGIN or by1 >= height - CLIP_MARGIN:
            continue                                   # cut off: a later frame shows it whole
        inside = [(y0, x0, _fix_spaces(view, (x0, y0, x1, y1), t), s, x1, y1) for (y0, x0, t, s, x1, y1) in inside]
        whole.append((bx0, by0, bx1, by1, side))
        inside.sort()
        text = " ".join(l[2] for l in inside).strip()
        score = min((l[3] for l in inside), default=0.0)
        if text:
            last = inside[-1]
            if _trailing_emoji(view, (bx0, by0, bx1, by1), (last[1], last[0], last[4], last[5]), side):
                text += " [Emoji]"
        if not text:
            crop = view[by0:by1, bx0:bx1]
            if (by1 - by0) > 220:
                text, score = "[Photo or attachment]", 0.5
            elif _looks_like_emoji(crop, side):
                text, score = "[Emoji]", 0.5
            else:
                text, score = _read_bubble_again(crop)
                if not text:
                    text, score = "[Unreadable message]", 0.1
        items.append(_Item("msg", by0, by1, text, score, side, bx0))

    bubble_line_h = [y1 - y0 for (x0, y0, x1, y1, t, s), c in zip(lines, claimed) if c]
    typical = sorted(bubble_line_h)[len(bubble_line_h) // 2] if bubble_line_h else 40
    for i, (x0, y0, x1, y1, text, score) in enumerate(lines):
        if claimed[i] or not text or y0 <= CLIP_MARGIN or y1 >= height - CLIP_MARGIN:
            continue
        if _STATUS.match(text) or _on_bubble(mask, x0, y0, x1, y1):
            continue                                   # receipts, or text of a bubble we couldn't outline
        cx = (x0 + x1) / 2
        if abs(cx - width / 2) < width * 0.14 and x0 > width * 0.12 and x1 < width * 0.88:
            items.append(_Item("time", y0, y1, text, score))
            continue
        if x0 > width * 0.3 or x1 > width * 0.75 or (y1 - y0) > typical * 0.9:
            continue
        # A group sender's name sits directly above the first of their grey bubbles, at its indent.
        below = [b for b in tops_visible if b[4] == "them" and 0 <= b[1] - y1 <= typical * 1.2]
        if any(abs(b[0] - x0) < width * 0.08 for b in below):
            items.append(_Item("name", y0, y1, text, score, x0=x0))
    for it in items:
        it.y0 += top
        it.y1 += top
    return items


# ---------------------------------------------------------------------------------------------
# The reader


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _bars(motion, height: int) -> tuple[int, int]:
    """Top and bottom of the scrolling thread, from how much each row moved."""
    import numpy as np

    rows = np.asarray(motion, dtype=float)
    n = len(rows)
    centre = rows[int(n * 0.3):int(n * 0.7)]
    if centre.size == 0 or centre.mean() <= 0:
        return int(height * 0.165), int(height * 0.90)
    moving = rows > centre.mean() * 0.35
    top = int(n * 0.3)
    while top > 0 and moving[top - 1]:
        top -= 1
    bottom = int(n * 0.7)
    while bottom < n and moving[bottom]:
        bottom += 1
    scale = height / n
    t, b = int(top * scale), int(bottom * scale)
    # Translucent bars let blurred content through; keep inside sane iOS bounds.
    t = min(max(t, int(height * 0.08)), int(height * 0.26))
    b = max(min(b, int(height * 0.95)), int(height * 0.74))
    return t, b


def read_recording(
    path: Path,
    *,
    progress: ProgressFn = lambda f, s: None,
    cancel: Optional[threading.Event] = None,
    recorded_at: Optional[datetime] = None,
) -> RecordingResult:
    cv2 = _cv2()
    import numpy as np

    path = Path(path)
    if path.suffix.lower() not in VIDEO_SUFFIXES:
        raise RecordingError("Choose the screen recording itself, an .mp4 or .mov video.")
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RecordingError("This video couldn't be opened. Screen recordings from an iPhone are .mp4 or .mov files.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if width > height:
        raise RecordingError("This video is landscape. Record the thread with the iPhone held upright.")
    recorded_at = recorded_at or recording_created_at(path) or datetime.fromtimestamp(path.stat().st_mtime)

    _Ocr.warm_up()

    # ---- 1. motion pass ------------------------------------------------------------------
    small_h = max(1, round(height * SMALL_WIDTH / width))
    scale = width / SMALL_WIDTH
    band = slice(int(small_h * 0.30), int(small_h * 0.78))
    window = cv2.createHanningWindow((SMALL_WIDTH, band.stop - band.start), cv2.CV_32F)
    motion = np.zeros(small_h)
    offsets: list[float] = []       # content offset of each frame, full-res px (content y = y - offset)
    stillness: list[float] = []
    prev = None
    offset = 0.0
    bright = []
    index = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(cv2.resize(frame, (SMALL_WIDTH, small_h), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        g = g.astype(np.float32)
        if prev is not None:
            diff = np.abs(g - prev)
            motion += diff.mean(axis=1)
            (dx, dy), response = cv2.phaseCorrelate(prev[band], g[band], window)
            if response < 0.08 or abs(dy) > (band.stop - band.start) * 0.45:
                dy = 0.0 if diff[band].mean() < 1.0 else dy
            offset += dy * scale
            stillness.append(abs(dy))
        else:
            stillness.append(0.0)
        offsets.append(offset)
        if index % 15 == 0:
            bright.append(float(np.median(g[band, : max(2, SMALL_WIDTH // 25)])))
        prev = g
        index += 1
        if total and index % 30 == 0:
            progress(0.05 + 0.25 * index / total, "Following the scroll")
        if cancel is not None and cancel.is_set():
            raise RecordingError("Stopped.")
    cap.release()
    frames = len(offsets)
    if frames < 2:
        raise RecordingError("This video has no frames to read.")
    top, bottom = _bars(motion, height)
    dark = (sorted(bright)[len(bright) // 2] if bright else 255) < 110
    view_h = bottom - top

    # ---- 2. key frames: see every stretch of the thread whole at least once ----------------
    covered: list[tuple[float, float]] = []

    def uncovered(lo: float, hi: float) -> float:
        need = hi - lo
        for a, b in covered:
            overlap = min(hi, b) - max(lo, a)
            if overlap > 0:
                need -= overlap
        return need

    def cover(lo: float, hi: float) -> None:
        covered.append((lo, hi))
        covered.sort()
        merged = [covered[0]]
        for a, b in covered[1:]:
            if a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        covered[:] = merged

    picks: list[int] = []
    margin = view_h * 0.12          # whole bubbles need room above and below

    def window(i: int) -> tuple[float, float]:
        return top - offsets[i] + margin, bottom - offsets[i] - margin

    def touching(lo: float, hi: float) -> bool:
        """Overlaps what is already read, so nothing between them goes unseen."""
        return not covered or max(min(hi, b) - max(lo, a) for a, b in covered) >= view_h * 0.15

    def commit(c: int) -> None:
        picks.append(c)
        cover(*window(c))

    candidate: Optional[int] = None
    since = 0
    retried = False
    i = 0
    while i < frames:
        lo, hi = window(i)
        need = uncovered(lo, hi) > view_h * KEY_ADVANCE
        if need and touching(lo, hi):
            # Wait a few frames for a stiller one (sharper after compression), never past the edge.
            if candidate is None:
                candidate, since = i, i
            elif stillness[i] <= stillness[candidate] + 0.3:
                candidate = i
            if stillness[i] < 0.6 or i - since >= 5:
                commit(candidate)
                candidate = None
            i += 1
            retried = False
        elif need:
            # Scrolled past the edge of what has been read: take what we have, then look again.
            if candidate is not None:
                commit(candidate)
                candidate = None
            elif i > 0 and not retried and i - 1 not in picks:
                commit(i - 1)
                retried = True
            else:
                commit(i)
                i += 1
                retried = False
        else:
            if candidate is not None:
                commit(candidate)
                candidate = None
            i += 1
            retried = False
    if candidate is not None:
        commit(candidate)
    if frames - 1 not in picks:
        picks.append(frames - 1)
    picks = sorted(set(picks))

    # ---- 3. read the key frames ------------------------------------------------------------
    cap = cv2.VideoCapture(str(path))
    placed: list[_Item] = []
    titles: dict[str, int] = {}
    want = set(picks)
    read_cover: list[tuple[float, float]] = []
    overlap = view_h * 0.35
    index = 0
    done = 0
    while want:
        ok, frame = cap.read()
        if not ok:
            break
        if index in want:
            want.discard(index)
            if done < 4:
                # The header is translucent, so the thread shows through it and changes from frame
                # to frame; the name itself doesn't. The most common reading is the name.
                read = _read_title(frame, top)
                if read:
                    titles[read] = titles.get(read, 0) + 1
            # Read only what earlier key frames haven't, plus an overlap big enough that a bubble
            # cut by the edge of this strip was read whole in the last one (and to pin the drift).
            off = offsets[index]
            lo, hi = top - off, bottom - off
            gaps = []
            cursor = lo
            for a, b in read_cover:
                if b <= cursor or a >= hi:
                    continue
                if a > cursor:
                    gaps.append((cursor, a))
                cursor = max(cursor, b)
            if cursor < hi:
                gaps.append((cursor, hi))
            if not gaps:
                index += 1
                done += 1
                continue
            s_lo = max(lo, gaps[0][0] - overlap)
            s_hi = min(hi, gaps[-1][1] + overlap)
            s_top, s_bottom = int(s_lo + off), int(s_hi + off)
            items = _read_frame(frame, s_top, s_bottom, dark)
            safe_lo = s_lo + (overlap / 2 if s_lo > lo else CLIP_MARGIN)
            safe_hi = s_hi - (overlap / 2 if s_hi < hi else CLIP_MARGIN)
            read_cover.append((safe_lo, safe_hi))
            read_cover.sort()
            merged = [read_cover[0]]
            for a, b in read_cover[1:]:
                if a <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], b))
                else:
                    merged.append((a, b))
            read_cover[:] = merged
            shift = -offsets[index]
            for it in items:
                it.y0 += shift
                it.y1 += shift
            _place(placed, items)
            done += 1
            progress(0.30 + 0.65 * done / len(picks), f"Reading messages ({done} of {len(picks)} screens)")
            if cancel is not None and cancel.is_set():
                raise RecordingError("Stopped.")
        index += 1
    cap.release()

    # ---- 4. one conversation, in order ---------------------------------------------------
    placed.sort(key=lambda it: (it.y0, it.x0))
    title = max(titles.items(), key=lambda kv: (kv[1], -len(kv[0])))[0] if titles else ""
    messages: list[ReadMessage] = []
    names_seen: dict[str, int] = {}
    current_time: Optional[datetime] = None
    current_header: Optional[str] = None
    them = title or "Them"
    last_name: Optional[str] = None
    warnings: list[str] = []
    unparsed = 0
    for it in placed:
        if it.kind == "time":
            when = parse_header(it.text, recorded_at)
            if when is None:
                unparsed += 1
                continue
            current_time, current_header = when, it.text
            last_name = None                        # a new block names its first sender again
        elif it.kind == "name":
            last_name = it.text.strip()
            names_seen[last_name] = names_seen.get(last_name, 0) + 1
        else:
            me = it.side == "me"
            if me:
                last_name = None                    # their next run starts with a name again
            sender = "Me" if me else (last_name or "")
            messages.append(ReadMessage(it.text, me, sender, current_time, current_header, it.score))
    # A group shows a name above each run of someone's messages; one-to-one threads show none.
    them_runs = sum(1 for a, b in zip([None] + messages, messages) if not b.from_me and (a is None or a.from_me))
    is_group = sum(names_seen.values()) >= max(1, them_runs // 3)
    unknown = 0
    for m in messages:
        if m.from_me:
            continue
        if not is_group:
            m.sender = them
        elif not m.sender:
            m.sender = "Unknown"
            unknown += 1
    if unknown:
        warnings.append(f"The sender of {unknown} message{'s' if unknown != 1 else ''} in this group couldn't be read.")
    if not messages:
        raise RecordingError(
            "No messages were found in this recording. Record a Messages thread, scrolling slowly enough to see each bubble."
        )
    if messages and messages[0].ts is None:
        warnings.append("The first messages were above every time header in the recording, so they have no time.")
    if unparsed:
        warnings.append(f"{unparsed} time header{'s' if unparsed != 1 else ''} couldn't be read.")
    progress(1.0, "Done")
    return RecordingResult(
        title=them, is_group=is_group, messages=messages, recorded_at=recorded_at,
        frames=frames, key_frames=len(picks), duration_s=frames / fps, warnings=warnings,
    )


def _read_title(frame, top: int) -> str:
    """The contact or group name under the avatar in the thread's header."""
    header = frame[: max(1, top)]
    lines = _Ocr.lines(header)
    width = frame.shape[1]
    best = ""
    for x0, y0, x1, y1, text, score in lines:
        cx = (x0 + x1) / 2
        clean = text.strip().rstrip("›>〉 ").strip()
        if abs(cx - width / 2) > width * 0.2 or not clean or re.fullmatch(r"[\d:.\s]+", clean):
            continue
        if y0 > header.shape[0] * 0.45 and len(clean) > len(best):
            best = clean
    return best


def _place(placed: list[_Item], items: list[_Item]) -> None:
    """Add a frame's items to the thread, correcting drift and keeping each bubble once."""
    import statistics

    # Drift: the motion pass is very good frame to frame but can wander over a long
    # recording. Matching text pins this frame to what is already placed.
    deltas = []
    for it in items:
        if it.kind != "msg" or len(it.text) < 6:
            continue
        near = [p for p in placed if p.kind == "msg" and p.side == it.side and abs(p.y0 - it.y0) < 220
                and _similar(p.text, it.text) > 0.85]
        if len(near) == 1:
            deltas.append(near[0].y0 - it.y0)
    if len(deltas) >= 2 and statistics.pstdev(deltas) < 12:
        d = statistics.median(deltas)
        for it in items:
            it.y0 += d
            it.y1 += d
    for it in items:
        match = None
        for p in placed:
            if p.kind != it.kind or abs(p.y0 - it.y0) > MERGE_TOLERANCE:
                continue
            if it.kind == "msg":
                if p.side != it.side:
                    continue
                # A placeholder ("[Emoji]", "[Unreadable message]") is the same bubble as whatever
                # another frame read at that spot; real text must otherwise look alike.
                if not (_placeholder(p.text) or _placeholder(it.text)) and _similar(p.text, it.text) < 0.5:
                    continue
            match = p
            break
        if match is None:
            placed.append(it)
        elif _better(it, match):
            match.text, match.score = it.text, it.score


def _placeholder(text: str) -> bool:
    return text.startswith("[") and text.endswith("]")


def _better(new: _Item, old: _Item) -> bool:
    if _placeholder(old.text) != _placeholder(new.text):
        return _placeholder(old.text)                 # real text beats a placeholder
    return new.score > old.score or (new.score == old.score and len(new.text) > len(old.text))


# ---------------------------------------------------------------------------------------------
# As an Unbubble archive (so the library, search, every export and the upload all just work)

SOURCE = "screen_recording"
SIDECAR = "recording.json"


def _to_ms(when: Optional[datetime]) -> Optional[int]:
    return int(when.timestamp() * 1000) if when else None


def write_archive(result: RecordingResult, video_name: str) -> str:
    """Save what was read as a new one-conversation archive. Returns the archive id."""
    import json

    from .archive import Archive

    directory = Archive.create(
        SOURCE, f"{result.title} (screen recording)",
        {"video": video_name, "recorded_at": result.recorded_at.isoformat(timespec="minutes") if result.recorded_at else None,
         "duration_s": round(result.duration_s, 1), "warnings": result.warnings},
    )
    sidecar = {
        "title": result.title,
        "is_group": result.is_group,
        "recorded_at": result.recorded_at.isoformat(timespec="minutes") if result.recorded_at else None,
        "video": video_name,
        "names": {},
        "messages": [
            {"text": m.text, "from_me": m.from_me, "sender": m.sender, "header": m.header,
             "confidence": round(m.confidence, 3)}
            for m in result.messages
        ],
    }
    (directory / SIDECAR).write_text(json.dumps(sidecar, indent=1, ensure_ascii=False), encoding="utf-8")
    meta = _write_db(directory, sidecar)
    Archive.finalize(directory, **meta)
    return directory.name


def update_archive(directory: Path, *, recorded_at: Optional[str] = None,
                   names: Optional[dict[str, str]] = None, title: Optional[str] = None) -> dict:
    """Correct the recording date (every relative header moves with it) or rename people."""
    import json

    from .archive import Archive

    path = directory / SIDECAR
    if not path.exists():
        raise RecordingError("This conversation wasn't made from a screen recording.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if recorded_at:
        data["recorded_at"] = datetime.fromisoformat(recorded_at).isoformat(timespec="minutes")
    if names:
        clean = {k: v.strip() for k, v in names.items() if isinstance(v, str) and v.strip() and k != "Me"}
        data["names"] = {**data.get("names", {}), **clean}
    if title and title.strip():
        data["title_override"] = title.strip()
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    db = directory / "archive.db"
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            p.unlink()
    meta = _write_db(directory, data)
    label = f"{data.get('title_override') or data['title']} (screen recording)"
    Archive.finalize(directory, label=label, recorded_at=data["recorded_at"], **meta)
    return meta


def _write_db(directory: Path, data: dict) -> dict:
    import json
    import sqlite3

    from .normalize import ARCHIVE_SCHEMA

    recorded_at = datetime.fromisoformat(data["recorded_at"]) if data.get("recorded_at") else datetime.now()
    names: dict[str, str] = data.get("names", {})
    title = data.get("title_override") or names.get(data["title"]) or data["title"]
    con = sqlite3.connect(directory / "archive.db")
    con.executescript(ARCHIVE_SCHEMA)
    people: dict[str, int] = {}
    rows = []
    first = last = None
    preview = ""
    me_count = 0
    for i, m in enumerate(data["messages"], start=1):
        ts = _to_ms(parse_header(m["header"], recorded_at)) if m.get("header") else None
        sender_id = None
        if not m["from_me"]:
            name = names.get(m["sender"], m["sender"]) or "Unknown"
            if not data["is_group"]:
                name = title
            sender_id = people.setdefault(name, len(people) + 1)
        else:
            me_count += 1
        rows.append((i, 1, f"rec:{directory.name}:{i}", ts, int(m["from_me"]), sender_id, m["text"], None,
                     "iMessage", "message", None, None, None, None, None, 0, 0, None, None, 0))
        if ts is not None:
            first = ts if first is None else min(first, ts)
            last = ts if last is None else max(last, ts)
        preview = m["text"]
    con.executemany(f"INSERT INTO messages VALUES ({','.join('?' * 20)})", rows)
    con.executemany(
        "INSERT INTO people (id, key, name, addresses, is_contact) VALUES (?,?,?,?,0)",
        [(pid, f"rec:{name}", name, "[]") for name, pid in people.items()],
    )
    con.execute(
        "INSERT INTO threads (id, key, title, is_group, services, participant_ids, message_count, attachment_count,"
        " first_ts, last_ts, last_preview, from_me_count) VALUES (1,?,?,?,?,?,?,0,?,?,?,?)",
        (f"rec:{directory.name}", title, int(data["is_group"]), json.dumps(["iMessage"]),
         json.dumps(sorted(people.values())), len(rows), first, last, preview, me_count),
    )
    con.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
    meta = {"source": SOURCE, "threads": 1, "messages": len(rows), "attachments": 0, "missing_attachments": 0,
            "reactions": 0, "people": len(people), "first_ts": first, "last_ts": last, "contacts_resolved": 0}
    con.executemany("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", [(k, json.dumps(v)) for k, v in meta.items()])
    con.commit()
    con.close()
    return {"threads": 1, "messages": len(rows), "people": len(people), "first_ts": first, "last_ts": last}


# ---------------------------------------------------------------------------------------------
# Screen recordings on a connected iPhone (they're saved to Photos, which the PC reaches over USB)


async def list_device_videos(udid: str, limit: int = 40) -> list[dict]:
    """The newest videos in the iPhone's camera roll, newest first."""
    from pymobiledevice3 import lockdown
    from pymobiledevice3.services.afc import AfcService

    client = await lockdown.create_using_usbmux(serial=udid, autopair=False)
    found: list[dict] = []
    try:
        async with AfcService(client) as afc:
            folders = [f for f in await afc.listdir("/DCIM") if f[:3].isdigit()]
            for folder in sorted(folders, reverse=True):
                for name in await afc.listdir(f"/DCIM/{folder}"):
                    if Path(name).suffix.lower() not in VIDEO_SUFFIXES:
                        continue
                    st = await afc.stat(f"/DCIM/{folder}/{name}")
                    found.append({
                        "path": f"/DCIM/{folder}/{name}", "name": name, "bytes": st["st_size"],
                        "modified": int(st["st_mtime"].timestamp() * 1000),
                        "likely_screen_recording": name.upper().startswith("RPREPLAY"),
                    })
                if len(found) >= limit * 3:
                    break
    finally:
        await client.close()
    found.sort(key=lambda v: v["modified"], reverse=True)
    return found[:limit]


async def pull_device_video(udid: str, device_path: str, dest: Path, progress: ProgressFn) -> Path:
    """Copy one video off the iPhone over USB."""
    from pymobiledevice3 import lockdown
    from pymobiledevice3.services.afc import AfcService

    if not device_path.startswith("/DCIM/") or ".." in device_path:
        raise RecordingError("That isn't a video in the iPhone's camera roll.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    client = await lockdown.create_using_usbmux(serial=udid, autopair=False)
    try:
        async with AfcService(client) as afc:
            size = (await afc.stat(device_path))["st_size"] or 1
            handle = await afc.fopen(device_path, "r")
            done = 0
            try:
                with open(dest, "wb") as out:
                    while True:
                        chunk = await afc.fread(handle, 4 * 1024 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                        done += len(chunk)
                        progress(min(done / size, 1.0), "Copying the recording from your iPhone")
            finally:
                await afc.fclose(handle)
    finally:
        await client.close()
    return dest
