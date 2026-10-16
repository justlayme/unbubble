"""Build a realistic iPhone capture layout for tests:

    <out>/HomeDomain/Library/SMS/sms.db
    <out>/HomeDomain/Library/AddressBook/AddressBook.sqlitedb
    <out>/MediaDomain/Library/SMS/Attachments/...
"""

from __future__ import annotations

import plistlib
import random
import sqlite3
import sys
from pathlib import Path

APPLE = 978_307_200


def ns(unix_seconds: float) -> int:
    return int((unix_seconds - APPLE) * 1e9)


def attributed_body(text: str) -> bytes:
    """A typedstream NSAttributedString blob in the layout Messages writes."""
    raw = text.encode("utf-8")
    if len(raw) < 0x80:
        length = bytes([len(raw)])
    else:
        length = b"\x81" + len(raw).to_bytes(2, "little")
    return (
        b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00"
        b"\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + length + raw +
        b"\x86\x84\x02iI\x01" + bytes([min(len(text), 127)]) + b"\x92\x84\x84\x84\x0cNSDictionary\x00\x94\x84\x01i\x01"
        b"\x92\x84\x96\x96\x1d__kIMMessagePartAttributeName\x86\x92\x84\x84\x84\x08NSNumber\x00\x84\x84\x07NSValue"
        b"\x00\x94\x84\x01*\x84\x99\x99\x00\x86\x86\x86"
    )


SCHEMA = """
CREATE TABLE handle (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL, country TEXT, service TEXT NOT NULL,
  uncanonicalized_id TEXT, person_centric_id TEXT);
CREATE TABLE chat (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, style INTEGER, state INTEGER,
  account_id TEXT, properties BLOB, chat_identifier TEXT, service_name TEXT, room_name TEXT, account_login TEXT,
  is_archived INTEGER DEFAULT 0, last_addressed_handle TEXT, display_name TEXT, group_id TEXT, is_filtered INTEGER,
  successful_query INTEGER, engram_id TEXT, server_change_token TEXT, ck_sync_state INTEGER DEFAULT 0,
  original_group_id TEXT, last_read_message_timestamp INTEGER DEFAULT 0, cloudkit_record_id TEXT,
  last_addressed_sim_id TEXT, is_blackholed INTEGER DEFAULT 0, syndication_date INTEGER DEFAULT 0,
  syndication_type INTEGER DEFAULT 0, is_recovered INTEGER DEFAULT 0, is_deleting_incoming_messages INTEGER DEFAULT 0);
CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER, UNIQUE(chat_id, handle_id));
CREATE TABLE message (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, text TEXT, replace INTEGER DEFAULT 0,
  service_center TEXT, handle_id INTEGER DEFAULT 0, subject TEXT, country TEXT, attributedBody BLOB, version INTEGER DEFAULT 0,
  type INTEGER DEFAULT 0, service TEXT, account TEXT, account_guid TEXT, error INTEGER DEFAULT 0, date INTEGER,
  date_read INTEGER, date_delivered INTEGER, is_delivered INTEGER DEFAULT 0, is_finished INTEGER DEFAULT 0,
  is_emote INTEGER DEFAULT 0, is_from_me INTEGER DEFAULT 0, is_empty INTEGER DEFAULT 0, is_delayed INTEGER DEFAULT 0,
  is_auto_reply INTEGER DEFAULT 0, is_prepared INTEGER DEFAULT 0, is_read INTEGER DEFAULT 0, is_system_message INTEGER DEFAULT 0,
  is_sent INTEGER DEFAULT 0, has_dd_results INTEGER DEFAULT 0, is_service_message INTEGER DEFAULT 0,
  is_forward INTEGER DEFAULT 0, was_downgraded INTEGER DEFAULT 0, is_archive INTEGER DEFAULT 0, cache_has_attachments INTEGER DEFAULT 0,
  cache_roomnames TEXT, was_data_detected INTEGER DEFAULT 0, was_deduplicated INTEGER DEFAULT 0, is_audio_message INTEGER DEFAULT 0,
  is_played INTEGER DEFAULT 0, date_played INTEGER, item_type INTEGER DEFAULT 0, other_handle INTEGER DEFAULT 0,
  group_title TEXT, group_action_type INTEGER DEFAULT 0, share_status INTEGER DEFAULT 0, share_direction INTEGER DEFAULT 0,
  is_expirable INTEGER DEFAULT 0, expire_state INTEGER DEFAULT 0, message_action_type INTEGER DEFAULT 0,
  message_source INTEGER DEFAULT 0, associated_message_guid TEXT, associated_message_type INTEGER DEFAULT 0,
  balloon_bundle_id TEXT, payload_data BLOB, expressive_send_style_id TEXT, associated_message_range_location INTEGER DEFAULT 0,
  associated_message_range_length INTEGER DEFAULT 0, time_expressive_send_played INTEGER, message_summary_info BLOB,
  ck_sync_state INTEGER DEFAULT 0, ck_record_id TEXT, ck_record_change_tag TEXT, destination_caller_id TEXT,
  is_corrupt INTEGER DEFAULT 0, reply_to_guid TEXT, sort_id INTEGER, is_spam INTEGER DEFAULT 0,
  has_unseen_mention INTEGER DEFAULT 0, thread_originator_guid TEXT, thread_originator_part TEXT,
  syndication_ranges TEXT, synced_syndication_ranges TEXT, was_delivered_quietly INTEGER DEFAULT 0,
  did_notify_recipient INTEGER DEFAULT 0, date_retracted INTEGER, date_edited INTEGER, was_detonated INTEGER DEFAULT 0,
  part_count INTEGER, is_stewie INTEGER DEFAULT 0, is_kt_verified INTEGER DEFAULT 0, is_sos INTEGER DEFAULT 0,
  is_critical INTEGER DEFAULT 0, bia_reference_id TEXT, fallback_hash TEXT, associated_message_emoji TEXT,
  is_pending_satellite_send INTEGER DEFAULT 0, needs_relay INTEGER DEFAULT 0, schedule_type INTEGER DEFAULT 0,
  schedule_state INTEGER DEFAULT 0, sent_or_received_off_grid INTEGER DEFAULT 0);
CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER, message_date INTEGER DEFAULT 0,
  PRIMARY KEY (chat_id, message_id));
CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, created_date INTEGER DEFAULT 0,
  start_date INTEGER DEFAULT 0, filename TEXT, uti TEXT, mime_type TEXT, transfer_state INTEGER DEFAULT 0,
  is_outgoing INTEGER DEFAULT 0, user_info BLOB, transfer_name TEXT, total_bytes INTEGER DEFAULT 0,
  is_sticker INTEGER DEFAULT 0, sticker_user_info BLOB, attribution_info BLOB, hide_attachment INTEGER DEFAULT 0,
  ck_sync_state INTEGER DEFAULT 0, ck_server_change_token_blob BLOB, ck_record_id TEXT, original_guid TEXT UNIQUE,
  is_commsafety_sensitive INTEGER DEFAULT 0, emoji_image_content_identifier TEXT,
  emoji_image_short_description TEXT, preview_generation_state INTEGER DEFAULT 0);
CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER, UNIQUE(message_id, attachment_id));
"""


