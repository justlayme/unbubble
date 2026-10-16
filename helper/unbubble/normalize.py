"""Turn an Apple Messages database (iPhone `sms.db` or Mac `chat.db`) into a Grey Mirror archive.

The archive is our own small SQLite file with a stable schema, so the web app and every
exporter are insulated from Apple's schema drift across iOS/macOS versions. Columns that do not
exist on an older database are read as NULL instead of failing.
"""

from __future__ import annotations

import json
import plistlib
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .contacts import ContactBook
from .typedstream_text import best_text, decode_attributed_body
from .util import address_key, apple_to_unix_ms, pretty_address

ProgressFn = Callable[[float, str], None]
AttachmentResolver = Callable[[str], Optional[Path]]

ARCHIVE_SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY, key TEXT UNIQUE, name TEXT, addresses TEXT, is_contact INTEGER
);
CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY, key TEXT UNIQUE, title TEXT, is_group INTEGER, services TEXT,
    participant_ids TEXT, message_count INTEGER DEFAULT 0, attachment_count INTEGER DEFAULT 0,
    first_ts INTEGER, last_ts INTEGER, last_preview TEXT, from_me_count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY, thread_id INTEGER, guid TEXT UNIQUE, ts INTEGER, from_me INTEGER,
    sender_id INTEGER, text TEXT, subject TEXT, service TEXT, kind TEXT, system_text TEXT,
    effect TEXT, app TEXT, reply_to_guid TEXT, edits TEXT, is_unsent INTEGER DEFAULT 0,
    is_audio INTEGER DEFAULT 0, read_ts INTEGER, delivered_ts INTEGER, attachment_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_messages_thread_ts ON messages(thread_id, ts, id);
