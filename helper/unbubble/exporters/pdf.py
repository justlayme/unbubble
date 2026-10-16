"""One PDF per conversation, suitable for printing, records and legal use.

Text is set in a Unicode font with per-character fallback (so names, accents, CJK and emoji
don't turn into boxes), photos are embedded inline, and every page carries the conversation,
source device, export date and page number.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..media import browser_image, is_image

INK = colors.HexColor("#16181D")
GRAPHITE = colors.HexColor("#4A505A")
HAZE = colors.HexColor("#B9BEC6")
SILVER = colors.HexColor("#F1F2F4")
TARNISH = colors.HexColor("#5B6E86")

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"

_REGULAR_CANDIDATES = [
    FONT_DIR / "NotoSans-Regular.ttf",
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    Path("/Library/Fonts/Arial Unicode.ttf"),
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf",
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
]
_BOLD_CANDIDATES = [
    FONT_DIR / "NotoSans-SemiBold.ttf",
    FONT_DIR / "NotoSans-Bold.ttf",
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeuib.ttf",
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arialbd.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
]
_FALLBACK_CANDIDATES = [
    FONT_DIR / "NotoEmoji-Regular.ttf",
    FONT_DIR / "NotoSansSymbols2-Regular.ttf",
    FONT_DIR / "NotoSansJP-Regular.ttf",
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "seguisym.ttf",
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "seguiemj.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
]


class Fonts:
    _instance: Optional["Fonts"] = None

    def __init__(self) -> None:
        self.regular = self._register("GMRegular", _REGULAR_CANDIDATES) or "Helvetica"
        self.bold = self._register("GMBold", _BOLD_CANDIDATES) or (self.regular if self.regular != "Helvetica" else "Helvetica-Bold")
        self.fallbacks: list[tuple[str, set[int]]] = []
        for i, path in enumerate(_FALLBACK_CANDIDATES):
            name = self._register(f"GMFallback{i}", [path])
            if name:
                self.fallbacks.append((name, self._coverage(name)))
        self.coverage = self._coverage(self.regular) if self.regular != "Helvetica" else set(range(32, 256))

    @classmethod
    def get(cls) -> "Fonts":
        if cls._instance is None:
            cls._instance = Fonts()
        return cls._instance

    @staticmethod
    def _register(name: str, candidates: list[Path]) -> Optional[str]:
        for path in candidates:
            try:
                if path.exists():
                    pdfmetrics.registerFont(TTFont(name, str(path)))
                    return name
            except Exception:
                continue
        return None

    @staticmethod
    def _coverage(name: str) -> set[int]:
        try:
            return set(pdfmetrics.getFont(name).face.charToGlyph.keys())
        except Exception:
            return set()

    def markup(self, text: str) -> str:
        """Escape text and wrap characters the main font lacks in a fallback font run."""
        out: list[str] = []
        run_font: Optional[str] = None
        buf: list[str] = []

        def flush() -> None:
            if not buf:
                return
            chunk = escape("".join(buf)).replace("\n", "<br/>")
            out.append(f'<font name="{run_font}">{chunk}</font>' if run_font else chunk)
            buf.clear()

        for ch in text:
            cp = ord(ch)
            font = None
            if cp > 127 and ch != "\n" and cp not in self.coverage and not (0xFE00 <= cp <= 0xFE0F or cp == 0x200D):
                font = next((name for name, cov in self.fallbacks if cp in cov), None)
            if 0xFE00 <= cp <= 0xFE0F or cp == 0x200D or 0x1F3FB <= cp <= 0x1F3FF:
                continue  # variation selectors, ZWJ and skin-tone swatches can't be drawn in a monochrome font
            if font != run_font:
                flush()
                run_font = font
            buf.append(ch)
        flush()
        return "".join(out)


def _styles(f: Fonts) -> dict[str, ParagraphStyle]:
    return {
        "title": ParagraphStyle("title", fontName=f.bold, fontSize=22, leading=26, textColor=INK, spaceAfter=6),
        "meta": ParagraphStyle("meta", fontName=f.regular, fontSize=9.5, leading=13, textColor=GRAPHITE),
        "theirs": ParagraphStyle("theirs", fontName=f.regular, fontSize=10.5, leading=14, textColor=INK),
        "mine": ParagraphStyle("mine", fontName=f.regular, fontSize=10.5, leading=14, textColor=colors.white),
        "who": ParagraphStyle("who", fontName=f.bold, fontSize=8.5, leading=11, textColor=GRAPHITE),
        "time_l": ParagraphStyle("time_l", fontName=f.regular, fontSize=7.5, leading=10, textColor=GRAPHITE, alignment=TA_LEFT),
        "time_r": ParagraphStyle("time_r", fontName=f.regular, fontSize=7.5, leading=10, textColor=GRAPHITE, alignment=2),
        "day": ParagraphStyle("day", fontName=f.bold, fontSize=8.5, leading=12, textColor=GRAPHITE, alignment=TA_CENTER,
                              spaceBefore=10, spaceAfter=6),
        "sys": ParagraphStyle("sys", fontName=f.regular, fontSize=8.5, leading=11, textColor=GRAPHITE, alignment=TA_CENTER),
    }


def _local(ts: Optional[int]) -> Optional[datetime]:
    return datetime.fromtimestamp(ts / 1000) if ts else None


def _fmt_time(ts: Optional[int]) -> str:
    d = _local(ts)
    return d.strftime("%I:%M %p").lstrip("0") if d else ""


def _fmt_day(ts: Optional[int]) -> str:
    d = _local(ts)
    return d.strftime("%A, %B %d, %Y").replace(" 0", " ") if d else "Undated"


def write_pdfs(ctx, progress) -> None:
    out_dir = ctx.root / "PDF"
    out_dir.mkdir(parents=True, exist_ok=True)
    fonts = Fonts.get()
    styles = _styles(fonts)
    thumbs = ctx.archive.dir / "cache" / "pdf"
    manifest = ctx.archive.manifest
    source = manifest.get("device_name") or manifest.get("label") or "Messages"
    exported = datetime.now().strftime("%B %d, %Y at %I:%M %p").replace(" 0", " ")
    for i, t in enumerate(ctx.threads):
        progress(i / max(len(ctx.threads), 1), f"Writing PDF · {t['title']}")
        _write_thread_pdf(ctx, t, out_dir / f"{ctx.thread_dirs[t['id']]}.pdf", fonts, styles, thumbs, source, exported)


def _bubble(text_markup: str, style: ParagraphStyle, mine: bool, max_w: float, plain: str, fonts: Fonts) -> Table:
    longest = max((stringWidth(line, fonts.regular, style.fontSize) for line in (plain or " ").split("\n")), default=0)
    width = min(max_w, longest + 24)
    para = Paragraph(text_markup or "&nbsp;", style)
    tbl = Table([[para]], colWidths=[width], hAlign="RIGHT" if mine else "LEFT")
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), INK if mine else SILVER),
        ("BOX", (0, 0), (-1, -1), 0 if mine else 0.6, HAZE if not mine else INK),
        ("ROUNDEDCORNERS", [9, 9, 9, 9]),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return tbl


def _write_thread_pdf(ctx, t, path: Path, fonts: Fonts, st, thumbs: Path, source: str, exported: str) -> None:
    page_w, page_h = LETTER
    margin = 0.7 * inch
    frame_w = page_w - 2 * margin
    max_bubble = frame_w * 0.72
    title = t["title"]

    def on_page(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont(fonts.regular, 7.5)
        canvas.setFillColor(GRAPHITE)
        safe_title = "".join(ch for ch in title if ord(ch) in fonts.coverage or ord(ch) < 128).strip()
        canvas.drawString(margin, 0.45 * inch, f"{safe_title} — exported from {source} by Unbubble on {exported}"[:150])
        canvas.drawRightString(page_w - margin, 0.45 * inch, f"Page {doc.page}")
        canvas.setStrokeColor(HAZE)
        canvas.setLineWidth(0.5)
        canvas.line(margin, 0.6 * inch, page_w - margin, 0.6 * inch)
        canvas.restoreState()

    doc = SimpleDocTemplate(str(path), pagesize=LETTER, leftMargin=margin, rightMargin=margin,
                            topMargin=0.65 * inch, bottomMargin=0.8 * inch, title=title,
                            author="Unbubble", subject=f"Messages with {title}", creator="Unbubble")
    story: list = [Paragraph(fonts.markup(title), st["title"])]
    people = "<br/>".join(
        fonts.markup(f"{p['name']} — {', '.join(p['addresses'])}") for p in t["participants"]
    )
    first, last = _fmt_day(t["first_ts"]), _fmt_day(t["last_ts"])
    story.append(Paragraph(
        f"{people}<br/><br/>{t['message_count']:,} messages ({t['from_me_count']:,} sent by you), "
        f"{t['attachment_count']:,} attachments<br/>{escape(first)} to {escape(last)}<br/>"
        f"Source: {escape(source)}. Times shown in this computer's time zone.", st["meta"]))
    story.append(Spacer(1, 14))

    last_day = None
    prev_sender = None
    for m in ctx.archive.iter_thread(t["id"]):
        day = _fmt_day(m["ts"])
        if day != last_day:
            story.append(Paragraph(escape(day), st["day"]))
            last_day = day
            prev_sender = None
        if m["kind"] == "system":
            story.append(Paragraph(fonts.markup(f"{m['system_text'] or ''}  {_fmt_time(m['ts'])}"), st["sys"]))
            prev_sender = None
            continue
        mine = m["from_me"]
        block: list = []
        if t["is_group"] and not mine and prev_sender != m["sender"]:
            block.append(Paragraph(fonts.markup(m["sender"]), st["who"]))
        prev_sender = "me" if mine else m["sender"]
        if m["reply_to"]:
            quote = f"Replying to {m['reply_to']['sender']}: {(m['reply_to']['text'] or '')[:120]}"
            block.append(Paragraph(fonts.markup(quote), st["time_r" if mine else "time_l"]))
        for a in m["attachments"]:
            block.append(_attachment_flowable(ctx, a, mine, st, fonts, thumbs))
        style = st["mine" if mine else "theirs"]
        if m["kind"] == "unsent":
            block.append(Paragraph(fonts.markup(f"{'You' if mine else m['sender']} unsent a message"),
                                   st["time_r" if mine else "time_l"]))
        elif m["text"] or m["subject"]:
            plain = ((m["subject"] + "\n") if m["subject"] else "") + (m["text"] or "")
            mark = (f"<b>{fonts.markup(m['subject'])}</b><br/>" if m["subject"] else "") + fonts.markup(m["text"] or "")
            block.append(_bubble(mark, style, mine, max_bubble, plain, fonts))
        details = [_fmt_time(m["ts"])]
        if m["service"] and m["service"] != "iMessage":
            details.append(m["service"])
        if m["effect"]:
            details.append(f"sent with {m['effect']}")
        if m["reactions"]:
            details.append("reactions: " + ", ".join(f"{r['emoji'] or r['kind']} {r['sender']}" for r in m["reactions"]))
        if m["edits"]:
            details.append("edited; earlier versions: " + " / ".join(e["text"] for e in m["edits"][:-1]))
        block.append(Paragraph(fonts.markup("  ·  ".join(d for d in details if d)), st["time_r" if mine else "time_l"]))
        block.append(Spacer(1, 5))
        story.append(KeepTogether(block) if len(block) <= 4 else block[0])
        if len(block) > 4:
            story.extend(block[1:])
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)


def _attachment_flowable(ctx, a: dict, mine: bool, st, fonts: Fonts, thumbs: Path):
    label_style = st["time_r" if mine else "time_l"]
    if a["missing"]:
        return Paragraph(fonts.markup(f"[{a['name'] or 'Attachment'} — not stored on the device]"), label_style)
    found = ctx.archive.attachment_path(a["id"])
    if found and is_image(found[0], found[1]):
        img = browser_image(found[0], found[1], thumbs, max_edge=900)
        if img:
            try:
                flow = Image(str(img[0]))
                scale = min(200 / flow.imageWidth, 240 / flow.imageHeight, 1)
                flow.drawWidth, flow.drawHeight = flow.imageWidth * scale, flow.imageHeight * scale
                flow.hAlign = "RIGHT" if mine else "LEFT"
                return flow
            except Exception:
                pass
    rel = ctx.copied.get(a["id"])
    name = a["name"] or "Attachment"
    return Paragraph(fonts.markup(f"[Attachment: {name}{' — saved as ' + rel if rel else ''}]"), label_style)
