"""Screen recordings of iPhone Messages → conversations.

The two fixtures are real H.264 and HEVC videos of an iOS Messages look-alike scrolled the way a
person flicks back through a thread (made by recording_fixture.py + record_fixture.mjs), with the
exact conversation they show in the matching .json.
"""
from __future__ import annotations

import asyncio
import json
import struct
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import pytest

from unbubble.recording import (
    RecordingError, parse_header, read_recording, recording_created_at, update_archive, write_archive,
)

FIXTURES = Path(__file__).parent / "fixtures" / "recordings"
NOW = datetime(2026, 10, 16, 21, 30)          # Friday


# ---- time headers -----------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Today 9:41 AM", datetime(2026, 10, 16, 9, 41)),
    ("Yesterday 11:02 PM", datetime(2026, 10, 15, 23, 2)),
    ("Saturday 1:30 AM", datetime(2026, 10, 10, 1, 30)),
    ("Friday 8:00 PM", datetime(2026, 10, 9, 20, 0)),            # a weekday name is never today
    ("Sat, Oct 11 at 3:20 PM", datetime(2026, 10, 11, 15, 20)),
    ("Oct 3, 2025 at 3:20 PM", datetime(2025, 10, 3, 15, 20)),
    ("Dec 24 at 7:05 PM", datetime(2025, 12, 24, 19, 5)),        # no year and in the future: last year
    ("11 Oct at 15:20", datetime(2026, 10, 11, 15, 20)),         # day-first, 24-hour regions
    ("Today 12:15 AM", datetime(2026, 10, 16, 0, 15)),
    # What OCR does to small grey text: drops spaces, reads O as zero.
    ("Thu,Oct8at5:47PM", datetime(2026, 10, 8, 17, 47)),
    ("Wed,0ct7at1:06PM", datetime(2026, 10, 7, 13, 6)),
    ("Tuesday4:32PM", datetime(2026, 10, 13, 16, 32)),
])
def test_parse_header(text, expected):
    assert parse_header(text, NOW) == expected


@pytest.mark.parametrize("text", ["Delivered", "Read 9:41 PM", "lol", "Today", "25:99 PM", "Feb 30 at 1:00 PM"])
def test_parse_header_rejects(text):
    assert parse_header(text, NOW) is None


def test_recording_created_at(tmp_path):
    # A minimal QuickTime file: moov > mvhd (version 0) with a creation time.
    seconds = int((datetime(2026, 10, 16, 21, 30) - datetime(1904, 1, 1)).total_seconds())
    mvhd_body = bytes([0, 0, 0, 0]) + struct.pack(">II", seconds, seconds) + b"\0" * 88
    mvhd = struct.pack(">I4s", 8 + len(mvhd_body), b"mvhd") + mvhd_body
    moov = struct.pack(">I4s", 8 + len(mvhd), b"moov") + mvhd
    ftyp = struct.pack(">I4s", 16, b"ftyp") + b"qt  \0\0\0\0"
    path = tmp_path / "x.mov"
    path.write_bytes(ftyp + moov)
    got = recording_created_at(path)
    assert got is not None and abs((got - datetime.fromtimestamp(datetime(2026, 10, 16, 21, 30).replace(
        tzinfo=__import__("datetime").timezone.utc).timestamp())).total_seconds()) < 1
    path.write_bytes(ftyp)
    assert recording_created_at(path) is None


# ---- reading real videos ----------------------------------------------------------------------

def _score(name: str, video: str):
    truth = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    result = read_recording(FIXTURES / video, recorded_at=datetime.fromisoformat(truth["recorded_at"]))
    want = [m for m in truth["messages"]]
    got = result.messages
    sm = SequenceMatcher(None, [m["text"].lower() for m in want], [m.text.lower() for m in got], autojunk=False)
    pairs = [(b.a + k, b.b + k) for b in sm.get_matching_blocks() for k in range(b.size)]
    return truth, result, want, got, pairs


@pytest.fixture(scope="module")
def light():
    return _score("light", "light.mp4")


@pytest.fixture(scope="module")
def darkgroup():
    return _score("darkgroup", "darkgroup.mov")


def test_reads_one_to_one_thread(light):
    truth, result, want, got, pairs = light
    assert result.title == "Jordan" and not result.is_group
    assert abs(len(got) - len(want)) <= 1, "each bubble kept exactly once"
    assert len(pairs) >= len(want) * 0.92, "messages read word for word, in order"
    for i, j in pairs:
        assert got[j].from_me == (want[i]["sender"] == "Me")
        assert got[j].sender == ("Me" if got[j].from_me else "Jordan")


