"""Developer mock iPhone (GM_MOCK_DEVICE=1). Lets contributors work on the UI without hardware.

Set GM_MOCK_FIXTURE to a folder laid out like a capture (see tests/make_fixture.py). The mock
walks through the real capture phases with realistic pacing, then imports the fixture with the
real normalize pipeline.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from .archive import Archive
from .capture import Emit
from .contacts import ContactBook
from .normalize import normalize

MOCK_UDID = "00008140-MOCK0000DEV1"


def enabled() -> bool:
    return os.environ.get("GM_MOCK_DEVICE") == "1"


# GM_MOCK_PLATFORM=windows GM_MOCK_DRIVER_MISSING=1 simulates a PC without Apple's driver, so the
# one-click driver install can be developed and tested without Windows.
_driver_installed = os.environ.get("GM_MOCK_DRIVER_MISSING") != "1"


def platform_override() -> str | None:
    return os.environ.get("GM_MOCK_PLATFORM") if enabled() else None


def driver_status() -> dict:
    return {"installed": _driver_installed, "running": _driver_installed, "winget": True}


async def install_driver(progress) -> dict:
    global _driver_installed
    progress("installing", "Installing Apple's iPhone driver. Choose Yes if Windows asks.")
    await asyncio.sleep(2.5)
    progress("starting", "Starting the driver")
    await asyncio.sleep(1)
    _driver_installed = True
    return {"ok": True}


def devices(synced: bool = False) -> list[dict]:
    if not _driver_installed:
        from .capture import CaptureError

        raise CaptureError("usbmux_unavailable", "Apple device service is not running", hint="windows_drivers")
    return [{"udid": MOCK_UDID, "connection": "USB", "name": "Layne's iPhone", "model": "iPhone 17 Pro",
             "ios": "26.0.1", "paired": True, "state": "ready"}]


async def capture(emit: Emit) -> str:
    fixture = Path(os.environ["GM_MOCK_FIXTURE"])
    loop = asyncio.get_running_loop()
    emit("capture.phase", {"phase": "trust", "message": "Unlock your iPhone and tap Trust if asked"})
    await asyncio.sleep(1.2)
    emit("capture.phase", {"phase": "stream", "incremental": False,
                           "message": "Reading messages from your iPhone. The first export takes longest; later ones only pull what's new."})
    for i in range(1, 41):
        await asyncio.sleep(0.12)
        emit("capture.progress", {"phase": "stream", "fraction": round(i / 40 * 0.8, 4)})
    emit("capture.phase", {"phase": "unpack", "message": "Unpacking new messages"})
    await asyncio.sleep(0.6)
    meta = {"device_name": "Layne's iPhone", "model": "iPhone 17 Pro", "ios": "26.0.1", "udid": MOCK_UDID}
    directory = Archive.create("iphone", "Layne's iPhone", meta)
    emit("capture.phase", {"phase": "rebuild", "message": "Rebuilding every conversation"})
    media = fixture / "MediaDomain"

    def resolve(name: str):
        return media / name.removeprefix("~/")

    def progress(f: float, m: str) -> None:
        loop.call_soon_threadsafe(emit, "capture.progress", {"phase": "rebuild", "fraction": round(0.84 + 0.16 * f, 4), "message": m})

    result = await asyncio.to_thread(
        normalize, fixture / "HomeDomain/Library/SMS/sms.db", directory / "archive.db",
        ContactBook.from_ios(fixture / "HomeDomain/Library/AddressBook/AddressBook.sqlitedb"), resolve,
        source_label="iphone", progress=progress,
    )
    Archive.finalize(directory, threads=result.threads, messages=result.messages, attachments=result.attachments,
                     missing_attachments=result.missing_attachments, first_ts=result.first_ts, last_ts=result.last_ts)
    return directory.name


# ---- screen recordings on the mock iPhone ---------------------------------------------------
# GM_MOCK_RECORDING points at a local video; the mock iPhone shows it as its newest screen recording.

def device_videos() -> list[dict]:
    path = os.environ.get("GM_MOCK_RECORDING")
    if not path or not Path(path).is_file():
        return []
    p = Path(path)
    return [{"path": "/DCIM/100APPLE/RPReplay_Final1792180000.MP4", "name": "RPReplay_Final1792180000.MP4",
             "bytes": p.stat().st_size, "modified": int(p.stat().st_mtime * 1000), "likely_screen_recording": True}]


def device_video_path(device_path: str) -> Path:
    path = os.environ.get("GM_MOCK_RECORDING")
    if not path or not device_videos() or device_path != device_videos()[0]["path"]:
        from .recording import RecordingError

        raise RecordingError("That video is no longer on the iPhone.")
    return Path(path)
