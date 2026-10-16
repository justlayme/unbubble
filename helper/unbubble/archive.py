"""Read side of a captured archive: summaries, thread lists, paged messages and search."""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterator, Optional

from .util import data_dir


def archives_root() -> Path:
    root = data_dir() / "archives"
    root.mkdir(parents=True, exist_ok=True)
    return root


class Archive:
    def __init__(self, archive_id: str) -> None:
        if not archive_id or "/" in archive_id or "\\" in archive_id or ".." in archive_id:
            raise ValueError("Invalid archive id")
        self.id = archive_id
        self.dir = archives_root() / archive_id
        if not (self.dir / "manifest.json").exists():
            raise FileNotFoundError(f"No archive {archive_id}")
        self._con: Optional[sqlite3.Connection] = None

    # ---- lifecycle ------------------------------------------------------------------------

    @staticmethod
    def create(source: str, label: str, extra: Optional[dict] = None) -> Path:
        archive_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        directory = archives_root() / archive_id
        directory.mkdir(parents=True)
        manifest = {"id": archive_id, "source": source, "label": label, "created": int(time.time() * 1000),
                    "status": "building", **(extra or {})}
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2))
        return directory

    @staticmethod
    def finalize(directory: Path, **fields: Any) -> None:
        path = directory / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest.update(fields)
        manifest["status"] = "ready"
        path.write_text(json.dumps(manifest, indent=2))

    @staticmethod
    def list_all() -> list[dict]:
        items = []
        for d in sorted(archives_root().iterdir(), reverse=True):
            mf = d / "manifest.json"
            if not mf.exists():
                continue
            try:
                manifest = json.loads(mf.read_text())
            except json.JSONDecodeError:
                continue
            if manifest.get("status") != "ready":
                continue
            items.append(manifest)
        return items

    @staticmethod
    def discard_incomplete() -> None:
        for d in archives_root().iterdir():
            mf = d / "manifest.json"
            try:
                if not mf.exists() or json.loads(mf.read_text()).get("status") != "ready":
                    shutil.rmtree(d, ignore_errors=True)
            except (OSError, json.JSONDecodeError):
                shutil.rmtree(d, ignore_errors=True)

    def delete(self) -> None:
        self.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    @property
    def con(self) -> sqlite3.Connection:
        if self._con is None:
            self._con = sqlite3.connect(self.dir / "archive.db", check_same_thread=False)
            self._con.row_factory = sqlite3.Row
        return self._con

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    @property
    def manifest(self) -> dict:
        return json.loads((self.dir / "manifest.json").read_text())

    # ---- queries --------------------------------------------------------------------------

    def summary(self) -> dict:
        meta = {row["key"]: json.loads(row["value"]) for row in self.con.execute("SELECT key, value FROM meta")}
        busiest = [
            dict(r) for r in self.con.execute(
                "SELECT id, title, message_count FROM threads ORDER BY message_count DESC LIMIT 5"
            )
        ]
        per_year = [
            {"year": r[0], "count": r[1]} for r in self.con.execute(
                "SELECT strftime('%Y', ts/1000, 'unixepoch') AS y, COUNT(*) FROM messages"
                " WHERE ts IS NOT NULL GROUP BY y ORDER BY y"
            )
        ]
        return {**self.manifest, "stats": meta, "busiest": busiest, "per_year": per_year}

    def people(self) -> dict[int, dict]:
        return {
            r["id"]: {"id": r["id"], "name": r["name"], "addresses": json.loads(r["addresses"]), "is_contact": bool(r["is_contact"])}
            for r in self.con.execute("SELECT * FROM people")
        }

    def threads(self, query: str = "") -> list[dict]:
        people = self.people()
        rows = self.con.execute("SELECT * FROM threads ORDER BY last_ts DESC").fetchall()
        q = query.strip().lower()
        out = []
        for r in rows:
            participants = [people[p] for p in json.loads(r["participant_ids"]) if p in people]
            if q and q not in r["title"].lower() and not any(
                q in a.lower() for p in participants for a in p["addresses"]
            ):
                continue
            out.append({
                "id": r["id"], "title": r["title"], "is_group": bool(r["is_group"]),
                "services": json.loads(r["services"]), "participants": participants,
                "message_count": r["message_count"], "attachment_count": r["attachment_count"],
                "from_me_count": r["from_me_count"], "first_ts": r["first_ts"], "last_ts": r["last_ts"],
                "last_preview": r["last_preview"],
            })
        return out

    def _hydrate(self, rows: list[sqlite3.Row], people: dict[int, dict]) -> list[dict]:
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        guids = [r["guid"] for r in rows]
        marks = ",".join("?" * len(ids))
        atts: dict[int, list[dict]] = {}
        for a in self.con.execute(f"SELECT * FROM attachments WHERE message_id IN ({marks})", ids):
            atts.setdefault(a["message_id"], []).append({
                "id": a["id"], "name": a["name"], "mime": a["mime"], "uti": a["uti"], "bytes": a["bytes"],
                "missing": bool(a["missing"]), "is_sticker": bool(a["is_sticker"]),
            })
        reacts: dict[str, list[dict]] = {}
        for x in self.con.execute(
            f"SELECT * FROM reactions WHERE message_guid IN ({','.join('?' * len(guids))}) ORDER BY ts", guids
        ):
            reacts.setdefault(x["message_guid"], []).append({
                "kind": x["kind"], "emoji": x["emoji"], "part": x["part"], "from_me": bool(x["from_me"]),
                "sender": "You" if x["from_me"] else people.get(x["sender_id"], {}).get("name", "Someone"),
                "ts": x["ts"],
            })
        reply_guids = list({r["reply_to_guid"] for r in rows if r["reply_to_guid"]})
        replies: dict[str, dict] = {}
        if reply_guids:
            for p in self.con.execute(
                f"SELECT guid, text, from_me, sender_id, attachment_count FROM messages WHERE guid IN ({','.join('?' * len(reply_guids))})",
                reply_guids,
            ):
                replies[p["guid"]] = {
                    "guid": p["guid"],
                    "text": p["text"] or ("Attachment" if p["attachment_count"] else ""),
                    "sender": "You" if p["from_me"] else people.get(p["sender_id"], {}).get("name", "Someone"),
                }
        out = []
        for r in rows:
            out.append({
                "id": r["id"], "guid": r["guid"], "ts": r["ts"], "from_me": bool(r["from_me"]),
                "sender": "You" if r["from_me"] else people.get(r["sender_id"], {}).get("name", "Unknown"),
                "sender_id": r["sender_id"], "text": r["text"], "subject": r["subject"], "service": r["service"],
                "kind": r["kind"], "system_text": r["system_text"], "effect": r["effect"], "app": r["app"],
                "reply_to": replies.get(r["reply_to_guid"]) if r["reply_to_guid"] else None,
                "edits": json.loads(r["edits"]) if r["edits"] else None, "is_unsent": bool(r["is_unsent"]),
                "is_audio": bool(r["is_audio"]), "read_ts": r["read_ts"], "delivered_ts": r["delivered_ts"],
                "attachments": atts.get(r["id"], []), "reactions": reacts.get(r["guid"], []),
            })
        return out

    def messages(self, thread_id: int, before: Optional[tuple[int, int]] = None, limit: int = 200) -> dict:
        """Newest-first pages; `before` is the (ts, id) cursor of the oldest message already loaded."""
        limit = max(1, min(int(limit), 1000))
        people = self.people()
        if before:
            rows = self.con.execute(
                "SELECT * FROM messages WHERE thread_id = ? AND (COALESCE(ts,0) < ? OR (COALESCE(ts,0) = ? AND id < ?))"
                " ORDER BY COALESCE(ts,0) DESC, id DESC LIMIT ?",
                (thread_id, before[0], before[0], before[1], limit + 1),
            ).fetchall()
        else:
            rows = self.con.execute(
                "SELECT * FROM messages WHERE thread_id = ? ORDER BY COALESCE(ts,0) DESC, id DESC LIMIT ?",
                (thread_id, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        rows = list(reversed(rows[:limit]))
        msgs = self._hydrate(rows, people)
        cursor = [rows[0]["ts"] or 0, rows[0]["id"]] if rows and has_more else None
        return {"messages": msgs, "next_before": cursor}

    def iter_thread(self, thread_id: int, chunk: int = 2000) -> Iterator[dict]:
        people = self.people()
        last = (-1, -1)
        while True:
            rows = self.con.execute(
                "SELECT * FROM messages WHERE thread_id = ? AND (COALESCE(ts,0) > ? OR (COALESCE(ts,0) = ? AND id > ?))"
                " ORDER BY COALESCE(ts,0), id LIMIT ?",
                (thread_id, last[0], last[0], last[1], chunk),
            ).fetchall()
            if not rows:
                return
            yield from self._hydrate(rows, people)
            last = (rows[-1]["ts"] or 0, rows[-1]["id"])

    def search(self, query: str, thread_id: Optional[int] = None, limit: int = 100) -> list[dict]:
        q = query.strip()
        if not q:
            return []
        fts = " ".join('"' + tok.replace('"', '""') + '"*' for tok in q.split())
        params: list[Any] = [fts]
        sql = (
            "SELECT m.id, m.thread_id, m.ts, m.from_me, m.sender_id, t.title,"
            " snippet(messages_fts, 0, '\u0002', '\u0003', '…', 14) AS snip"
            " FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid JOIN threads t ON t.id = m.thread_id"
            " WHERE messages_fts MATCH ?"
        )
        if thread_id:
            sql += " AND m.thread_id = ?"
            params.append(thread_id)
        sql += " ORDER BY m.ts DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        people = self.people()
        return [
            {"id": r["id"], "thread_id": r["thread_id"], "thread_title": r["title"], "ts": r["ts"],
             "sender": "You" if r["from_me"] else people.get(r["sender_id"], {}).get("name", "Unknown"),
             "snippet": r["snip"]}
            for r in self.con.execute(sql, params)
        ]

    def locate(self, thread_id: int, message_id: int, context: int = 60) -> dict:
        """A page of messages centred on one message (for jumping to a search hit)."""
        people = self.people()
        anchor = self.con.execute("SELECT ts, id FROM messages WHERE id = ?", (message_id,)).fetchone()
        if not anchor:
            return {"messages": [], "next_before": None}
        ts, mid = anchor["ts"] or 0, anchor["id"]
        older = self.con.execute(
            "SELECT * FROM messages WHERE thread_id = ? AND (COALESCE(ts,0) < ? OR (COALESCE(ts,0) = ? AND id <= ?))"
            " ORDER BY COALESCE(ts,0) DESC, id DESC LIMIT ?",
            (thread_id, ts, ts, mid, context + 1),
        ).fetchall()
        newer = self.con.execute(
            "SELECT * FROM messages WHERE thread_id = ? AND (COALESCE(ts,0) > ? OR (COALESCE(ts,0) = ? AND id > ?))"
            " ORDER BY COALESCE(ts,0), id LIMIT ?",
            (thread_id, ts, ts, mid, context),
        ).fetchall()
        has_more = len(older) > context
        older = list(reversed(older[:context]))
        rows = older + list(newer)
        cursor = [older[0]["ts"] or 0, older[0]["id"]] if older and has_more else None
        return {"messages": self._hydrate(rows, people), "next_before": cursor, "anchor": message_id,
                "has_newer": len(newer) == context}

    def attachment_path(self, attachment_id: int) -> Optional[tuple[Path, Optional[str], str]]:
        row = self.con.execute("SELECT src_path, mime, name FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
        if not row or not row["src_path"]:
            return None
        path = Path(row["src_path"])
        return (path, row["mime"], row["name"]) if path.exists() else None
