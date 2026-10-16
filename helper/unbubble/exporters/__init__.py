"""Exports: JSON + CSV, a browsable offline HTML archive, and one PDF per conversation.

Layout of an export folder:

    <Device> — 2026-10-15 14.02/
      Open Messages.html        ← the browsable archive (works offline, any browser)
      viewer/                   ← its data files
      PDF/<Conversation>.pdf
      Data/threads.json, Data/messages.csv, Data/threads.csv, Data/json/<Conversation>.json
      Attachments/<Conversation>/<file>
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..archive import Archive
from ..util import safe_filename
from ..windows_support import longpath

Progress = Callable[[float, str], None]
FORMATS = ("html", "pdf", "json", "csv")


@dataclass
class ExportContext:
    archive: Archive
    root: Path
    threads: list[dict]
    formats: set[str]
    include_attachments: bool
    progress: Progress
    thread_dirs: dict[int, str] = field(default_factory=dict)
    # attachment id -> path relative to export root (only for copied files)
    copied: dict[int, str] = field(default_factory=dict)


def _unique_names(threads: list[dict]) -> dict[int, str]:
    used: dict[str, int] = {}
    names: dict[int, str] = {}
    for t in threads:
        base = safe_filename(t["title"], 60)
        n = used.get(base.lower(), 0)
        used[base.lower()] = n + 1
        names[t["id"]] = base if n == 0 else f"{base} ({n + 1})"
    return names


def copy_attachments(ctx: ExportContext) -> None:
    total = sum(t["attachment_count"] for t in ctx.threads) or 1
    done = 0
    for t in ctx.threads:
        if not t["attachment_count"]:
            continue
        folder = ctx.root / "Attachments" / ctx.thread_dirs[t["id"]]
        rows = ctx.archive.con.execute(
            "SELECT a.id, a.name, a.src_path FROM attachments a JOIN messages m ON m.id = a.message_id"
            " WHERE m.thread_id = ? AND a.missing = 0 ORDER BY m.ts", (t["id"],)
        ).fetchall()
        for row in rows:
            done += 1
            src = Path(row["src_path"])
            if not os.path.exists(longpath(src)):
                continue
            os.makedirs(longpath(folder), exist_ok=True)
            name = safe_filename(row["name"] or src.name, 100)
            dest = folder / f"{row['id']}-{name}"
            try:
                shutil.copy2(longpath(src), longpath(dest))
            except OSError:
                continue
            ctx.copied[row["id"]] = dest.relative_to(ctx.root).as_posix()
            if done % 50 == 0:
                ctx.progress(0.05 + 0.25 * done / total, f"Copying attachments ({done:,} of {total:,})")


def run_export(
    archive: Archive,
    dest_root: Path,
    formats: list[str],
    thread_ids: Optional[list[int]] = None,
    include_attachments: bool = True,
    progress: Progress = lambda f, s: None,
) -> Path:
    from . import html_archive, jsoncsv, pdf

    fmt = {f for f in formats if f in FORMATS} or {"html"}
    all_threads = archive.threads()
    threads = [t for t in all_threads if not thread_ids or t["id"] in set(thread_ids)]
    label = archive.manifest.get("device_name") or archive.manifest.get("label") or "Messages"
    root = dest_root / safe_filename(f"{label} — {time.strftime('%Y-%m-%d %H.%M')}", 100)
    root.mkdir(parents=True, exist_ok=True)
    ctx = ExportContext(archive, root, threads, fmt, include_attachments, progress, _unique_names(threads))

    progress(0.02, "Preparing export")
    if include_attachments:
        copy_attachments(ctx)
    steps = [s for s in ("json", "csv", "html", "pdf") if s in fmt]
    span = 0.68 / max(len(steps), 1)
    for i, step in enumerate(steps):
        base = 0.3 + span * i

        def sub(f: float, msg: str, base: float = base) -> None:
            progress(base + span * f, msg)

        if step == "json":
            jsoncsv.write_json(ctx, sub)
        elif step == "csv":
            jsoncsv.write_csv(ctx, sub)
        elif step == "html":
            html_archive.write_html(ctx, sub)
        elif step == "pdf":
            pdf.write_pdfs(ctx, sub)
    progress(1.0, "Export complete")
    return root
