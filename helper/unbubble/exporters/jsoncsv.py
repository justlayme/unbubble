"""Machine-readable exports. JSON keeps everything (edits, reactions, replies); CSV is one row per
message for spreadsheets and analysis tools."""

from __future__ import annotations

import csv
import json

from ..util import iso_from_ms


def _att_entry(ctx, a: dict) -> dict:
    return {**a, "file": ctx.copied.get(a["id"])}


def write_json(ctx, progress) -> None:
    out = ctx.root / "Data" / "json"
    out.mkdir(parents=True, exist_ok=True)
    index = []
    for i, t in enumerate(ctx.threads):
        progress(i / max(len(ctx.threads), 1), f"Writing JSON · {t['title']}")
        filename = f"{ctx.thread_dirs[t['id']]}.json"
        with open(out / filename, "w", encoding="utf-8") as fh:
            fh.write('{"thread": ')
            json.dump({**t, "first": iso_from_ms(t["first_ts"]), "last": iso_from_ms(t["last_ts"])}, fh, ensure_ascii=False)
            fh.write(', "messages": [\n')
            first = True
            for m in ctx.archive.iter_thread(t["id"]):
                m = {**m, "time": iso_from_ms(m["ts"]), "attachments": [_att_entry(ctx, a) for a in m["attachments"]]}
                if not first:
                    fh.write(",\n")
                json.dump(m, fh, ensure_ascii=False)
                first = False
            fh.write("\n]}\n")
        index.append({"id": t["id"], "title": t["title"], "file": f"json/{filename}", "messages": t["message_count"]})
    summary = ctx.archive.summary()
    (ctx.root / "Data" / "threads.json").write_text(
        json.dumps({"source": {k: summary.get(k) for k in ("label", "device_name", "model", "ios", "source", "created")},
                    "threads": index}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_csv(ctx, progress) -> None:
    out = ctx.root / "Data"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "threads.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["thread_id", "title", "is_group", "participants", "messages", "sent_by_you", "attachments", "first", "last"])
        for t in ctx.threads:
            w.writerow([t["id"], t["title"], t["is_group"],
                        "; ".join(f"{p['name']} <{', '.join(p['addresses'])}>" for p in t["participants"]),
                        t["message_count"], t["from_me_count"], t["attachment_count"],
                        iso_from_ms(t["first_ts"]), iso_from_ms(t["last_ts"])])
    with open(out / "messages.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["thread_id", "thread", "time_utc", "sender", "from_me", "service", "kind", "text",
                    "reply_to", "reactions", "edited", "unsent", "effect", "attachments"])
        for i, t in enumerate(ctx.threads):
            progress(i / max(len(ctx.threads), 1), f"Writing CSV · {t['title']}")
            for m in ctx.archive.iter_thread(t["id"]):
                w.writerow([
                    t["id"], t["title"], iso_from_ms(m["ts"]), m["sender"], int(m["from_me"]), m["service"],
                    m["kind"], m["text"] or m["system_text"] or "",
                    (m["reply_to"] or {}).get("text", "") if m["reply_to"] else "",
                    " ".join(f"{r['emoji'] or r['kind']}:{r['sender']}" for r in m["reactions"]),
                    int(bool(m["edits"])), int(m["is_unsent"]), m["effect"] or "",
                    " | ".join(ctx.copied.get(a["id"]) or a["name"] or "" for a in m["attachments"]),
                ])
