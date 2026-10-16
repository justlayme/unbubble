"""Engine tests: rebuild accuracy, exports, incremental sync, relay crypto and the local server.

Run:  cd helper && pytest -q
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import plistlib
import sqlite3
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import make_fixture  # noqa: E402


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GM_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


@pytest.fixture()
def archive(tmp_path, data_dir):
    from unbubble.archive import Archive
    from unbubble.contacts import ContactBook
    from unbubble.normalize import normalize

    fx = tmp_path / "fixture"
    make_fixture.build(fx, bulk=500)
    directory = Archive.create("import", "test", {"device_name": "Test iPhone"})
    media = fx / "MediaDomain"
    result = normalize(
        fx / "HomeDomain/Library/SMS/sms.db", directory / "archive.db",
        ContactBook.from_ios(fx / "HomeDomain/Library/AddressBook/AddressBook.sqlitedb"),
        lambda f: media / f.removeprefix("~/"), source_label="test",
    )
    Archive.finalize(directory, threads=result.threads, messages=result.messages)
    return Archive(directory.name)


def _by_title(archive, title):
    return next(t for t in archive.threads() if t["title"] == title)


def test_threads_merge_sms_and_imessage_and_resolve_contacts(archive):
    titles = {t["title"] for t in archive.threads()}
    assert titles == {"Maya Okafor", "Tahoe 2024 🏔️", "+44 7700 900123"}
    maya = _by_title(archive, "Maya Okafor")
    assert set(maya["services"]) == {"SMS", "iMessage"}


def test_reactions_replay_adds_and_removals(archive):
    maya = _by_title(archive, "Maya Okafor")
    msgs = list(archive.iter_thread(maya["id"]))
    dinner = next(m for m in msgs if (m["text"] or "").startswith("Dinner Friday"))
    assert sorted(r["emoji"] for r in dinner["reactions"]) == sorted(["❤️", "🔥"])  # the laugh was removed


def test_attributed_body_edits_replies_unsent_effects(archive):
    msgs = list(archive.iter_thread(_by_title(archive, "Maya Okafor")["id"]))
    late = next(m for m in msgs if m["text"] == "Running 15 min late, sorry!")
    assert [e["text"] for e in late["edits"]] == ["Running 10 min late", "Running 15 min late, sorry!"]
    assert late["reply_to"]["text"] == "7:30 works"
    assert any(m["kind"] == "unsent" for m in msgs)
    assert next(m for m in msgs if m["text"] == "the view from our table")["effect"] == "Confetti"


def test_group_events_and_missing_attachments(archive):
    group = list(archive.iter_thread(_by_title(archive, "Tahoe 2024 🏔️")["id"]))
    assert [m["system_text"] for m in group if m["kind"] == "system"] == [
        "You named the conversation “Tahoe 2024 🏔️”", "Jordan Ellis added Sam Reyes", "Sam Reyes left the conversation"]
    atts = [a for m in archive.iter_thread(_by_title(archive, "Maya Okafor")["id"]) for a in m["attachments"]]
    assert {a["name"]: a["missing"] for a in atts} == {"IMG_0412.HEIC": False, "IMG_0413.jpeg": False, "trip.MOV": True}


def test_paging_and_search(archive):
    maya = _by_title(archive, "Maya Okafor")
    first = archive.messages(maya["id"], limit=50)
    older = archive.messages(maya["id"], tuple(first["next_before"]), limit=50)
    assert older["messages"][-1]["ts"] <= first["messages"][0]["ts"]
    assert {m["id"] for m in first["messages"]}.isdisjoint({m["id"] for m in older["messages"]})
    assert archive.search("north beach")


def test_all_exports(archive, tmp_path):
    from unbubble.exporters import run_export

    root = run_export(archive, tmp_path / "out", ["html", "pdf", "json", "csv"])
    assert (root / "Open Messages.html").exists()
    assert len(list((root / "PDF").glob("*.pdf"))) == 3
    data = json.loads(next((root / "Data/json").glob("Maya*.json")).read_text(encoding="utf-8"))
    assert len(data["messages"]) == _by_title(archive, "Maya Okafor")["message_count"]
    assert (root / "Data/messages.csv").read_text(encoding="utf-8-sig").count("\n") > 500
    assert any(p.suffix == ".jpg" for p in (root / "viewer/media").iterdir())  # HEIC converted for browsers


def test_incremental_unpack_only_touches_new_files(data_dir):
    from unbubble import device_store as ds

    udid = "00008130-001A2B3C4D5E"
    d = ds.device_dir(udid)
    d.mkdir(parents=True)
    for name, data in [("Manifest.plist", {"IsEncrypted": False, "Lockdown": {"ProductVersion": "26.0"}}),
                       ("Status.plist", {"Date": 0, "Version": "3.3"}), ("Info.plist", {})]:
        (d / name).write_bytes(plistlib.dumps(data))
    con = sqlite3.connect(d / "Manifest.db")
    con.execute("CREATE TABLE Files (fileID TEXT PRIMARY KEY, domain TEXT, relativePath TEXT, flags INTEGER, file BLOB)")
    fid = lambda dom, rel: hashlib.sha1(f"{dom}-{rel}".encode()).hexdigest()  # noqa: E731
    entries = [("HomeDomain", "Library/SMS/sms.db"), ("MediaDomain", "Library/SMS/Attachments/a/IMG_1.jpeg"),
               ("CameraRollDomain", "Media/DCIM/100APPLE/IMG_9.HEIC")]
    for dom, rel in entries:
        con.execute("INSERT INTO Files VALUES (?,?,?,1,NULL)", (fid(dom, rel), dom, rel))
    con.commit()
    con.close()

    def receive(dom, rel, data):
        f = fid(dom, rel)
        (d / f[:2]).mkdir(exist_ok=True)
        (d / f[:2] / f).write_bytes(data)

    receive(*entries[0], b"v1")
    receive(*entries[1], b"img")
    assert ds.unpack_new_files(udid, "")["files"] == 2
    receive(*entries[0], b"v2")
    assert ds.unpack_new_files(udid, "")["files"] == 1
    assert (ds.unpacked_dir(udid) / "HomeDomain/Library/SMS/sms.db").read_bytes() == b"v2"
    assert ds.has_sync_state(udid)
    assert not any("DCIM" in str(p) for p in data_dir.rglob("*"))


def test_relay_channel_round_trip_and_replay_rejection():
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from unbubble.relay_client import INFO, Channel

    web_sk = ec.generate_private_key(ec.SECP256R1())
    web_pk = web_sk.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    room = "R" * 24
    ch = Channel(room, web_pk)
    # Derive the browser's keys exactly as crypto.ts does.
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ch.pk_raw)
    okm = HKDF(algorithm=hashes.SHA256(), length=64, salt=room.encode(), info=INFO).derive(web_sk.exchange(ec.ECDH(), peer))
    web_recv, web_send = AESGCM(okm[:32]), AESGCM(okm[32:])

    big = b"x" * (1024 * 1024 + 17)
    frames = ch.seal(big)
    assert len(frames) == 3
    out = b"".join(web_recv.decrypt(f[:12], f[12:], None)[1:] for f in frames)
    assert out == big

    nonce = struct.pack(">IQ", 2, 0)
    frame = nonce + web_send.encrypt(nonce, b"\x00" + b'{"id":1}', None)
    assert ch.open(frame) == b'{"id":1}'
    with pytest.raises(ValueError):
        ch.open(frame)  # replay


def test_relay_refuses_untrusted_hosts(monkeypatch):
    from unbubble.relay_client import allowed_relay

    monkeypatch.delenv("GM_RELAY_HOSTS", raising=False)
    assert allowed_relay("wss://relay.thegreymirror.com")
    assert not allowed_relay("ws://relay.thegreymirror.com")
    assert not allowed_relay("wss://relay.thegreymirror.com.evil.example")
    assert not allowed_relay("wss://evil.example")


def test_server_origin_lock_and_rpc(archive):
    import aiohttp
    from aiohttp.test_utils import TestClient, TestServer

    from unbubble.server import build_app

    async def run():
        async with TestClient(TestServer(build_app())) as client:
            r = await client.get("/status", headers={"Origin": "https://evil.example"})
            assert "Access-Control-Allow-Origin" not in r.headers
            with pytest.raises(aiohttp.WSServerHandshakeError):
                await client.ws_connect("/ws", headers={"Origin": "https://evil.example"})
            r = await client.post("/control/url", json={"url": "unbubble://open"},
                                  headers={"Origin": "https://thegreymirror.com", "X-GM-Local": "1"})
            assert r.status == 403
            ws = await client.ws_connect("/ws", headers={"Origin": "https://thegreymirror.com"})
            await ws.send_json({"id": 1, "method": "archives.list", "params": {}})
            msg = await ws.receive_json()
            assert msg["id"] == 1 and msg["result"][0]["id"] == archive.id
            await ws.send_json({"id": 2, "method": "export.reveal", "params": {"path": "/etc"}})
            assert (await ws.receive_json())["error"]["code"] == "not_found"  # only folders it saved
            await ws.close()

    asyncio.run(run())


def test_windows_safe_names_and_long_paths(monkeypatch):
    from pathlib import PureWindowsPath

    from unbubble import windows_support
    from unbubble.util import safe_filename

    assert safe_filename("CON") == "_CON"
    assert safe_filename("aux.txt") == "_aux.txt"
    assert safe_filename('Mom <3 / "Dad"?') == "Mom 3 Dad"
    assert safe_filename("Trailing dots...  ") == "Trailing dots"
    assert safe_filename("   ") == "Untitled"
    monkeypatch.setattr(windows_support, "IS_WINDOWS", True)
    monkeypatch.setattr(windows_support, "Path", lambda p: type("P", (), {"absolute": lambda self: PureWindowsPath(p)})())
    assert windows_support.longpath(r"C:\Users\a\x.jpg") == "\\\\?\\C:\\Users\\a\\x.jpg"
    assert windows_support.longpath(r"\\server\share\x") == "\\\\?\\UNC\\server\\share\\x"


def test_driver_mock_flow(monkeypatch):
    import importlib

    monkeypatch.setenv("GM_MOCK_DEVICE", "1")
    monkeypatch.setenv("GM_MOCK_DRIVER_MISSING", "1")
    from unbubble import capture, mock

    importlib.reload(mock)
    with pytest.raises(capture.CaptureError) as e:
        mock.devices()
    assert e.value.data["hint"] == "windows_drivers"
    asyncio.run(mock.install_driver(lambda *_: None))
    assert mock.devices()[0]["state"] == "ready"


def test_only_thegreymirror_is_trusted():
    from unbubble.server import PROD_ORIGINS
    assert PROD_ORIGINS == {"https://thegreymirror.com", "https://www.thegreymirror.com"}