def test_timestamps_come_from_headers(light):
    truth, result, want, got, pairs = light
    timed = [(i, j) for i, j in pairs if got[j].ts is not None]
    assert len(timed) >= len(pairs) - 2      # only bubbles above the first visible header are undated
    for i, j in timed:
        sent = datetime.fromisoformat(want[i]["at"])
        assert got[j].ts <= sent and (sent - got[j].ts).total_seconds() < 6 * 3600


def test_reads_dark_group_thread_from_hevc(darkgroup):
    truth, result, want, got, pairs = darkgroup
    assert result.title == "Family" and result.is_group
    assert len(pairs) >= len(want) * 0.9
    named = [(i, j) for i, j in pairs if want[i]["sender"] != "Me"]
    right = sum(1 for i, j in named if got[j].sender == want[i]["sender"])
    assert right >= len(named) * 0.9, "group senders come from the names above their bubbles"


def test_reads_a_messy_real_world_recording():
    """Control Center at both ends, a notification banner, photos, link previews, tapback badges,
    the typing indicator, a 3x (1179 px) HEVC recording at heavy compression."""
    truth = json.loads((FIXTURES / "messy.json").read_text(encoding="utf-8"))
    events = []
    result = read_recording(FIXTURES / "messy.mov", recorded_at=datetime.fromisoformat(truth["recorded_at"]),
                            on_event=events.append)
    want = [m["text"] if not m["text"].startswith("[") else m["text"] for m in truth["messages"]]
    got = [m.text for m in result.messages]
    sm = SequenceMatcher(None, [w.lower() for w in want], [g.lower() for g in got], autojunk=False)
    same = sum(b.size for b in sm.get_matching_blocks())
    assert len(got) == len(want), "nothing from Control Center, the banner or the typing indicator"
    assert same >= len(want) * 0.93
    assert "[Photo]" in got and any("nytimes.com" in g or "alltrails.com" in g or "teamsnap.com" in g or "zillow.com" in g
                                    for g in got), "photos and link previews kept"
    assert not any("Screen Recording" in g or "omw" in g for g in got)
    # the reader streams what it finds, for the live view
    assert any(e["type"] == "messages" and e["messages"] for e in events)
    assert any(e["type"] == "eta" for e in events)