CREATE TABLE IF NOT EXISTS reactions (
    id INTEGER PRIMARY KEY, message_guid TEXT, part INTEGER, kind TEXT, emoji TEXT,
    from_me INTEGER, sender_id INTEGER, ts INTEGER
);
CREATE INDEX IF NOT EXISTS idx_reactions_guid ON reactions(message_guid);
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY, message_id INTEGER, guid TEXT, name TEXT, mime TEXT, uti TEXT,
    bytes INTEGER, src_path TEXT, missing INTEGER, is_sticker INTEGER
);
CREATE INDEX IF NOT EXISTS idx_attachments_message ON attachments(message_id);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(text, content='messages', content_rowid='id');
"""

TAPBACKS = {0: "love", 1: "like", 2: "dislike", 3: "laugh", 4: "emphasize", 5: "question", 6: "emoji", 7: "sticker"}
TAPBACK_EMOJI = {"love": "❤️", "like": "👍", "dislike": "👎", "laugh": "😂", "emphasize": "‼️", "question": "❓"}

EFFECTS = {
    "com.apple.MobileSMS.expressivesend.impact": "Slam",
    "com.apple.MobileSMS.expressivesend.loud": "Loud",
    "com.apple.MobileSMS.expressivesend.gentle": "Gentle",
    "com.apple.MobileSMS.expressivesend.invisibleink": "Invisible Ink",
    "com.apple.messages.effect.CKConfettiEffect": "Confetti",
    "com.apple.messages.effect.CKFireworksEffect": "Fireworks",
    "com.apple.messages.effect.CKHeartEffect": "Love",
    "com.apple.messages.effect.CKHappyBirthdayEffect": "Balloons",
    "com.apple.messages.effect.CKShootingStarEffect": "Shooting Star",
    "com.apple.messages.effect.CKSparklesEffect": "Celebration",
    "com.apple.messages.effect.CKSpotlightEffect": "Spotlight",
    "com.apple.messages.effect.CKLasersEffect": "Lasers",
    "com.apple.messages.effect.CKEchoEffect": "Echo",
}

_ASSOC_GUID = re.compile(r"^(?:p:\d+/|bp:)?(.+)$")
_ASSOC_PART = re.compile(r"^p:(\d+)/")


@dataclass
class NormalizeResult:
    threads: int = 0
    messages: int = 0
    attachments: int = 0
    missing_attachments: int = 0
    reactions: int = 0
    people: int = 0
    first_ts: Optional[int] = None
    last_ts: Optional[int] = None
    warnings: list[str] = field(default_factory=list)


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    except sqlite3.DatabaseError:
        return set()


def _select(cols: set[str], wanted: list[str], alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return ", ".join(f"{prefix}{c}" if c in cols else f"NULL AS {c}" for c in wanted)


def _app_label(bundle_id: Optional[str]) -> Optional[str]:
    if not bundle_id:
        return None
    if "URLBalloonProvider" in bundle_id:
        return "link"
    if "PassbookUIService" in bundle_id or "ApplePay" in bundle_id:
        return "Apple Cash"
    if "Handwriting" in bundle_id or "DigitalTouch" in bundle_id:
        return "Handwriting"
    if "findmy" in bundle_id.lower() or "location" in bundle_id.lower():
        return "Location"
    tail = bundle_id.rsplit(":", 1)[-1].rsplit(".", 1)[-1]
    return tail or "iMessage app"


def _decode_edits(blob: Optional[bytes]) -> tuple[list[dict], bool]:
    """Return (edit history, has_retracted_parts) from `message_summary_info`."""
    if not blob:
        return [], False
    try:
        info = plistlib.loads(bytes(blob))
    except Exception:
        return [], False
    edits: list[dict] = []
    ec = info.get("ec") if isinstance(info, dict) else None
    if isinstance(ec, dict):
        for versions in ec.values():
            if not isinstance(versions, list):
                continue
            for version in versions:
                if not isinstance(version, dict):
                    continue
                text = decode_attributed_body(version.get("t"))
                ts = apple_to_unix_ms(version.get("d")) if isinstance(version.get("d"), (int, float)) else None
                if text is not None:
                    edits.append({"text": text, "ts": ts})
    retracted = bool(isinstance(info, dict) and info.get("rp"))
    edits.sort(key=lambda e: e["ts"] or 0)
    return edits, retracted


def normalize(
    source_db: Path,
    archive_db: Path,
    contacts: ContactBook,
    resolve_attachment: AttachmentResolver,
    *,
    source_label: str,
    region: str = "US",
    progress: ProgressFn = lambda f, s: None,
) -> NormalizeResult:
    result = NormalizeResult()
    src = sqlite3.connect(f"file:{source_db.as_posix()}?mode=ro", uri=True)
    src.text_factory = lambda b: b.decode("utf-8", errors="replace")
    if archive_db.exists():
        archive_db.unlink()
    out = sqlite3.connect(archive_db)
    out.executescript(ARCHIVE_SCHEMA)

    # ---- people ---------------------------------------------------------------------------
    progress(0.02, "Reading contacts")
    handle_cols = _columns(src, "handle")
    handles = {
        row[0]: (row[1], row[2])
        for row in src.execute(f"SELECT ROWID, {_select(handle_cols, ['id', 'service'])} FROM handle")
    }
    person_ids: dict[str, int] = {}
    person_rows: dict[int, dict] = {}
    handle_to_person: dict[int, int] = {}

    def person_for(address: Optional[str]) -> Optional[int]:
        if not address:
            return None
        contact = contacts.lookup(address)
        key = f"c:{contact.contact_id}" if contact else f"a:{address_key(address)}"
        pid = person_ids.get(key)
        if pid is None:
            pid = len(person_ids) + 1
            person_ids[key] = pid
            person_rows[pid] = {
                "key": key,
                "name": contact.name if contact else pretty_address(address, region),
                "addresses": [],
                "is_contact": 1 if contact else 0,
            }
        if address not in person_rows[pid]["addresses"]:
            person_rows[pid]["addresses"].append(address)
        return pid

    for hid, (addr, _service) in handles.items():
        pid = person_for(addr)
        if pid:
            handle_to_person[hid] = pid

    # ---- chats -> threads -----------------------------------------------------------------
    progress(0.05, "Mapping conversations")
    chat_cols = _columns(src, "chat")
    chats = list(
        src.execute(
            f"SELECT ROWID, {_select(chat_cols, ['guid', 'chat_identifier', 'display_name', 'service_name', 'style'])} FROM chat"
        )
    )
    chat_members: dict[int, list[int]] = defaultdict(list)
    for chat_id, handle_id in src.execute("SELECT chat_id, handle_id FROM chat_handle_join"):
        pid = handle_to_person.get(handle_id)
        if pid and pid not in chat_members[chat_id]:
            chat_members[chat_id].append(pid)

    thread_key_for_chat: dict[int, str] = {}
    threads: dict[str, dict] = {}
    for chat_id, guid, identifier, display_name, service, style in chats:
        members = chat_members.get(chat_id, [])
        is_group = style == 43 or len(members) > 1
        if not is_group and not members and identifier:
            pid = person_for(identifier)
            members = [pid] if pid else []
        if is_group:
            key = f"g:{guid or chat_id}"
        elif members:
            key = f"p:{person_rows[members[0]]['key']}"
        else:
            key = f"x:{guid or chat_id}"
        thread_key_for_chat[chat_id] = key
        t = threads.setdefault(
            key,
            {"is_group": is_group, "members": [], "services": set(), "display_name": None},
        )
        for m in members:
            if m not in t["members"]:
                t["members"].append(m)
        if service:
            t["services"].add(service)
        if display_name and not t["display_name"]:
            t["display_name"] = display_name

    thread_ids: dict[str, int] = {}
    for idx, (key, t) in enumerate(threads.items(), start=1):
        thread_ids[key] = idx
        if t["display_name"]:
            title = t["display_name"]
        else:
            names = [person_rows[m]["name"] for m in t["members"]]
            if not names:
                title = "Unknown"
            elif len(names) <= 3:
                title = ", ".join(names[:-1]) + (" & " if len(names) > 1 else "") + names[-1]
            else:
                title = f"{names[0]}, {names[1]} & {len(names) - 2} others"
        t["title"] = title

    message_chat: dict[int, int] = {}
    for chat_id, message_id in src.execute("SELECT chat_id, message_id FROM chat_message_join"):
        message_chat.setdefault(message_id, chat_id)

    # ---- attachments index ----------------------------------------------------------------
    att_cols = _columns(src, "attachment")
    att_by_message: dict[int, list[tuple]] = defaultdict(list)
    if att_cols:
        q = (
            "SELECT maj.message_id, a.ROWID, "
            + _select(att_cols, ["guid", "filename", "mime_type", "uti", "transfer_name", "total_bytes", "is_sticker", "hide_attachment"], "a")
            + " FROM message_attachment_join maj JOIN attachment a ON a.ROWID = maj.attachment_id"
        )
        for row in src.execute(q):
            att_by_message[row[0]].append(row[1:])

    # ---- messages -------------------------------------------------------------------------
    msg_cols = _columns(src, "message")
    wanted = [
        "guid", "text", "attributedBody", "handle_id", "is_from_me", "date", "date_read",
        "date_delivered", "service", "subject", "associated_message_guid", "associated_message_type",
        "associated_message_emoji", "thread_originator_guid", "expressive_send_style_id", "item_type",
        "group_action_type", "group_title", "other_handle", "message_summary_info", "date_edited",
        "date_retracted", "is_audio_message", "balloon_bundle_id",
    ]
    total = src.execute("SELECT COUNT(*) FROM message").fetchone()[0] or 1
    cursor = src.execute(f"SELECT ROWID, {_select(msg_cols, wanted)} FROM message ORDER BY date, ROWID")

    stats: dict[int, dict] = defaultdict(lambda: {"count": 0, "att": 0, "first": None, "last": None, "preview": None, "me": 0})
    pending_reactions: list[tuple] = []
    latest_group_title: dict[int, Optional[str]] = {}
    msg_batch: list[tuple] = []
    att_batch: list[tuple] = []
    seen = 0

    def flush() -> None:
        if msg_batch:
            out.executemany(
                "INSERT OR IGNORE INTO messages (id, thread_id, guid, ts, from_me, sender_id, text, subject, service, kind,"
                " system_text, effect, app, reply_to_guid, edits, is_unsent, is_audio, read_ts, delivered_ts, attachment_count)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                msg_batch,
            )
            msg_batch.clear()
        if att_batch:
            out.executemany(
                "INSERT INTO attachments (message_id, guid, name, mime, uti, bytes, src_path, missing, is_sticker)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                att_batch,
            )
            att_batch.clear()

    for row in cursor:
        seen += 1
        if seen % 5000 == 0:
            progress(0.08 + 0.82 * seen / total, f"Rebuilding messages ({seen:,} of {total:,})")
            flush()
        (rowid, guid, text, body, handle_id, from_me, date, date_read, date_delivered, service, subject,
         assoc_guid, assoc_type, assoc_emoji, reply_guid, effect_id, item_type, group_action, group_title,
         other_handle, summary_info, date_edited, date_retracted, is_audio, balloon) = row

        chat_id = message_chat.get(rowid)
        if chat_id is None:
            continue
        thread_id = thread_ids[thread_key_for_chat[chat_id]]
        ts = apple_to_unix_ms(date)
        sender = None if from_me else handle_to_person.get(handle_id)
        assoc_type = assoc_type or 0

        # Tapbacks are stored as standalone messages pointing at their target.
        if assoc_guid and (2000 <= assoc_type <= 2007 or 3000 <= assoc_type <= 3007 or assoc_type == 1000):
            m = _ASSOC_GUID.match(assoc_guid)
            part_match = _ASSOC_PART.match(assoc_guid)
            target = m.group(1) if m else assoc_guid
            if assoc_type == 1000:
                kind, removed = "sticker", False
            else:
                kind = TAPBACKS.get(assoc_type % 1000, "emoji")
                removed = assoc_type >= 3000
            emoji = assoc_emoji or TAPBACK_EMOJI.get(kind)
            pending_reactions.append(
                (target, int(part_match.group(1)) if part_match else 0, kind, emoji, int(bool(from_me)), sender, ts, removed)
            )
            continue

        body_text = best_text(text, body)
        edits, retracted_parts = _decode_edits(summary_info)
        is_unsent = bool(date_retracted) or (retracted_parts and not body_text)
        kind = "message"
        system_text = None
        item_type = item_type or 0
        if item_type != 0:
            kind = "system"
            actor = "You" if from_me else (person_rows[sender]["name"] if sender else "Someone")
            other = handle_to_person.get(other_handle) if other_handle else None
            other_name = person_rows[other]["name"] if other else "someone"
            if item_type == 1:
                system_text = f"{actor} {'removed' if group_action == 1 else 'added'} {other_name}"
            elif item_type == 2:
                latest_group_title[thread_id] = group_title or None
                system_text = f"{actor} named the conversation “{group_title}”" if group_title else f"{actor} removed the name"
            elif item_type == 3:
                system_text = f"{actor} changed the group photo" if group_action == 1 else f"{actor} left the conversation"
            elif item_type == 4:
                system_text = f"{actor} shared a location"
            elif item_type == 5:
                system_text = f"{actor} kept an audio message"
            elif item_type == 6:
                system_text = "FaceTime call"
            else:
                system_text = "Conversation update"
        if is_unsent:
            kind = "unsent"

        atts = att_by_message.get(rowid, [])
        visible_atts = [a for a in atts if not a[-1]]
        for a_rowid, a_guid, filename, mime, uti, transfer_name, total_bytes, is_sticker, _hidden in visible_atts:
            src_path = resolve_attachment(filename) if filename else None
            missing = 0 if (src_path and src_path.exists()) else 1
            result.missing_attachments += missing
            att_batch.append(
                (rowid, a_guid, transfer_name or (Path(filename).name if filename else "attachment"),
                 mime, uti, total_bytes, str(src_path) if src_path else None, missing, int(bool(is_sticker)))
            )
        result.attachments += len(visible_atts)

        msg_batch.append(
            (rowid, thread_id, guid, ts, int(bool(from_me)), sender, body_text, subject, service, kind,
             system_text, EFFECTS.get(effect_id or "", None), _app_label(balloon), reply_guid,
             json.dumps(edits) if len(edits) > 1 else None, int(is_unsent), int(bool(is_audio)),
             apple_to_unix_ms(date_read), apple_to_unix_ms(date_delivered), len(visible_atts))
        )
        result.messages += 1
        s = stats[thread_id]
        s["count"] += 1
        s["att"] += len(visible_atts)
        s["me"] += int(bool(from_me))
        if ts is not None:
            s["first"] = ts if s["first"] is None else min(s["first"], ts)
            if s["last"] is None or ts >= s["last"]:
                s["last"] = ts
                if kind == "message":
                    s["preview"] = body_text or (f"{len(visible_atts)} attachment" + ("s" if len(visible_atts) != 1 else "") if visible_atts else s["preview"])
            result.first_ts = ts if result.first_ts is None else min(result.first_ts, ts)
            result.last_ts = ts if result.last_ts is None else max(result.last_ts, ts)
    flush()

    # ---- reactions: replay adds/removes in order so the final state is what the user saw ---
    progress(0.92, "Applying reactions")
    state: dict[tuple, tuple] = {}
    for target, part, kind, emoji, rme, sender, ts, removed in pending_reactions:
        who = ("me",) if rme else ("p", sender)
        slot = (target, part, who)
        if removed:
            state.pop(slot, None)
        else:
            state[slot] = (target, part, kind, emoji, rme, sender, ts)
    out.executemany(
        "INSERT INTO reactions (message_guid, part, kind, emoji, from_me, sender_id, ts) VALUES (?,?,?,?,?,?,?)",
        list(state.values()),
    )
    result.reactions = len(state)

    # ---- write threads + people -----------------------------------------------------------
    progress(0.95, "Indexing for search")
    for key, t in threads.items():
        tid = thread_ids[key]
        s = stats.get(tid)
        if not s or s["count"] == 0:
            continue
        if not t["display_name"] and latest_group_title.get(tid):
            t["title"] = latest_group_title[tid]
        out.execute(
            "INSERT INTO threads (id, key, title, is_group, services, participant_ids, message_count, attachment_count,"
            " first_ts, last_ts, last_preview, from_me_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, key, t["title"], int(t["is_group"]), json.dumps(sorted(t["services"])), json.dumps(t["members"]),
             s["count"], s["att"], s["first"], s["last"], s["preview"], s["me"]),
        )
        result.threads += 1
    out.executemany(
        "INSERT INTO people (id, key, name, addresses, is_contact) VALUES (?,?,?,?,?)",
        [(pid, p["key"], p["name"], json.dumps(p["addresses"]), p["is_contact"]) for pid, p in person_rows.items()],
    )
    result.people = len(person_rows)
    out.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
    meta = {
        "source": source_label,
        "threads": result.threads,
        "messages": result.messages,
        "attachments": result.attachments,
        "missing_attachments": result.missing_attachments,
        "reactions": result.reactions,
        "people": result.people,
        "first_ts": result.first_ts,
        "last_ts": result.last_ts,
        "contacts_resolved": len(contacts),
    }
    out.executemany("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", [(k, json.dumps(v)) for k, v in meta.items()])
    out.commit()
    out.close()
    src.close()
    progress(1.0, "Done")
    return result
