"""Browsable offline archive: open "Open Messages.html" in any browser, no internet needed."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from ..media import browser_image, needs_conversion

VIEWER_DIR = Path(__file__).parent / "viewer"
CHUNK = 1500

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Messages</title>
<link rel="stylesheet" href="viewer/viewer.css">
</head>
<body>
<div class="shell">
  <aside class="side" aria-label="Conversations">
    <header>
      <h1></h1>
      <p class="source"></p>
      <input class="filter" type="search" placeholder="Find a conversation" aria-label="Find a conversation">
    </header>
    <ul class="threads"></ul>
  </aside>
  <main class="convo">
    <div class="convo-head">
      <div>
        <button class="back" type="button">All conversations</button>
        <h2>Choose a conversation</h2>
        <p></p>
      </div>
      <input class="find" type="search" placeholder="Search this conversation" aria-label="Search this conversation">
    </div>
    <div class="stream" tabindex="0"></div>
  </main>
</div>
<script src="viewer/index.js"></script>
<script src="viewer/viewer.js"></script>
</body>
</html>
"""


def _compact(m: dict, att_files: dict, previews: dict) -> dict:
    out: dict = {"t": m["ts"], "s": m["sender"], "k": m["kind"]}
    if m["from_me"]:
        out["me"] = 1
    for src, dst in (("text", "x"), ("subject", "sub"), ("system_text", "y"), ("effect", "fx"), ("app", "app")):
        if m.get(src):
            out[dst] = m[src]
    if m.get("service"):
        out["svc"] = m["service"]
    if m.get("reply_to"):
        out["r"] = {"s": m["reply_to"]["sender"], "x": (m["reply_to"]["text"] or "")[:140]}
    if m.get("edits"):
        out["ed"] = m["edits"]
    if m["attachments"]:
        out["a"] = [
            {k: v for k, v in {"n": a["name"], "m": a["mime"], "f": att_files.get(a["id"]), "p": previews.get(a["id"])}.items() if v}
            for a in m["attachments"]
        ]
    if m["reactions"]:
        out["re"] = [{"e": r["emoji"], "k": r["kind"], "s": r["sender"]} for r in m["reactions"]]
    return out


def write_html(ctx, progress) -> None:
    viewer = ctx.root / "viewer"
    (viewer / "t").mkdir(parents=True, exist_ok=True)
    shutil.copy2(VIEWER_DIR / "viewer.css", viewer / "viewer.css")
    shutil.copy2(VIEWER_DIR / "viewer.js", viewer / "viewer.js")

    # Browser-safe previews for formats Chrome/Edge/Firefox can't show (HEIC).
    previews: dict[int, str] = {}
    if ctx.copied:
        media_dir = viewer / "media"
        rows = ctx.archive.con.execute("SELECT id, mime, src_path FROM attachments WHERE missing = 0").fetchall()
        for row in rows:
            if row["id"] not in ctx.copied or not row["src_path"]:
                continue
            src = Path(row["src_path"])
            if needs_conversion(src, row["mime"]):
                converted = browser_image(src, row["mime"], media_dir, max_edge=2048)
                if converted:
                    previews[row["id"]] = converted[0].relative_to(ctx.root).as_posix()

    manifest = ctx.archive.manifest
    index = {
        "label": manifest.get("device_name") or manifest.get("label") or "Messages",
        "subtitle": "Exported " + time.strftime("%B %d, %Y").replace(" 0", " "),
        "threads": [],
    }

    for i, t in enumerate(ctx.threads):
        progress(i / max(len(ctx.threads), 1), f"Building browsable archive · {t['title']}")
        batch: list[dict] = []
        n = 0
        for m in ctx.archive.iter_thread(t["id"]):
            batch.append(_compact(m, ctx.copied, previews))
            if len(batch) >= CHUNK:
                _write_chunk(viewer, t["id"], n, batch)
                n += 1
                batch = []
        if batch or n == 0:
            _write_chunk(viewer, t["id"], n, batch)
            n += 1
        index["threads"].append({
            "id": t["id"], "title": t["title"], "group": t["is_group"], "count": t["message_count"],
            "first": t["first_ts"], "last": t["last_ts"], "preview": (t["last_preview"] or "")[:120], "chunks": n,
            "addresses": ", ".join(a for p in t["participants"] for a in p["addresses"])[:200],
        })

    (viewer / "index.js").write_text("window.GM_INDEX = " + json.dumps(index, ensure_ascii=False) + ";\n", encoding="utf-8")
    (ctx.root / "Open Messages.html").write_text(PAGE, encoding="utf-8")


def _write_chunk(viewer: Path, tid: int, n: int, batch: list[dict]) -> None:
    payload = json.dumps(batch, ensure_ascii=False, separators=(",", ":"))
    # Guard against "</script>" style sequences and U+2028/2029 inside a script file.
    payload = payload.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    (viewer / "t" / f"{tid}-{n}.js").write_text(f"GM_chunk({tid},{n},{payload});\n", encoding="utf-8")