def test_rejects_non_videos(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("hi")
    with pytest.raises(RecordingError):
        read_recording(p)
    p = tmp_path / "broken.mp4"
    p.write_bytes(b"\0" * 100)
    with pytest.raises(RecordingError):
        read_recording(p)


# ---- as an archive ----------------------------------------------------------------------------

def test_archive_round_trip(light, tmp_path, monkeypatch):
    monkeypatch.setenv("GM_DATA_DIR", str(tmp_path))
    from unbubble.archive import Archive
    from unbubble.exporters import run_export

    _, result, _, got, _ = light
    archive_id = write_archive(result, "light.mp4")
    a = Archive(archive_id)
    t = a.threads()[0]
    assert t["title"] == "Jordan" and t["message_count"] == len(got)
    msgs = list(a.iter_thread(t["id"]))
    assert [m["text"] for m in msgs] == [m.text for m in got]
    assert msgs[-1]["sender"] == ("Me" if got[-1].from_me else "Jordan") or msgs[-1]["from_me"] == got[-1].from_me
    root = run_export(a, tmp_path / "out", ["csv"], None, True, lambda f, m: None)
    a.close()
    rows = (root / "Data" / "messages.csv").read_text(encoding="utf-8-sig").splitlines()
    assert len(rows) == len(got) + 1

    # Fix the name and the recording date afterwards, without reading the video again.
    first_relative = next((m for m in got if m.header and m.header.split()[0] in ("Today", "Yesterday", "Monday",
                           "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")), None)
    update_archive(a.dir, names={"Jordan": "Jordan Lee"}, title="Jordan Lee", recorded_at="2026-10-17T09:00")
    b = Archive(archive_id)
    assert b.threads()[0]["title"] == "Jordan Lee"
    after = list(b.iter_thread(1))
    assert {m["sender"] for m in after if not m["from_me"]} == {"Jordan Lee"}
    if first_relative is not None:
        i = got.index(first_relative)
        assert after[i]["ts"] - int(first_relative.ts.timestamp() * 1000) == 86_400_000   # moved one day
    b.close()


# ---- the app commands the website and UI call ----------------------------------------------

def test_rpc_flow_with_mock_iphone(tmp_path, monkeypatch):
    monkeypatch.setenv("GM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GM_MOCK_DEVICE", "1")
    monkeypatch.setenv("GM_MOCK_RECORDING", str(FIXTURES / "light.mp4"))
    from unbubble import mock, rpc

    async def run():
        async def send(_m):
            pass

        r = rpc.Rpc(send, "test")
        videos = await r.m_recording_device_videos(mock.MOCK_UDID)
        assert videos and videos[0]["likely_screen_recording"]
        await r.m_recording_start(udid=mock.MOCK_UDID, device_path=videos[0]["path"], recorded_at="2026-10-16T21:30")
        await rpc.HUB.job
        done = rpc.HUB.last_job_event
        assert done["event"] == "recording.done", done
        info = await r.m_recording_info(done["data"]["archive_id"])
        assert info["title"] == "Jordan" and info["people"][0]["name"] == "Jordan" and info["messages"] > 20
        await r.m_recording_start(path=str(tmp_path / "missing.mp4"))
        await rpc.HUB.job
        assert rpc.HUB.last_job_event["event"] == "recording.failed"

    asyncio.run(run())


def test_dropped_video_upload_and_live_events(tmp_path, monkeypatch):
    """A video dropped on the page: one-time ticket over the approved connection, streamed to the
    loopback server, read with live progress, then deleted."""
    monkeypatch.setenv("GM_DATA_DIR", str(tmp_path))
    from aiohttp.test_utils import TestClient, TestServer

    from unbubble import rpc, server, uploads

    async def run():
        app = server.build_app()
        async with TestClient(TestServer(app, host="127.0.0.1")) as client:
            server.BOUND_PORT = client.port
            origin = f"http://127.0.0.1:{client.port}"
            sent = []

            async def send(m):
                sent.append(m)

            r = rpc.Rpc(send, "direct")
            rpc.HUB.sessions.add(r)
            data = (FIXTURES / "light.mp4").read_bytes()
            ticket = await r.m_recording_upload_ticket("RPReplay_Final.MP4", len(data))
            path = "/recording/upload?t=" + ticket["upload_id"]
            # someone else's page can't push files in
            bad = await client.put(path, data=data, headers={"Origin": "https://evil.example"})
            assert bad.status == 403
            res = await client.put(path, data=data, headers={"Origin": origin})
            assert res.status == 200 and (await res.json())["upload_id"] == ticket["upload_id"]
            again = await client.put(path, data=data, headers={"Origin": origin})
            assert again.status == 403, "tickets upload once"
            junk = await r.m_recording_upload_ticket("notes.mov", 100)
            nv = await client.put("/recording/upload?t=" + junk["upload_id"], data=b"x" * 100, headers={"Origin": origin})
            assert nv.status == 415, "only videos"

            await r.m_recording_start(upload_id=ticket["upload_id"], recorded_at="2026-10-16T21:30")
            await rpc.HUB.job
            assert rpc.HUB.last_job_event["event"] == "recording.done", rpc.HUB.last_job_event
            await asyncio.sleep(0.05)
            events = [m["data"] for m in list(r._queue._queue) if m.get("event") == "recording.event"] if hasattr(r, "_queue") else []
            assert uploads.finished(ticket["upload_id"]) is None, "the upload is deleted once read"
            info = await r.m_recording_info(rpc.HUB.last_job_event["data"]["archive_id"])
            assert info["video"] == "RPReplay_Final.MP4" and info["preview"]
            rpc.HUB.sessions.discard(r)
            return events

    asyncio.run(run())


def test_review_edits(light, tmp_path, monkeypatch):
    monkeypatch.setenv("GM_DATA_DIR", str(tmp_path))
    from unbubble import rpc
    from unbubble.archive import Archive

    _, result, _, got, _ = light
    archive_id = write_archive(result, "light.mp4")

    async def run():
        async def send(_m):
            pass
        r = rpc.Rpc(send, "test")
        items = await r.m_recording_review(archive_id)
        assert items, "emoji and unclear bubbles come back for a glance"
        assert all(i["image"] for i in items), "each with how it looked"
        first = items[0]["index"]
        await r.m_recording_update(archive_id, edits={str(first): "😂😂", "0": None})
        msgs = list(Archive(archive_id).iter_thread(1))
        assert len(msgs) == len(got) - 1
        assert msgs[first - 1]["text"] == "😂😂"
        left = await r.m_recording_review(archive_id)
        assert len(left) == len(items) - 1 - (1 if items and items[0]["index"] == 0 else 0)

    asyncio.run(run())