def build(out: Path, bulk: int = 0) -> None:
    sms_dir = out / "HomeDomain" / "Library" / "SMS"
    ab_dir = out / "HomeDomain" / "Library" / "AddressBook"
    att_root = out / "MediaDomain" / "Library" / "SMS" / "Attachments"
    for d in (sms_dir, ab_dir, att_root):
        d.mkdir(parents=True, exist_ok=True)
    db_path = sms_dir / "sms.db"
    db_path.unlink(missing_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)

    h = {}
    for key, addr, svc in [
        ("maya_im", "+14155550142", "iMessage"), ("maya_sms", "+14155550142", "SMS"),
        ("jordan", "jordan.ellis@icloud.com", "iMessage"), ("sam", "+12125550199", "iMessage"),
        ("unknown", "+447700900123", "iMessage"),
    ]:
        cur = con.execute("INSERT INTO handle (id, service, country) VALUES (?, ?, 'us')", (addr, svc))
        h[key] = cur.lastrowid

    def chat(guid, ident, style, service, members, name=None):
        cid = con.execute("INSERT INTO chat (guid, chat_identifier, style, service_name, display_name) VALUES (?,?,?,?,?)",
                          (guid, ident, style, service, name)).lastrowid
        for m in members:
            con.execute("INSERT INTO chat_handle_join VALUES (?, ?)", (cid, h[m]))
        return cid

    c_maya_im = chat("iMessage;-;+14155550142", "+14155550142", 45, "iMessage", ["maya_im"])
    c_maya_sms = chat("SMS;-;+14155550142", "+14155550142", 45, "SMS", ["maya_sms"])
    c_group = chat("iMessage;+;chat8812", "chat8812", 43, "iMessage", ["jordan", "sam", "maya_im"])
    c_unknown = chat("iMessage;-;+447700900123", "+447700900123", 45, "iMessage", ["unknown"])

    counter = [0]

    def msg(chat_id, t, text=None, me=0, handle=None, **kw):
        counter[0] += 1
        guid = kw.pop("guid", f"GUID-{counter[0]:05d}")
        cols = {"guid": guid, "text": text, "is_from_me": me, "handle_id": h[handle] if handle else 0,
                "date": ns(t), "service": kw.pop("service", "iMessage"), **kw}
        q = f"INSERT INTO message ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
        mid = con.execute(q, list(cols.values())).lastrowid
        con.execute("INSERT INTO chat_message_join VALUES (?, ?, ?)", (chat_id, mid, ns(t)))
        return mid, guid

    def attach(mid, rel, mime, uti, name, data: bytes | None):
        path = att_root / rel
        if data is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        aid = con.execute(
            "INSERT INTO attachment (guid, filename, mime_type, uti, transfer_name, total_bytes) VALUES (?,?,?,?,?,?)",
            (f"ATT-{mid}-{name}", f"~/Library/SMS/Attachments/{rel}", mime, uti, name, len(data or b"")),
        ).lastrowid
        con.execute("INSERT INTO message_attachment_join VALUES (?, ?)", (mid, aid))
        con.execute("UPDATE message SET cache_has_attachments = 1 WHERE ROWID = ?", (mid,))

    t0 = 1_700_000_000  # Nov 2023
    # --- Maya, over SMS first, then iMessage (should be ONE thread) -------------------------
    msg(c_maya_sms, t0, "hey it's maya, new number", handle="maya_sms", service="SMS")
    msg(c_maya_sms, t0 + 60, "Saved 👋", me=1, service="SMS")
    _, g_dinner = msg(c_maya_im, t0 + 86400, None, handle="maya_im",
                      attributedBody=attributed_body("Dinner Friday? I found a place in North Beach 🍝"))
    msg(c_maya_im, t0 + 86400 + 30, "Yes!! What time", me=1,
        date_read=ns(t0 + 86400 + 90), date_delivered=ns(t0 + 86400 + 31))
    # tapbacks on the dinner message: love (kept), laugh then removed, custom emoji
    msg(c_maya_im, t0 + 86400 + 40, "Loved “Dinner Friday?…”", me=1, associated_message_guid=f"p:0/{g_dinner}",
        associated_message_type=2000)
    msg(c_maya_im, t0 + 86400 + 41, "Laughed at …", handle="maya_im", associated_message_guid=f"p:0/{g_dinner}",
        associated_message_type=2003)
    msg(c_maya_im, t0 + 86400 + 42, "Removed a laugh", handle="maya_im", associated_message_guid=f"p:0/{g_dinner}",
        associated_message_type=3003)
    msg(c_maya_im, t0 + 86400 + 43, "Reacted 🔥", handle="maya_im", associated_message_guid=f"p:0/{g_dinner}",
        associated_message_type=2006, associated_message_emoji="🔥")
    # reply thread + edit history
    _, g_time = msg(c_maya_im, t0 + 86400 + 120, "7:30 works", handle="maya_im")
    edits = {"ec": {"0": [{"d": ns(t0 + 86400 + 200) / 1e9 * 1e9, "t": attributed_body("Running 10 min late")},
                          {"d": ns(t0 + 86400 + 260), "t": attributed_body("Running 15 min late, sorry!")}]}}
    msg(c_maya_im, t0 + 86400 + 200, "Running 15 min late, sorry!", me=1, thread_originator_guid=g_time,
        reply_to_guid=g_time, message_summary_info=plistlib.dumps(edits, fmt=plistlib.FMT_BINARY),
        date_edited=ns(t0 + 86400 + 260))
    msg(c_maya_im, t0 + 86400 + 300, None, handle="maya_im", date_retracted=ns(t0 + 86400 + 330),
        message_summary_info=plistlib.dumps({"rp": [0]}, fmt=plistlib.FMT_BINARY))
    mid, _ = msg(c_maya_im, t0 + 86400 * 2, "the view from our table", handle="maya_im",
                 expressive_send_style_id="com.apple.messages.effect.CKConfettiEffect")
    attach(mid, "a1/01/AAA-1/IMG_0412.HEIC", "image/heic", "public.heic", "IMG_0412.HEIC", _heic_bytes())
    mid, _ = msg(c_maya_im, t0 + 86400 * 2 + 50, None, me=1)
    attach(mid, "b2/02/BBB-2/IMG_0413.jpeg", "image/jpeg", "public.jpeg", "IMG_0413.jpeg", _jpeg_bytes((90, 120, 160)))
    mid, _ = msg(c_maya_im, t0 + 86400 * 2 + 70, "old video", handle="maya_im")
    attach(mid, "c3/03/CCC-3/trip.MOV", "video/quicktime", "com.apple.quicktime-movie", "trip.MOV", None)  # offloaded
    msg(c_maya_im, t0 + 86400 * 3, "https://maps.apple.com/?q=North+Beach", handle="maya_im",
        balloon_bundle_id="com.apple.messages.URLBalloonProvider")

    # --- Group chat ---------------------------------------------------------------------------
    msg(c_group, t0 + 86400 * 4, None, me=1, item_type=2, group_title="Tahoe 2024 🏔️")
    msg(c_group, t0 + 86400 * 4 + 10, None, handle="jordan", item_type=1, group_action_type=0, other_handle=h["sam"])
    msg(c_group, t0 + 86400 * 4 + 20, "Cabin is booked for Feb 9–12", handle="jordan")
    msg(c_group, t0 + 86400 * 4 + 25, "I'll drive", handle="sam")
    _, g_split = msg(c_group, t0 + 86400 * 4 + 30, "Splitting it 4 ways, I'll send the request", me=1)
    msg(c_group, t0 + 86400 * 4 + 35, "Liked …", handle="jordan", associated_message_guid=f"bp:{g_split}",
        associated_message_type=2001)
    msg(c_group, t0 + 86400 * 4 + 36, "Emphasized …", handle="sam", associated_message_guid=f"p:0/{g_split}",
        associated_message_type=2004)
    msg(c_group, t0 + 86400 * 4 + 40, "Ünïcödé, 日本語, Ελληνικά & emoji 🫶🏽 all render", handle="maya_im")
    msg(c_group, t0 + 86400 * 4 + 50, None, handle="sam", item_type=3, group_action_type=0)

    # --- Unknown number (not in contacts) -----------------------------------------------------
    msg(c_unknown, t0 + 86400 * 5, "Your parcel is waiting for pickup", handle="unknown", service="iMessage")

    # --- optional bulk history for performance testing -----------------------------------------
    rnd = random.Random(7)
    words = "ok yes no maybe tonight tomorrow lol love that sounds good call me later can't wait omw see you".split()
    for i in range(bulk):
        me = rnd.random() < 0.48
        msg(c_maya_im, t0 - 86400 * 400 + i * 600, " ".join(rnd.choice(words) for _ in range(rnd.randint(2, 18))),
            me=int(me), handle=None if me else "maya_im")
    con.commit()
    con.close()

    ab = ab_dir / "AddressBook.sqlitedb"
    ab.unlink(missing_ok=True)
    c = sqlite3.connect(ab)
    c.executescript("""
      CREATE TABLE ABPerson (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, First TEXT, Last TEXT, Organization TEXT, Nickname TEXT);
      CREATE TABLE ABMultiValue (UID INTEGER PRIMARY KEY, record_id INTEGER, property INTEGER, identifier INTEGER, label INTEGER, value TEXT);
    """)
    for first, last, phones, emails in [
        ("Maya", "Okafor", ["(415) 555-0142"], []),
        ("Jordan", "Ellis", [], ["Jordan.Ellis@icloud.com"]),
        ("Sam", "Reyes", ["+1 212-555-0199"], []),
    ]:
        rid = c.execute("INSERT INTO ABPerson (First, Last) VALUES (?, ?)", (first, last)).lastrowid
        for p in phones:
            c.execute("INSERT INTO ABMultiValue (record_id, property, value) VALUES (?, 3, ?)", (rid, p))
        for e in emails:
            c.execute("INSERT INTO ABMultiValue (record_id, property, value) VALUES (?, 4, ?)", (rid, e))
    c.commit()
    c.close()


def _jpeg_bytes(color) -> bytes:
    from io import BytesIO

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (900, 1200), color)
    d = ImageDraw.Draw(img)
    for y in range(0, 1200, 40):
        d.line([(0, y), (900, y + 300)], fill=(color[0] + 40, color[1] + 40, color[2] + 40), width=6)
    buf = BytesIO()
    img.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def _heic_bytes() -> bytes:
    from io import BytesIO

    import pillow_heif
    from PIL import Image

    pillow_heif.register_heif_opener()
    img = Image.new("RGB", (1200, 900), (40, 70, 110))
    buf = BytesIO()
    img.save(buf, "HEIF", quality=70)
    return buf.getvalue()


if __name__ == "__main__":
    build(Path(sys.argv[1]), bulk=int(sys.argv[2]) if len(sys.argv) > 2 else 0)
    print("fixture written to", sys.argv[1])
