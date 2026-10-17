"""The command set the website can call, independent of how the bytes travel.

Messages are JSON: requests `{"id", "method", "params"}`, replies `{"id", "result"}` or
`{"id", "error": {"code", "message", ...}}`, and push events `{"event", "data"}`. The same `Rpc`
object is driven by the direct localhost WebSocket and by the encrypted relay.
"""

from __future__ import annotations

import asyncio
import base64
import os
import platform
import sys
import traceback
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from . import PROTOCOL_VERSION, __version__, device_store
from .archive import Archive
from .capture import CaptureError, capture_device, list_devices
from .recording import RecordingError
from . import mock
from . import windows_support as win
from .media import browser_image, guess_mime
from .util import default_export_dir

Send = Callable[[dict], Awaitable[None]]


class Hub:
    """Process-wide state shared by every connected page: one capture or export at a time."""

    def __init__(self) -> None:
        self.sessions: set["Rpc"] = set()
        self.job: Optional[asyncio.Task] = None
        self.job_kind: Optional[str] = None
        self.last_job_event: Optional[dict] = None
        self.archives: dict[str, Archive] = {}
        self.exported: set[str] = set()

    def archive(self, archive_id: str) -> Archive:
        a = self.archives.get(archive_id)
        if a is None:
            a = Archive(archive_id)
            self.archives[archive_id] = a
        return a

    def broadcast(self, event: str, data: dict) -> None:
        msg = {"event": event, "data": data}
        if event.startswith(("capture.", "export.", "recording.")):
            self.last_job_event = msg
        for s in list(self.sessions):
            s.push(msg)


HUB = Hub()


class RpcError(Exception):
    def __init__(self, code: str, message: str, **data: Any) -> None:
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


