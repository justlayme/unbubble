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
CALM_PX = 0.5                # small-frame px moved per frame (either side) that still counts as a pause


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
    crop: object = field(default=None, repr=False)   # how it looked (numpy image), for review


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


_ENGINE_BUILD = threading.Lock()


def _ocr_workers() -> tuple[int, int]:
    """How many OCR engines to run side by side, and threads for each. One engine can't keep a
    modern CPU busy on images this small, so a few run at once, each on its share of the cores."""
    import os

    cores = os.cpu_count() or 2
    workers = int(os.environ.get("GM_OCR_WORKERS", "0")) or max(1, min(4, cores // 3))
    return workers, max(1, cores // workers)


def _new_engine(threads: int = 0):
    import rapidocr_onnxruntime.utils as rapid_utils
    from onnxruntime import SessionOptions
    from rapidocr_onnxruntime import RapidOCR

    def options():
        o = SessionOptions()
        if threads:
            o.intra_op_num_threads = threads
            o.inter_op_num_threads = 1
        return o

    with _ENGINE_BUILD:                            # the engine builds its own session options
        original = rapid_utils.SessionOptions
        rapid_utils.SessionOptions = options
        try:
            engine = RapidOCR()
        finally:
            rapid_utils.SessionOptions = original
    # Messages text on an iPhone recording is already large, so detection never needs the default
    # upscale to a 736 px short side; cap the long side instead (much faster, same accuracy).
    for op in getattr(getattr(engine, "text_detector", None), "preprocess_op", []):
        if hasattr(op, "limit_type") and hasattr(op, "limit_side_len"):
            op.limit_type, op.limit_side_len = "max", 1280
    return engine


class _Ocr:
    """On-device OCR (RapidOCR, ONNX), a small pool of engines so strips are read in parallel.
    Models ship with the app; engines are built on first use (or warmed up in the background)."""

    _free: list = []
    _built = 0
    _cond = threading.Condition()

    @classmethod
    def _acquire(cls):
        workers, threads = _ocr_workers()
        with cls._cond:
            while not cls._free and cls._built >= workers:
                cls._cond.wait()
            if cls._free:
                return cls._free.pop()
            cls._built += 1
        try:
            return _new_engine(threads)
        except Exception:
            with cls._cond:
                cls._built -= 1
                cls._cond.notify()
            raise

    @classmethod
    def _release(cls, engine) -> None:
        with cls._cond:
            cls._free.append(engine)
            cls._cond.notify()

    @classmethod
    def warm_up(cls) -> None:
        """Build the engines in the background while the motion pass runs (seconds saved)."""
        workers, _ = _ocr_workers()

        def load() -> None:
            try:
                cls._release(cls._acquire())
            except Exception:
                pass
        for _ in range(max(0, workers - cls._built)):
            threading.Thread(target=load, name="ocr-warm-up", daemon=True).start()

    @classmethod
    def lines(cls, image) -> list[tuple[float, float, float, float, str, float]]:
        engine = cls._acquire()
        try:
            result, _ = engine(image, use_cls=False)
        finally:
            cls._release(engine)
        out = []
        for box, text, score in result or []:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            out.append((min(xs), min(ys), max(xs), max(ys), str(text).strip(), float(score)))
        return out

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
        engine = cls._acquire()
        try:
            result = engine.text_recognizer([image])[0]
        finally:
            cls._release(engine)
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
    crop: object = None       # the message as it appeared (numpy image), for review


@dataclass
class _Block:
    x0: int
    y0: int
    x1: int
    y1: int
    side: str                 # "me" | "them", from which edge it hugs
    kind: str                 # "bubble" | "photo" | "emoji" | "card"
    text_top: int = 0         # cards: where the grey caption starts
    colour: str = ""          # "mine" (blue/green) | "grey"


def _colour_masks(hsv, dark: bool):
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    blue = (h >= 98) & (h <= 125) & (s >= 110) & (v >= 140)
    green = (h >= 50) & (h <= 85) & (s >= 90) & (v >= 110)
    grey = ((s <= 40) & (v >= 28) & (v <= 62)) if dark else ((s <= 30) & (v >= 205) & (v <= 243))
    return blue | green, grey


def _side(x0: float, x1: float, width: int) -> Optional[str]:
    """Messages puts yours against the right edge and theirs against the left (after the avatar
    in a group). Anything hugging neither is a tapback badge, an avatar or stray UI."""
    left, right = x0 / width, (width - x1) / width
    if right <= 0.075 and left > right:
        return "me"
    if left <= 0.16 and right > left:
        return "them"
    return None


def _find_blocks(view, dark: bool) -> list[_Block]:
    """Everything in the thread that is a message: text bubbles (any colour: blue, green, grey;
    a link preview is grey whoever sent it), photos, big emoji and link-preview cards."""
    cv2 = _cv2()
    import numpy as np

    height, width = view.shape[:2]
    hsv = cv2.cvtColor(view, cv2.COLOR_BGR2HSV)
    mine, grey = _colour_masks(hsv, dark)
    kernel3 = np.ones((3, 3), np.uint8)
    blocks: list[_Block] = []
    bubbles_mask = np.zeros((height, width), np.uint8)
    for colour, mask in (("mine", mine), ("grey", grey)):
        m = cv2.morphologyEx(mask.astype(np.uint8) * 255, cv2.MORPH_OPEN, kernel3)
        contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            x, y, w, hh = cv2.boundingRect(c)
            if w < width * 0.06 or hh < 26 or w > width * 0.94:
                continue
            if cv2.contourArea(c) < 0.55 * w * hh:     # not a filled rounded rectangle
                continue
            side = _side(x, x + w, width)
            if side is None or (colour == "mine" and side != "me") or hh < width * 0.06:
                continue                               # yours are blue/green on the right; a bubble is a line tall
            region = m[y:y + hh, x:x + w] > 0
            vals = hsv[y:y + hh, x:x + w][region]
            # A bubble is one flat colour: blue/green keep one hue (text edges only change its
            # brightness); grey keeps one brightness. A photo's sky or field doesn't.
            if len(vals) and ((colour == "mine" and vals[:, 0].std() > 4) or (colour == "grey" and vals[:, 2].std() > 6)):
                continue
            # A tapback badge sitting on a bubble's top corner widens its outline upward; the
            # bubble itself starts at the first row that spans most of its width.
            rows = (m[y:y + hh, x:x + w] > 0).sum(axis=1)
            full = np.nonzero(rows >= rows.max() * 0.5)[0]
            ty0, ty1 = y + int(full[0]), y + int(full[-1]) + 1
            blocks.append(_Block(x, ty0, x + w, ty1, side, "bubble", colour=colour))
            cv2.rectangle(bubbles_mask, (x, y), (x + w, y + hh), 255, -1)

    # Photos and big emoji: large areas that aren't the thread's background or a bubble. Text
    # strokes, avatars and typing dots are too thin or small to survive the opening.
    gray = cv2.cvtColor(view, cv2.COLOR_BGR2GRAY).astype(np.int16)
    bg = 0 if dark else 255
    content = ((np.abs(gray - bg) > (16 if dark else 30)) | (hsv[..., 1] > 70)).astype(np.uint8) * 255
    content[cv2.dilate(bubbles_mask, np.ones((9, 9), np.uint8)) > 0] = 0
    content = cv2.morphologyEx(content, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21)))
    content = cv2.morphologyEx(content, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    content[bubbles_mask > 0] = 0
    contours, _ = cv2.findContours(content, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    media: list[_Block] = []
    for c in contours:
        x, y, w, hh = cv2.boundingRect(c)
        if w < width * 0.09 or hh < width * 0.07:
            continue
        side = _side(x, x + w, width)
        if side is None:
            continue
        kind = "photo" if (w >= width * 0.3 and hh >= width * 0.2) else "emoji"
        media.append(_Block(x, y, x + w, y + hh, side, kind))

    # A link preview is an image with a grey caption bubble directly under it, same width.
    for mb in media:
        if mb.kind != "photo":
            blocks.append(mb)
            continue
        caption = next((b for b in blocks if b.kind == "bubble" and -6 <= b.y0 - mb.y1 <= 30
                        and abs(b.x0 - mb.x0) < 22 and abs(b.x1 - mb.x1) < 22), None)
        if caption is not None:
            blocks.remove(caption)
            blocks.append(_Block(mb.x0, mb.y0, max(mb.x1, caption.x1), caption.y1, mb.side, "card", caption.y0, "grey"))
        else:
            blocks.append(mb)
    return blocks


def _is_typing_indicator(crop) -> bool:
    """The grey "…" bubble while someone types: small, no text, three round dots in a row."""
    cv2 = _cv2()
    import numpy as np

    h, w = crop.shape[:2]
    if w > h * 2.6 or w < h * 1.1:
        return False
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.int16)
    bg = int(np.median(gray))
    ink = (np.abs(gray - bg) > 18).astype(np.uint8)
    n, _, stats, cents = cv2.connectedComponentsWithStats(ink)
    def inner(i):
        x, y, ww, hh = stats[i, 0], stats[i, 1], stats[i, 2], stats[i, 3]
        return x > 0 and y > 0 and x + ww < w and y + hh < h
    dots = [i for i in range(1, n) if inner(i) and 0.02 * h * h < stats[i, cv2.CC_STAT_AREA] < 0.14 * h * h
            and abs(int(stats[i, cv2.CC_STAT_WIDTH]) - int(stats[i, cv2.CC_STAT_HEIGHT])) <= max(3, h * 0.06)]
    if len(dots) != 3:
        return False
    ys = [cents[i][1] for i in dots]
    return max(ys) - min(ys) < h * 0.15


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
    ios_blue = (h >= 98) & (h <= 125)              # a bubble or my tapback badge, never an emoji
    colourful = (s >= 120) & (v >= 120) & ~ios_blue & ~(s < 45)
    return colourful.mean() > 0.02


def _read_bubble_again(crop) -> tuple[str, float]:
    """Second look at a bubble OCR found no text in: a one-line bubble ("k", "ok") is read
    directly with the bubble's padding trimmed; anything taller is zoomed and detected again."""
    cv2 = _cv2()
    h, w = crop.shape[:2]
    dy, dx = int(h * 0.15), int(min(w * 0.2, h * 0.33))
    # A lone letter reads inconsistently depending on framing, so read a few framings and take
    # what they agree on.
    core = crop[int(h * 0.18):h - int(h * 0.18), int(w * 0.22):w - int(w * 0.22)]
    framings = [crop[dy:h - dy, dx:w - dx], crop[int(h * 0.22):h - int(h * 0.22), int(w * 0.25):w - int(w * 0.25)], crop,
                cv2.cvtColor(255 - cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR), core]
    if core.size:
        framings.append(cv2.resize(core, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))
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
        if len(scores) >= 2:                       # several framings agree: that's what it says
            return text, max(0.8, max(scores))
        if max(scores) >= 0.3:
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
    """Messages, headers and sender names in one strip of a frame, in screen coordinates."""
    view = frame[top:bottom]
    height, width = view.shape[:2]
    blocks = _find_blocks(view, dark)
    lines = _Ocr.lines_scaled(view, OCR_SCALE)
    mine, grey = _colour_masks(_cv2().cvtColor(view, _cv2().COLOR_BGR2HSV), dark)
    mask = mine | grey
    items: list[_Item] = []
    claimed = [False] * len(lines)
    tops_visible = [b for b in blocks if b.y0 > CLIP_MARGIN]
    for b in sorted(blocks, key=lambda b: b.y0):
        inside = []
        for i, (x0, y0, x1, y1, text, score) in enumerate(lines):
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if b.x0 - 4 <= cx <= b.x1 + 4 and b.y0 - 4 <= cy <= b.y1 + 4:
                claimed[i] = True
                if b.kind == "bubble" or (b.kind == "card" and cy >= b.text_top):
                    # Text printed on this bubble, not on a tapback badge overlapping its corner.
                    own = mine if b.colour == "mine" else grey
                    if _on_bubble(own, x0, y0, x1, y1):
                        inside.append((y0, x0, text, score, x1, y1))
        if b.y0 <= CLIP_MARGIN or b.y1 >= height - CLIP_MARGIN:
            # Cut off: another frame should show it whole. Note it, so a message too tall to fit
            # any strip whole still gets a second look from the frame with the most room for it.
            edge = "both" if (b.y0 <= CLIP_MARGIN and b.y1 >= height - CLIP_MARGIN) else ("top" if b.y0 <= CLIP_MARGIN else "bottom")
            items.append(_Item("clip", b.y0, b.y1, edge, 0.0, b.side, b.x0))
            continue
        crop = view[b.y0:b.y1, b.x0:b.x1]
        # Half a link preview? An image near the strip's bottom may have its caption just out of
        # view; a wide grey bubble at the top may have its image just out of view.
        if b.kind == "photo" and height - b.y1 < width * 0.18:
            items.append(_Item("clip", b.y0, b.y1, "bottom", 0.0, b.side, b.x0))
        elif b.kind == "bubble" and b.colour == "grey" and b.y0 < width * 0.06 and (b.x1 - b.x0) > width * 0.55:
            items.append(_Item("clip", b.y0, b.y1, "top", 0.0, b.side, b.x0))
        if b.kind in ("photo", "emoji"):
            text, score = ("[Photo]", 0.7) if b.kind == "photo" else ("[Emoji]", 0.6)
        else:
            inside = [(y0, x0, _fix_spaces(view, (x0, y0, x1, y1), t), s, x1, y1) for (y0, x0, t, s, x1, y1) in inside]
            inside.sort()
            text = " ".join(l[2] for l in inside).strip()
            score = min((l[3] for l in inside), default=0.0)
            if text and b.kind == "bubble":
                last = inside[-1]
                if _trailing_emoji(view, (b.x0, b.y0, b.x1, b.y1), (last[1], last[0], last[4], last[5]), b.side):
                    text += " [Emoji]"
            if not text and b.kind == "card":
                text, score = "[Link]", 0.6
            if not text:
                if _is_typing_indicator(crop):
                    continue                           # someone typing, not a message
                if _looks_like_emoji(crop, b.side):
                    text, score = "[Emoji]", 0.5
                else:
                    text, score = _read_bubble_again(crop)
                    if not text or re.fullmatch(r"[.·•…oO0\s]+", text):
                        if _is_typing_indicator(crop):
                            continue
                        text, score = "[Unreadable message]", 0.1
        items.append(_Item("msg", b.y0, b.y1, text, score, b.side, b.x0, crop=_thumb(crop)))

    bubble_line_h = [y1 - y0 for (x0, y0, x1, y1, t, s), c in zip(lines, claimed) if c]
    typical = sorted(bubble_line_h)[len(bubble_line_h) // 2] if bubble_line_h else 40
    for i, (x0, y0, x1, y1, text, score) in enumerate(lines):
        if claimed[i] or not text or y0 <= CLIP_MARGIN or y1 >= height - CLIP_MARGIN:
            continue
        if _STATUS.match(text) or _on_bubble(mask, x0, y0, x1, y1):
            continue                                   # receipts, badges, or text of a bubble we couldn't outline
        cx = (x0 + x1) / 2
        if abs(cx - width / 2) < width * 0.14 and x0 > width * 0.12 and x1 < width * 0.88:
            items.append(_Item("time", y0, y1, text, score))
            continue
        if x0 > width * 0.3 or x1 > width * 0.75 or (y1 - y0) > typical * 0.9:
            continue
        # A group sender's name sits directly above the first of their messages, at its indent.
        below = [b for b in tops_visible if b.side == "them" and 0 <= b.y0 - y1 <= typical * 1.2]
        if any(abs(b.x0 - x0) < width * 0.08 for b in below):
            items.append(_Item("name", y0, y1, text, score, x0=x0))
    for it in items:
        it.y0 += top
        it.y1 += top
    return items


def _thumb(crop, max_w: int = 520):
    """A small copy of a message as it appeared, for the review screen."""
    cv2 = _cv2()
    h, w = crop.shape[:2]
    if w > max_w:
        crop = cv2.resize(crop, (max_w, max(1, round(h * max_w / w))), interpolation=cv2.INTER_AREA)
    return crop.copy()


# ---------------------------------------------------------------------------------------------
# The reader


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _bars(motion, height: int) -> tuple[int, int]:
    """Top and bottom of the scrolling thread, from how much each row moved. Rows that never
    move are the status bar and header above, and the text field (and keyboard, if it was open)
    below."""
    import numpy as np

    rows = np.asarray(motion, dtype=float)
    n = len(rows)
    centre = rows[int(n * 0.3):int(n * 0.6)]
    if centre.size == 0 or centre.mean() <= 0:
        return int(height * 0.165), int(height * 0.90)
    moving = rows > centre.mean() * 0.3
    seed = int(n * 0.2) + int(np.argmax(rows[int(n * 0.2):int(n * 0.5)]))
    top = seed
    while top > 0 and moving[top - 1]:
        top -= 1
    bottom = seed
    while bottom < n and moving[bottom]:
        bottom += 1
    scale = height / n
    t, b = int(top * scale), int(bottom * scale)
    # Translucent bars let blurred content through; keep inside sane iOS bounds.
    t = min(max(t, int(height * 0.08)), int(height * 0.26))
    b = max(min(b, int(height * 0.95)), int(height * 0.42))
    return t, b


WORK_WIDTH = 886      # every recording is read at this width (iPhone screen recordings are 886-1290 px wide)
EventFn = Callable[[dict], None]


def _work_size(width: int, height: int) -> tuple[int, int, float]:
    if 840 <= width <= 960:
        return width, height, 1.0
    s = WORK_WIDTH / width
    return WORK_WIDTH, round(height * s), s


def _frame_shift(prev, g, pred: float = 0.0) -> tuple[Optional[float], bool]:
    """How far the thread moved between two small grey frames (+ = content moved down, i.e.
    scrolling back in time), and whether the measurement is sure.

    A flick can move content most of a screen in one frame, far beyond what phase correlation
    can see. A strip of each frame is found in the other over almost the whole screen; looking
    both ways means one direction always has its source in view, even with the keyboard open
    hiding half the thread. The strip is the most textured part of the frame (a smooth photo or
    an empty stretch gives nothing to lock onto), and a flat match peak is reported as unsure so
    the reader leans on the text instead. A weak preference for the last frame's speed breaks
    ties between look-alike bubbles."""
    cv2 = _cv2()
    import numpy as np

    n = g.shape[0]
    s0, s1 = int(n * 0.08), int(n * 0.92)
    tall = int(n * 0.14)
    best: Optional[tuple[float, float, bool]] = None
    for src, other, sign in ((g, prev, -1), (prev, g, 1)):
        # the most textured band between 22% and 64% of the screen
        energy = np.abs(np.diff(src, axis=0)).sum(axis=1)
        lo, hi = int(n * 0.22), int(n * 0.64) - tall
        if hi <= lo:
            lo, hi = int(n * 0.36), int(n * 0.36) + 1
        csum = np.concatenate([[0.0], np.cumsum(energy)])
        sums = csum[lo + tall:hi + tall] - csum[lo:hi]
        t0 = lo + int(np.argmax(sums))
        templ = src[t0:t0 + tall]
        if templ.std() < 3:
            continue
        r = cv2.matchTemplate(other[s0:s1], templ, cv2.TM_CCOEFF_NORMED)[:, 0]
        ks = np.arange(len(r))
        k = int(np.argmax(r - 0.0003 * np.abs(sign * ((s0 + ks) - t0) - pred)))
        frac = 0.0
        if 0 < k < len(r) - 1:
            a, b, c = r[k - 1], r[k], r[k + 1]
            den = a - 2 * b + c
            if den < 0:
                frac = 0.5 * (a - c) / den
        # sure = a sharp peak: well above the best match more than a few px away
        away = np.concatenate([r[:max(0, k - 6)], r[k + 7:]])
        sharp = (r[k] - (away.max() if away.size else -1.0)) > 0.04
        cand = (float(r[k]), float(sign * ((s0 + k + frac) - t0)), bool(sharp))
        if best is None or (cand[2], cand[0]) > (best[2], best[0]):
            best = cand
    if best is None or best[0] < 0.5:
        return None, False
    return best[1], best[2]


def _foreign_frames(header, footer):
    """Frames that aren't the Messages thread: Control Center at the start and end of every
    screen recording, the app switcher, the lock screen, and notification banners over the header.

    Messages' header and text field barely change while the thread scrolls under them, so a frame
    whose header or footer is far from the recording's usual one is covered by something else.
    Returns (foreign, banner): foreign frames are skipped entirely; banner frames are fine below
    the header (the banner sits over it) but aren't used to read the conversation's name."""
    import numpy as np

    n = len(header)
    H = np.asarray(header, dtype=np.float32)
    F = np.asarray(footer, dtype=np.float32)
    step = max(1, n // 400)

    def bad(sig):
        med = np.median(sig[::step], axis=0)
        d = np.abs(sig - med).mean(axis=1)
        return d > max(14.0, float(np.median(d)) * 4)

    hb, fb = bad(H), bad(F)
    any_bad = hb | fb
    if any_bad.mean() > 0.8:                       # nothing stable to compare with: trust every frame
        return np.zeros(n, bool), np.zeros(n, bool)
    foreign = hb & fb
    i = 0
    while i < n and any_bad[i]:                    # Control Center being swiped away at the start
        foreign[i] = True
        i += 1
    i = n - 1
    while i >= 0 and any_bad[i]:                   # ...and pulled down to stop at the end
        foreign[i] = True
        i -= 1
    return foreign, hb & ~foreign


def read_recording(
    path: Path,
    *,
    progress: ProgressFn = lambda f, s: None,
    cancel: Optional[threading.Event] = None,
    recorded_at: Optional[datetime] = None,
    on_event: Optional[EventFn] = None,
) -> RecordingResult:
    cv2 = _cv2()
    import numpy as np
    import time as _time

    emit = on_event or (lambda e: None)
    path = Path(path)
    if path.suffix.lower() not in VIDEO_SUFFIXES:
        raise RecordingError("Choose the screen recording itself, an .mp4 or .mov video.")
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RecordingError("This video couldn't be opened. Screen recordings from an iPhone are .mp4 or .mov files.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if src_w > src_h:
        raise RecordingError("This video is landscape. Record the thread with the iPhone held upright.")
    width, height, work_scale = _work_size(src_w, src_h)
    recorded_at = recorded_at or recording_created_at(path) or datetime.fromtimestamp(path.stat().st_mtime)

    def to_work(frame):
        if work_scale == 1.0:
            return frame
        return cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA if work_scale < 1 else cv2.INTER_CUBIC)

    _Ocr.warm_up()

    # ---- 1. motion pass ------------------------------------------------------------------
    small_h = max(1, round(src_h * SMALL_WIDTH / src_w))
    scale = width / SMALL_WIDTH                      # small px -> working px
    band = slice(int(small_h * 0.30), int(small_h * 0.62))
    window = cv2.createHanningWindow((SMALL_WIDTH, band.stop - band.start), cv2.CV_32F)
    hdr = slice(int(small_h * 0.065), int(small_h * 0.15))
    ftr = slice(int(small_h * 0.955), small_h)
    row_diff: list = []          # per frame: how much each row changed since the last
    dys: list[float] = []
    lost: list[bool] = []        # frames where the move couldn't be measured
    headers: list = []
    footers: list = []
    bright: list[tuple[int, float]] = []
    prev = None
    pred = 0.0
    index = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(cv2.resize(frame, (SMALL_WIDTH, small_h), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        g = g.astype(np.float32)
        headers.append(g[hdr, ::4].astype(np.uint8).ravel())
        footers.append(g[ftr, ::4].astype(np.uint8).ravel())
        if prev is not None:
            diff = np.abs(g - prev)
            row_diff.append(diff.mean(axis=1).astype(np.float16))
            dy, sure = _frame_shift(prev, g, pred)
            lost.append(not sure and float(diff.mean()) > 1.0)
            if dy is None:                         # nothing to lock onto: fall back, or assume still
                (dx, dy), response = cv2.phaseCorrelate(prev[band], g[band], window)
                if response < 0.3 or abs(dy) > (band.stop - band.start) * 0.45:
                    dy = 0.0
            pred = dy
            dys.append(dy * scale)
        else:
            row_diff.append(np.zeros(small_h, np.float16))
            dys.append(0.0)
            lost.append(False)
        if index % 10 == 0:
            bright.append((index, float(np.median(g[band, : max(2, SMALL_WIDTH // 25)]))))
        prev = g
        index += 1
        if total and index % 30 == 0:
            progress(0.02 + 0.2 * index / total, "Following the scroll")
        if cancel is not None and cancel.is_set():
            raise RecordingError("Stopped.")
    cap.release()
    frames = len(dys)
    if frames < 2:
        raise RecordingError("This video has no frames to read.")

    foreign, banner = _foreign_frames(headers, footers)
    if foreign.all():
        raise RecordingError("This video doesn't look like a Messages conversation. Record the thread itself while you scroll it.")
    offsets: list[float] = []
    stillness: list[float] = []
    offset = 0.0
    motion = np.zeros(small_h)
    for i in range(frames):
        jump = foreign[i] or (i > 0 and foreign[i - 1])
        dy = 0.0 if jump else dys[i]
        offset += dy
        offsets.append(offset)
        stillness.append(abs(dy) / scale)
        if not jump and not banner[i]:
            motion += row_diff[i].astype(np.float64)
    del row_diff
    top, bottom = _bars(motion, height)
    samples = [b for i, b in bright if not foreign[i]] or [b for _, b in bright]
    dark = sorted(samples)[len(samples) // 2] < 110
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

    def commit(c: int) -> None:
        picks.append(c)
        cover(*window(c))

    # People flick, then pause to look. Frames in a pause are sharp; frames mid-flick are smeared
    # (motion blur, and video compression makes it worse), which OCR reads as nonsense. So read
    # from the pauses: the middle of each run of still frames, wherever it shows new thread.
    calm = [max(stillness[max(0, i - 1)], stillness[i], stillness[min(frames - 1, i + 1)]) for i in range(frames)]
    runs: list[list[int]] = []
    for i in range(frames):
        if foreign[i] or calm[i] > CALM_PX:
            continue
        if runs and runs[-1][-1] == i - 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    for run in runs:
        best = run[len(run) // 2]
        lo, hi = window(best)
        if uncovered(lo, hi) > view_h * KEY_ADVANCE or not picks:
            commit(best)

    # Anything only ever seen while moving: take the steadiest frame that shows it.
    usable = [k for k in range(frames) if not foreign[k]]
    span_lo = min(window(k)[0] for k in usable)
    span_hi = max(window(k)[1] for k in usable)
    for _ in range(400):
        gaps, cursor = [], span_lo
        for a, b in covered:
            if a > cursor + 8:
                gaps.append((cursor, a))
            cursor = max(cursor, b)
        if span_hi > cursor + 8:
            gaps.append((cursor, span_hi))
        if not gaps:
            break
        a, b = gaps[0]
        need = min(b - a, view_h - 2 * margin)
        best, best_key = None, None
        for k in usable:
            lo, hi = window(k)
            seen = min(hi, b) - max(lo, a)
            if seen <= 0:
                continue
            key = (min(seen, need) >= need * 0.98, seen, -calm[k])
            if best_key is None or key > best_key:
                best, best_key = k, key
        if best is None or best in picks:
            cover(a, b)                            # nothing more to see there
            continue
        commit(best)
    picks = sorted(set(picks))

    # ---- 3. read the key frames ------------------------------------------------------------
    # Each key frame contributes the strip of thread no earlier one showed, plus an overlap big
    # enough that a message cut by the strip's edge was whole in the neighbour, and to pin the
    # strip in place by the text the two share. Strips are planned up front from the scroll
    # estimate, read in parallel, then stitched in order.
    overlap = view_h * 0.3
    plan: list[tuple[int, int, int, float, float]] = []      # frame, strip top, strip bottom, safe lo, safe hi
    read_cover: list[tuple[float, float]] = []
    for index in picks:
        off = offsets[index]
        lo, hi = top - off, bottom - off
        gaps, cursor = [], lo
        for a, b in read_cover:
            if b <= cursor or a >= hi:
                continue
            if a > cursor:
                gaps.append((cursor, a))
            cursor = max(cursor, b)
        if cursor < hi:
            gaps.append((cursor, hi))
        if not gaps:
            continue
        s_lo = max(lo, gaps[0][0] - overlap)
        s_hi = min(hi, gaps[-1][1] + overlap)
        safe_lo = s_lo + (overlap / 2 if s_lo > lo else CLIP_MARGIN)
        safe_hi = s_hi - (overlap / 2 if s_hi < hi else CLIP_MARGIN)
        plan.append((index, int(s_lo + off), int(s_hi + off), safe_lo, safe_hi))
        read_cover.append((safe_lo, safe_hi))
        read_cover.sort()
        merged = [read_cover[0]]
        for a, b in read_cover[1:]:
            if a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        read_cover[:] = merged

    from concurrent.futures import ThreadPoolExecutor

    workers, _ = _ocr_workers()
    placed: list[_Item] = []
    titles: dict[str, int] = {}
    clips: list[_Item] = []
    title_jobs = 0

    def job(frame, s_top: int, s_bottom: int, want_title: bool):
        title = _read_title(frame, top) if want_title else ""
        return title, _read_frame(frame, s_top, s_bottom, dark)

    started = _time.monotonic()
    done = 0
    last_read = 0

    def consume(index: int, result) -> None:
        nonlocal done, last_read
        title, items = result
        if title:
            titles[title] = titles.get(title, 0) + 1
        off = offsets[index]                       # includes corrections from strips before it
        for it in items:
            it.y0 -= off
            it.y1 -= off
        # The scroll estimate drifts and can jump; the text this strip shares with what's
        # already read says exactly where it belongs, within how far off the estimate could be.
        unsure = sum(1 for k in range(last_read + 1, index + 1) if lost[k] or foreign[k])
        last_read = index
        shift = _alignment(placed, items, limit=150 + min(6000, unsure * view_h * 0.5))
        if shift:
            for it in items:
                it.y0 += shift
                it.y1 += shift
            for k in range(index, frames):
                offsets[k] -= shift
        clips.extend(it for it in items if it.kind == "clip")
        fresh = _place(placed, [it for it in items if it.kind != "clip"])
        if fresh:
            emit({"type": "messages", "messages": [
                {"text": it.text, "from_me": it.side == "me"} for it in sorted(fresh, key=lambda t: t.y0) if it.kind == "msg"
            ], "found": sum(1 for p in placed if p.kind == "msg")})
        done += 1
        elapsed = _time.monotonic() - started
        progress(0.22 + 0.75 * done / max(1, len(plan)), f"Reading messages ({done} of {len(plan)} screens)")
        emit({"type": "eta", "seconds": round((len(plan) - done) * elapsed / done)})

    cap = cv2.VideoCapture(str(path))
    pending: list = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ocr") as pool:
        try:
            want = {f: (t, b) for f, t, b, _, _ in plan}
            index = 0
            while want and cap.grab():             # decode every frame, convert only the ones read
                if index in want:
                    ok, frame = cap.retrieve()
                    if ok:
                        s_top, s_bottom = want.pop(index)
                        wt = title_jobs < 4 and not banner[index]
                        title_jobs += wt
                        pending.append((index, pool.submit(job, to_work(frame), s_top, s_bottom, wt)))
                    else:
                        want.pop(index)
                    while len(pending) > workers * 2 or (pending and pending[0][1].done()):
                        i, fut = pending.pop(0)
                        consume(i, fut.result())
                    if cancel is not None and cancel.is_set():
                        raise RecordingError("Stopped.")
                index += 1
            for i, fut in pending:
                consume(i, fut.result())
                if cancel is not None and cancel.is_set():
                    raise RecordingError("Stopped.")
            pending = []
        except BaseException:
            for _, fut in pending:
                fut.cancel()
            raise
        finally:
            cap.release()

    # ---- 3b. second look at anything only ever seen cut off ---------------------------------
    usable = [k for k in range(frames) if not foreign[k]]
    wanted: dict[int, list[_Item]] = {}
    for c in clips:
        visible = c.y1 - c.y0
        def covers(p: _Item) -> bool:
            if p.kind != "msg" or p.side != c.side or min(p.y1, c.y1) - max(p.y0, c.y0) < visible * 0.6:
                return False
            # a photo or caption alone doesn't settle whether it was half of a link preview
            return not (c.text in ("top", "bottom") and (p.y1 - p.y0) <= visible + 24 and
                        (_placeholder(p.text) if c.text == "bottom" else True) and _possible_half(p, placed))
        if any(covers(p) for p in placed):
            continue
        best, best_key = None, None
        safe = view_h * 0.06                       # the scroll estimate can be a little off
        for k in usable:
            lo, hi = top - offsets[k] + safe, bottom - offsets[k] - safe
            if c.text == "bottom":                 # it goes on below what was seen: room under its top
                if c.y0 < lo:
                    continue
                room = hi - c.y0
            elif c.text == "top":                  # it starts above what was seen: room over its bottom
                if c.y1 > hi:
                    continue
                room = c.y1 - lo
            else:
                room = min(c.y0 - lo, hi - c.y1)
            key = (round(min(room, view_h * 0.8) / 20), -calm[k], -abs(room - view_h * 0.6))
            if best_key is None or key > best_key:
                best, best_key = k, key
        if best is not None:                       # even a key frame: only a strip of it was read
            wanted.setdefault(best, []).append(c)
    if wanted:
        cap = cv2.VideoCapture(str(path))
        index = 0
        remaining = set(wanted)
        while remaining and cap.grab():
            if index in remaining:
                remaining.discard(index)
                ok, frame = cap.retrieve()
                if ok:
                    frame = to_work(frame)
                    off = offsets[index]
                    items = _read_frame(frame, top, bottom, dark)
                    for it in items:
                        it.y0 -= off
                        it.y1 -= off
                    items = [it for it in items if it.kind != "clip"]
                    shift = _alignment(placed, items, limit=150 + min(6000, view_h * 0.5 * sum(lost)))
                    for it in items:
                        it.y0 += shift
                        it.y1 += shift
                    fresh = _place(placed, items)
                    if fresh:
                        emit({"type": "messages", "messages": [
                            {"text": it.text, "from_me": it.side == "me"} for it in sorted(fresh, key=lambda t: t.y0) if it.kind == "msg"
                        ], "found": sum(1 for p in placed if p.kind == "msg")})
            index += 1
        cap.release()

    # ---- 4. one conversation, in order ---------------------------------------------------
    placed = _resolve_overlaps(placed)
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
            messages.append(ReadMessage(it.text, me, sender, current_time, current_header, it.score, it.crop))
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


def _alignment(placed: list[_Item], items: list[_Item], limit: float = 150.0) -> float:
    """How far to move a newly read strip so its messages and headers land on the same ones
    already placed. Each reading votes for the shifts that would line it up with a matching one;
    a reading that matches several places (a repeated "ok") splits its vote among them. The
    shift with the most weight wins, within how far off the scroll estimate could be."""
    votes: list[tuple[float, float]] = []
    for it in items:
        if it.kind in ("name", "clip") or _placeholder(it.text) or len(it.text) < 4:
            continue
        mine = [p.y0 - it.y0 for p in placed
                if p.kind == it.kind and (it.kind != "msg" or p.side == it.side)
                and abs(p.y0 - it.y0) <= max(limit, 4000) and _similar(p.text, it.text) >= 0.9]
        for v in mine:
            votes.append((v, 1.0 / len(mine)))
    if not votes:
        return 0.0
    best_w, best = 0.0, []
    for v, _ in votes:
        group = [(u, w) for u, w in votes if abs(u - v) <= 10]
        weight = sum(w for _, w in group)
        if weight > best_w + 1e-9 or (abs(weight - best_w) <= 1e-9 and abs(v) < abs(best[0][0] if best else 1e9)):
            best_w, best = weight, group
    shift = sorted(u for u, _ in best)[len(best) // 2]
    if abs(shift) > limit:
        # Beyond what the scroll estimate could plausibly be off by: only with strong, unique
        # agreement (at least two distinct messages), well ahead of any other shift.
        rival = max((sum(w for u, w in votes if abs(u - v) <= 10) for v, _ in votes if abs(v - shift) > 30), default=0.0)
        if best_w >= 2.0 and best_w >= 2 * rival:
            return shift
        inside = [(v, w) for v, w in votes if abs(v) <= limit]
        if not inside:
            return 0.0
        return _alignment_pick(inside)
    if best_w >= 1.5 or (best_w >= 0.99 and abs(shift) < 150):
        return shift if abs(shift) > 2 else 0.0
    return 0.0


def _alignment_pick(votes: list[tuple[float, float]]) -> float:
    best_w, best = 0.0, []
    for v, _ in votes:
        group = [(u, w) for u, w in votes if abs(u - v) <= 10]
        weight = sum(w for _, w in group)
        if weight > best_w + 1e-9:
            best_w, best = weight, group
    shift = sorted(u for u, _ in best)[len(best) // 2]
    if best_w >= 1.5 or (best_w >= 0.99 and abs(shift) < 150):
        return shift if abs(shift) > 2 else 0.0
    return 0.0


def _place(placed: list[_Item], items: list[_Item]) -> list[_Item]:
    """Add a strip's items to the thread, keeping each message once. Returns the new ones."""
    fresh: list[_Item] = []
    for it in items:
        match = None
        for p in placed:
            if p.kind != it.kind:
                continue
            nested = it.kind == "msg" and _nested(p, it)
            if abs(p.y0 - it.y0) > MERGE_TOLERANCE and not nested:
                continue
            if it.kind == "msg":
                if p.side != it.side:
                    continue
                if nested and abs(p.y0 - it.y0) > MERGE_TOLERANCE:
                    a, b = p.text.lower(), it.text.lower()
                    if _placeholder(p.text) or _placeholder(it.text) or _similar(a, b) >= 0.6 or a in b or b in a:
                        match = p
                        break
                    continue
                # A placeholder ("[Emoji]", "[Unreadable message]") is the same bubble as whatever
                # another frame read at that spot; real text must otherwise look alike.
                if not (_placeholder(p.text) or _placeholder(it.text)) and _similar(p.text, it.text) < 0.5:
                    continue
            match = p
            break
        if match is None:
            placed.append(it)
            fresh.append(it)
            continue
        bigger = (it.y1 - it.y0) > (match.y1 - match.y0) + 20
        if bigger:
            match.y0, match.y1 = it.y0, it.y1          # the whole message, not part of it
            # ...including the other half of a link preview read separately
            for q in [q for q in placed if q is not match and q.kind == "msg" and q.side == it.side
                      and _nested(q, it) and (q.y1 - q.y0) < (it.y1 - it.y0)]:
                a, b = q.text.lower(), it.text.lower()
                if _placeholder(q.text) or a in b or _similar(a, b) >= 0.6:
                    placed.remove(q)
        if _better(it, match) or (bigger and not _placeholder(it.text)):
            match.text, match.score = it.text, it.score
            if it.crop is not None:
                match.crop = it.crop
    return fresh


def _possible_half(p: _Item, placed: list[_Item]) -> bool:
    """A photo with a same-side message right under it, or a message with a photo right above:
    could be the two halves of one link preview, read in different frames."""
    for q in placed:
        if q is p or q.kind != "msg" or q.side != p.side:
            continue
        if (0 <= q.y0 - p.y1 <= 30 and _placeholder(p.text)) or (0 <= p.y0 - q.y1 <= 30 and _placeholder(q.text)):
            return True
    return False


def _resolve_overlaps(placed: list[_Item]) -> list[_Item]:
    """Messages never overlap on screen. Where two readings do, one was put in the wrong place
    (usually a strip with too little to line it up by): drop the one that is a copy of a
    message kept elsewhere, else the less certain one."""
    msgs = sorted((p for p in placed if p.kind == "msg"), key=lambda p: p.y0)
    drop: set[int] = set()
    for i, a in enumerate(msgs):
        for b in msgs[i + 1:]:
            if b.y0 >= a.y1:
                break
            if id(a) in drop or id(b) in drop:
                continue
            inner = min(a.y1 - a.y0, b.y1 - b.y0)
            if min(a.y1, b.y1) - max(a.y0, b.y0) < inner * 0.4:
                continue

            def copies(x: _Item) -> int:
                return sum(1 for o in msgs if o is not x and o.side == x.side and id(o) not in drop
                           and _similar(o.text, x.text) >= 0.9 and abs(o.y0 - x.y0) > MERGE_TOLERANCE)

            ka = (copies(a) == 0, not _placeholder(a.text), a.y1 - a.y0, a.score)
            kb = (copies(b) == 0, not _placeholder(b.text), b.y1 - b.y0, b.score)
            drop.add(id(b) if ka >= kb else id(a))
    return [p for p in placed if id(p) not in drop]


def _nested(a: _Item, b: _Item) -> bool:
    """One box (nearly) inside the other, and the inner one is most of a message tall."""
    lo, hi = max(a.y0, b.y0), min(a.y1, b.y1)
    inner = min(a.y1 - a.y0, b.y1 - b.y0)
    return inner > 0 and (hi - lo) >= inner * 0.85


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
    review = directory / "review"
    cv2 = _cv2()
    entries = []
    for i, m in enumerate(result.messages):
        entry = {"text": m.text, "from_me": m.from_me, "sender": m.sender, "header": m.header,
                 "confidence": round(m.confidence, 3)}
        # Worth a glance: what couldn't be read as text (except photos, which are photos) and
        # anything read with low confidence. The review screen shows each as it looked.
        if (m.text.startswith("[") and m.text != "[Photo]") or "[Emoji]" in m.text or m.confidence < 0.6:
            entry["review"] = True
            entry["crop_id"] = i
            if m.crop is not None:
                review.mkdir(exist_ok=True)
                cv2.imwrite(str(review / f"{i}.jpg"), m.crop, [cv2.IMWRITE_JPEG_QUALITY, 88])
        entries.append(entry)
    sidecar = {
        "title": result.title,
        "is_group": result.is_group,
        "recorded_at": result.recorded_at.isoformat(timespec="minutes") if result.recorded_at else None,
        "video": video_name,
        "names": {},
        "messages": entries,
    }
    (directory / SIDECAR).write_text(json.dumps(sidecar, indent=1, ensure_ascii=False), encoding="utf-8")
    meta = _write_db(directory, sidecar)
    Archive.finalize(directory, **meta)
    return directory.name


def update_archive(directory: Path, *, recorded_at: Optional[str] = None,
                   names: Optional[dict[str, str]] = None, title: Optional[str] = None,
                   edits: Optional[dict] = None) -> dict:
    """Correct the recording date (every relative header moves with it), rename people, or fix
    messages from the review screen: {index: new text} or {index: None} to remove one."""
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
    if edits:
        drop = set()
        for key, value in edits.items():
            i = int(key)
            if not 0 <= i < len(data["messages"]):
                continue
            if value is None:
                drop.add(i)
            elif isinstance(value, str) and value.strip():
                m = data["messages"][i]
                m["text"], m["confidence"], m["review"] = value.strip()[:4000], 1.0, False
        data["messages"] = [m for i, m in enumerate(data["messages"]) if i not in drop]
        if not data["messages"]:
            raise RecordingError("That would leave the conversation empty.")
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