class Rpc:
    def __init__(self, send: Send, transport: str) -> None:
        self._send = send
        self.transport = transport
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=2000)
        self._pump = asyncio.create_task(self._drain())
        HUB.sessions.add(self)

    def close(self) -> None:
        HUB.sessions.discard(self)
        self._pump.cancel()

    def push(self, msg: dict) -> None:
        try:
            self._queue.put_nowait(msg)
        except asyncio.QueueFull:
            pass  # a stalled page should not stall the capture

    async def _drain(self) -> None:
        while True:
            msg = await self._queue.get()
            try:
                await self._send(msg)
            except Exception:
                return

    async def handle(self, msg: dict) -> None:
        mid = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}
        handler = getattr(self, "m_" + method.replace(".", "_"), None)
        try:
            if handler is None:
                raise RpcError("unknown_method", f"Unknown method {method}")
            result = await handler(**params)
            self.push({"id": mid, "result": result})
        except RpcError as e:
            self.push({"id": mid, "error": {"code": e.code, "message": e.message, **e.data}})
        except CaptureError as e:
            self.push({"id": mid, "error": {"code": e.code, "message": str(e), **e.data}})
        except TypeError as e:
            self.push({"id": mid, "error": {"code": "bad_params", "message": str(e)}})
        except FileNotFoundError as e:
            self.push({"id": mid, "error": {"code": "not_found", "message": str(e)}})
        except Exception as e:  # never crash the helper on one bad call
            traceback.print_exc()
            self.push({"id": mid, "error": {"code": "internal", "message": str(e)}})

    # ---- handshake ------------------------------------------------------------------------

    async def m_hello(self, client: str = "web", **_: Any) -> dict:
        return {
            "app": "Unbubble",
            "version": __version__,
            "protocol": PROTOCOL_VERSION,
            "platform": mock.platform_override() or {"darwin": "mac", "win32": "windows"}.get(sys.platform, "linux"),
            "os_version": platform.release(),
            "transport": self.transport,
            "drivers": mock.driver_status() if mock.enabled() else await asyncio.to_thread(win.driver_status),
            "export_dir": str(default_export_dir()),
            "job": {"kind": HUB.job_kind, "last": HUB.last_job_event} if HUB.job and not HUB.job.done() else None,
        }

    # ---- devices --------------------------------------------------------------------------

    async def m_devices_list(self) -> list[dict]:
        devices = mock.devices() if mock.enabled() else await list_devices()
        known = {d["udid"]: d for d in device_store.list_devices()}
        for d in devices:
            k = known.get(d["udid"], {})
            d["synced"] = bool(k.get("synced"))
            d["last_sync"] = k.get("last_sync")
        return devices

    async def m_devices_known(self) -> list[dict]:
        return device_store.list_devices()

    async def m_devices_forget(self, udid: str) -> dict:
        if HUB.job and not HUB.job.done():
            raise RpcError("busy", "Wait for the current export to finish")
        await asyncio.to_thread(device_store.forget_device, udid)
        return {"forgotten": True}

    # ---- capture --------------------------------------------------------------------------

    def _start_job(self, kind: str, coro: Awaitable[str]) -> None:
        if HUB.job and not HUB.job.done():
            raise RpcError("busy", f"A {HUB.job_kind} is already running")

        async def runner() -> None:
            try:
                archive_id = await coro
                HUB.broadcast(f"{kind}.done", {"archive_id": archive_id, "summary": HUB.archive(archive_id).summary()})
            except asyncio.CancelledError:
                HUB.broadcast(f"{kind}.cancelled", {})
                raise
            except CaptureError as e:
                HUB.broadcast(f"{kind}.failed", {"code": e.code, "message": str(e), **e.data})
            except RecordingError as e:
                HUB.broadcast(f"{kind}.failed", {"code": "recording", "message": str(e)})
            except Exception as e:
                traceback.print_exc()
                HUB.broadcast(f"{kind}.failed", {"code": "internal", "message": str(e)})

        HUB.job_kind = kind
        HUB.job = asyncio.create_task(runner())

    async def m_capture_start(self, udid: str, password: str = "") -> dict:
        if mock.enabled() and udid == mock.MOCK_UDID:
            self._start_job("capture", mock.capture(HUB.broadcast))
            return {"started": True}
        self._start_job("capture", capture_device(udid, password, HUB.broadcast))
        return {"started": True}

    async def m_job_cancel(self) -> dict:
        if HUB.job and not HUB.job.done():
            HUB.job.cancel()
            return {"cancelled": True}
        return {"cancelled": False}

    # ---- screen recordings ------------------------------------------------------------------
    # Someone records their Messages thread on the iPhone while scrolling; the app reads the video
    # on this PC (nothing leaves it) and saves the conversation like any other export.

    async def m_recording_choose(self) -> dict:
        chosen = await asyncio.to_thread(win.choose_video, win.videos_dir())
        if not chosen:
            return {"path": None}
        p = Path(chosen)
        return {"path": str(p), "name": p.name, "bytes": p.stat().st_size if p.exists() else 0}

    async def m_recording_device_videos(self, udid: str) -> list[dict]:
        from .recording import list_device_videos

        if mock.enabled() and udid == mock.MOCK_UDID:
            return mock.device_videos()
        try:
            return await list_device_videos(udid)
        except RecordingError:
            raise
        except Exception as e:
            raise RpcError("device", "Couldn't read the iPhone's videos. Unlock it and make sure it trusts this PC.",
                           detail=type(e).__name__) from e

    async def m_recording_start(
        self, path: Optional[str] = None, udid: Optional[str] = None, device_path: Optional[str] = None,
        recorded_at: Optional[str] = None,
    ) -> dict:
        import threading
        from datetime import datetime

        from .recording import pull_device_video, read_recording, recording_created_at, write_archive
        from .util import data_dir

        if not path and not (udid and device_path):
            raise RpcError("bad_request", "Choose a screen recording first")
        loop = asyncio.get_running_loop()
        stop = threading.Event()

        def progress(fraction: float, message: str) -> None:
            loop.call_soon_threadsafe(HUB.broadcast, "recording.progress",
                                      {"fraction": round(fraction, 4), "message": message})

        async def job() -> str:
            temp: Optional[Path] = None
            try:
                if path:
                    video = Path(path)
                    if not video.is_file():
                        raise RecordingError("That video is no longer there.")
                elif mock.enabled() and udid == mock.MOCK_UDID:
                    video = mock.device_video_path(device_path)
                else:
                    name = Path(device_path).name
                    temp = data_dir() / "tmp" / f"rec-{os.getpid()}-{name}"
                    await pull_device_video(udid, device_path, temp,
                                            lambda f, m: progress(f * 0.15, m))
                    video = temp
                when = datetime.fromisoformat(recorded_at) if recorded_at else None
                if when is None and temp is not None:
                    when = recording_created_at(temp)

                def scaled(f: float, m: str) -> None:
                    progress((0.15 + 0.85 * f) if temp else f, m)

                result = await asyncio.to_thread(read_recording, video, progress=scaled, cancel=stop, recorded_at=when)
                return await asyncio.to_thread(write_archive, result, Path(device_path or path).name)
            except asyncio.CancelledError:
                stop.set()
                raise
            finally:
                if temp is not None:
                    temp.unlink(missing_ok=True)

        self._start_job("recording", job())
        return {"started": True}

    async def m_recording_info(self, archive_id: str) -> dict:
        """What the confirm screen shows: the people found, the recording date, anything unsure."""
        import json

        a = HUB.archive(archive_id)
        side = a.dir / "recording.json"
        if not side.exists():
            raise RpcError("not_found", "This conversation wasn't made from a screen recording")
        data = json.loads(side.read_text(encoding="utf-8"))
        senders: dict[str, int] = {}
        for m in data["messages"]:
            if not m["from_me"]:
                key = data["title"] if not data["is_group"] else m["sender"]
                senders[key] = senders.get(key, 0) + 1
        unsure = sum(1 for m in data["messages"] if m["text"].startswith("[") or m["confidence"] < 0.6)
        return {
            "title": data.get("title_override") or data["title"], "is_group": data["is_group"],
            "recorded_at": data["recorded_at"], "video": data["video"], "names": data.get("names", {}),
            "people": [{"name": k, "messages": v} for k, v in sorted(senders.items(), key=lambda kv: -kv[1])],
            "messages": len(data["messages"]), "unsure": unsure,
            "warnings": a.manifest.get("warnings", []),
        }

    async def m_recording_update(
        self, archive_id: str, recorded_at: Optional[str] = None, names: Optional[dict] = None,
        title: Optional[str] = None,
    ) -> dict:
        from .recording import update_archive

        if HUB.job and not HUB.job.done():
            raise RpcError("busy", f"Wait for the current {HUB.job_kind} to finish")
        a = HUB.archives.pop(archive_id, None) or Archive(archive_id)
        a.close()
        try:
            await asyncio.to_thread(update_archive, a.dir, recorded_at=recorded_at, names=names, title=title)
        except RecordingError as e:
            raise RpcError("recording", str(e)) from e
        return {"summary": HUB.archive(archive_id).summary()}

    # ---- Windows: Apple's iPhone driver --------------------------------------------------------

    async def m_drivers_status(self) -> dict:
        if mock.enabled():
            return mock.driver_status()
        return await asyncio.to_thread(win.driver_status)

    async def m_drivers_install(self) -> dict:
        if HUB.job and not HUB.job.done():
            raise RpcError("busy", "Wait for the current export to finish")
        installer = mock.install_driver if mock.enabled() else win.install_driver
        return await installer(lambda phase, msg: HUB.broadcast("drivers.progress", {"phase": phase, "message": msg}))

    # ---- archives -------------------------------------------------------------------------

    async def m_archives_list(self) -> list[dict]:
        return Archive.list_all()

    async def m_archive_summary(self, archive_id: str) -> dict:
        return await asyncio.to_thread(lambda: HUB.archive(archive_id).summary())

    async def m_archive_threads(self, archive_id: str, query: str = "") -> list[dict]:
        return await asyncio.to_thread(lambda: HUB.archive(archive_id).threads(query))

    async def m_archive_messages(self, archive_id: str, thread_id: int, before: Optional[list] = None, limit: int = 200) -> dict:
        cursor = (int(before[0]), int(before[1])) if before else None
        return await asyncio.to_thread(lambda: HUB.archive(archive_id).messages(int(thread_id), cursor, limit))

    async def m_archive_locate(self, archive_id: str, thread_id: int, message_id: int) -> dict:
        return await asyncio.to_thread(lambda: HUB.archive(archive_id).locate(int(thread_id), int(message_id)))

    async def m_archive_search(self, archive_id: str, query: str, thread_id: Optional[int] = None) -> list[dict]:
        return await asyncio.to_thread(lambda: HUB.archive(archive_id).search(query, thread_id))

    async def m_archive_delete(self, archive_id: str) -> dict:
        a = HUB.archives.pop(archive_id, None) or Archive(archive_id)
        await asyncio.to_thread(a.delete)
        return {"deleted": True}

    async def m_archive_thread_dump(self, archive_id: str, thread_id: int) -> dict:
        """The whole conversation as JSON — what thegreymirror.com analyses when the user chooses to."""
        a = HUB.archive(archive_id)
        thread = next((t for t in await asyncio.to_thread(a.threads) if t["id"] == int(thread_id)), None)
        if thread is None:
            raise RpcError("not_found", "No such conversation")
        messages = await asyncio.to_thread(lambda: list(a.iter_thread(int(thread_id))))
        return {"thread": thread, "messages": messages}

    async def m_attachment_get(self, archive_id: str, attachment_id: int, size: str = "preview") -> dict:
        """Return an attachment as base64. Images are converted to JPEG (HEIC included) and capped
        at 1600px for `preview` or 480px for `thumb`, so they render in every browser."""
        a = HUB.archive(archive_id)
        found = await asyncio.to_thread(a.attachment_path, int(attachment_id))
        if not found:
            raise RpcError("not_found", "Attachment isn't stored on this device")
        path, mime, name = found
        edge = 480 if size == "thumb" else 1600
        img = await asyncio.to_thread(browser_image, path, mime, a.dir / "cache" / "web", edge)
        if img:
            data, out_mime = img[0].read_bytes(), img[1]
        else:
            if path.stat().st_size > 25 * 1024 * 1024:
                raise RpcError("too_large", "Too large to preview here; it's included in exports", bytes=path.stat().st_size)
            data, out_mime = path.read_bytes(), guess_mime(path, mime)
        return {"mime": out_mime, "name": name, "data": base64.b64encode(data).decode("ascii")}

    # ---- export ---------------------------------------------------------------------------

    async def m_export_start(
        self, archive_id: str, formats: list[str], thread_ids: Optional[list[int]] = None,
        include_attachments: bool = True, destination: Optional[str] = None,
    ) -> dict:
        from .exporters import run_export

        archive = HUB.archive(archive_id)
        dest = Path(destination).expanduser() if destination else default_export_dir()
        loop = asyncio.get_running_loop()

        def progress(fraction: float, message: str) -> None:
            loop.call_soon_threadsafe(HUB.broadcast, "export.progress", {"fraction": round(fraction, 4), "message": message})

        async def job() -> str:
            # Exporters use their own connection so the UI can keep browsing while they run.
            worker = Archive(archive_id)
            try:
                root = await asyncio.to_thread(run_export, worker, dest, formats, thread_ids, include_attachments, progress)
            finally:
                worker.close()
            HUB.exported.add(str(root.resolve()))
            HUB.broadcast("export.saved", {"path": str(root)})
            return archive_id

        self._start_job("export", job())
        return {"started": True, "destination": str(dest)}

    async def m_export_choose_folder(self, initial: Optional[str] = None) -> dict:
        start = Path(initial).expanduser() if initial else default_export_dir().parent
        chosen = await asyncio.to_thread(win.choose_folder, start)
        if not chosen:
            return {"path": None}
        return {"path": chosen, "cloud_synced": win.is_cloud_synced(Path(chosen))}

    async def m_export_check_folder(self, path: str) -> dict:
        p = Path(path).expanduser()
        return {"path": str(p), "cloud_synced": win.is_cloud_synced(p), "exists": p.exists()}

    async def m_export_reveal(self, path: str) -> dict:
        target = Path(path)
        if str(target.resolve()) not in HUB.exported or not target.exists():
            raise RpcError("not_found", "That export folder no longer exists")
        win.reveal(target)
        return {"ok": True}
