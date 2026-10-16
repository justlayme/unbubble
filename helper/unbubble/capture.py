"""iPhone capture over USB.

iOS only releases the Messages database through its backup service. We drive that service
ourselves and keep only Messages + Contacts as the bytes stream in; everything else (photos, app
data, …) is drained and discarded on the fly. The user never gets a backup folder and needs only
enough disk space for their messages and attachments.

The first run takes as long as the phone needs to send everything once. We keep the phone's file
index (see device_store), so every later run is incremental: the phone sends only what changed.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from . import device_store
from .archive import Archive
from .contacts import ContactBook
from .normalize import normalize

Emit = Callable[[str, dict], None]


class CaptureError(Exception):
    """An expected, user-actionable failure. `code` drives the copy shown on the website."""

    def __init__(self, code: str, message: str, **data: Any) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


def _mb():
    # Imported lazily so the helper still starts (and Mac mode still works) if the device stack
    # fails to load on an unusual system.
    from pymobiledevice3 import exceptions, lockdown, usbmux
    from pymobiledevice3.services.mobilebackup2 import BackupSelection, Mobilebackup2Service

    return exceptions, lockdown, usbmux, BackupSelection, Mobilebackup2Service


def _mux_hint() -> str:
    if sys.platform == "win32":
        return "windows_drivers"
    if sys.platform.startswith("linux"):
        return "linux_usbmuxd"
    return "restart_usbmuxd"


async def list_devices() -> list[dict]:
    exceptions, lockdown, usbmux, _, _ = _mb()
    try:
        devices = await usbmux.list_devices()
    except (exceptions.ConnectionFailedToUsbmuxdError, ConnectionRefusedError, FileNotFoundError, OSError) as exc:
        raise CaptureError("usbmux_unavailable", "Apple device service is not running", hint=_mux_hint()) from exc

    seen: dict[str, dict] = {}
    for dev in devices:
        if not dev.is_usb:
            continue
        info = {"udid": dev.serial, "connection": "USB", "name": None, "model": None, "ios": None,
                "paired": False, "state": "connected"}
        try:
            client = await asyncio.wait_for(lockdown.create_using_usbmux(serial=dev.serial, autopair=False), 6)
            try:
                values = client.all_values or {}
                info.update(
                    name=values.get("DeviceName"),
                    model=client.display_name or values.get("ProductType"),
                    ios=values.get("ProductVersion"),
                    paired=bool(client.paired),
                )
                info["state"] = "ready" if client.paired else "needs_trust"
            finally:
                await client.close()
        except Exception as exc:  # locked, untrusted, mid-boot — report rather than fail the list
            name = type(exc).__name__
            info["state"] = "locked" if "Password" in name else "needs_trust"
        seen[dev.serial] = info
    return list(seen.values())


async def capture_device(udid: str, password: str, emit: Emit) -> str:
    """Capture Messages + Contacts from `udid` and build an archive. Returns the archive id."""
    exceptions, lockdown, _usbmux, BackupSelection, Mobilebackup2Service = _mb()
    loop = asyncio.get_running_loop()

    # 1. Trust ------------------------------------------------------------------------------
    emit("capture.phase", {"phase": "trust", "message": "Unlock your iPhone and tap Trust if asked"})
    client = None
    try:
        for _attempt in range(90):  # ~3 minutes for the user to unlock and tap Trust
            try:
                client = await lockdown.create_using_usbmux(serial=udid, autopair=True, pair_timeout=5)
                break
            except exceptions.PasswordRequiredError:
                emit("capture.phase", {"phase": "trust", "message": "Unlock your iPhone to continue"})
            except exceptions.PairingDialogResponsePendingError:
                emit("capture.phase", {"phase": "trust", "message": "Tap Trust on your iPhone"})
            except exceptions.UserDeniedPairingError as exc:
                raise CaptureError("trust_denied", "The iPhone declined to trust this computer") from exc
            except exceptions.DeviceNotFoundError as exc:
                raise CaptureError("device_gone", "The iPhone was disconnected") from exc
            await asyncio.sleep(2)
        if client is None:
            raise CaptureError("trust_timeout", "Timed out waiting for the iPhone to trust this computer")

        values = client.all_values or {}
        label = values.get("DeviceName") or "iPhone"
        device_meta = {"device_name": label, "model": client.display_name, "ios": values.get("ProductVersion"), "udid": udid}

        backup = Mobilebackup2Service(client)
        encrypted = await backup.get_will_encrypt()
        if encrypted and not password:
            raise CaptureError(
                "needs_password",
                "This iPhone encrypts local backups. Enter the backup password to continue.",
            )

        rules = Mobilebackup2Service.resolve_backup_selection(["messages", "contacts"])
        keep = Mobilebackup2Service.selection_filter_callback(rules)
        incremental = device_store.has_sync_state(udid)
        device_store.write_state(udid, **device_meta)

        # 2. Stream -----------------------------------------------------------------------------
        # Everything except Messages and Contacts is drained without touching the disk. On repeat
        # runs the phone diffs against the kept manifest and sends only what changed.
        emit("capture.phase", {
            "phase": "stream", "incremental": incremental,
            "message": ("Pulling only what's new since your last export" if incremental else
                        "Reading messages from your iPhone. The first export takes longest; later ones only pull what's new.")
            + " If your iPhone asks for its passcode, enter it.",
        })
        last = [-1.0]

        def on_progress(percent: float) -> None:
            if percent - last[0] >= 0.25 or percent >= 100:
                last[0] = percent
                emit("capture.progress", {"phase": "stream", "fraction": round(min(percent, 100) / 100 * 0.8, 4)})

        async def run_backup() -> None:
            await backup.backup(
                full=False,  # the library falls back to a full run when no sync state exists
                backup_directory=device_store.backup_root(udid),
                progress_callback=on_progress,
                filter_callback=keep,
                password=password,
                patch_manifest=False,  # keep the full index so the next run can be incremental
            )

        try:
            try:
                await run_backup()
            except (exceptions.ConnectionTerminatedError, exceptions.DeviceNotFoundError):
                raise
            except asyncio.CancelledError:
                raise
            except Exception:
                if not incremental:
                    raise
                # A stale index (restored phone, changed backup password, …): start fresh once.
                device_store.reset_sync_state(udid)
                incremental = False
                emit("capture.phase", {"phase": "stream", "incremental": False,
                                       "message": "Starting a fresh read of your iPhone"})
                await run_backup()
        except (exceptions.ConnectionTerminatedError, exceptions.DeviceNotFoundError) as exc:
            raise CaptureError("device_gone", "The iPhone was disconnected during the export") from exc
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass

    # 3. Unpack only the files that arrived this run (decrypting when needed) ---------------------
    emit("capture.phase", {"phase": "unpack", "message": "Unpacking new messages"})

    def unpack_progress(fraction: float, message: str) -> None:
        loop.call_soon_threadsafe(emit, "capture.progress",
                                  {"phase": "unpack", "fraction": round(0.8 + 0.04 * fraction, 4), "message": message})

    try:
        unpack = await asyncio.to_thread(device_store.unpack_new_files, udid, password, unpack_progress)
    except Exception as exc:
        msg = str(exc).lower()
        if "password" in msg or "decrypt" in msg or "invalid tag" in msg:
            raise CaptureError("wrong_password", "That backup password didn't work") from exc
        raise CaptureError("unpack_failed", f"Couldn't unpack the capture: {exc}") from exc

    # 4. Rebuild ----------------------------------------------------------------------------------
    unpacked = device_store.unpacked_dir(udid)
    sms_db = unpacked / "HomeDomain" / "Library" / "SMS" / "sms.db"
    if not sms_db.exists():
        raise CaptureError("no_messages", "The iPhone didn't return a Messages database")
    directory = Archive.create("iphone", label, {**device_meta, "incremental": incremental, "new_files": unpack["files"]})
    try:
        snapshot = await asyncio.to_thread(_snapshot_sqlite, sms_db, directory / "source.db")
        contacts = ContactBook.from_ios(unpacked / "HomeDomain" / "Library" / "AddressBook" / "AddressBook.sqlitedb")

        def progress(fraction: float, message: str) -> None:
            loop.call_soon_threadsafe(emit, "capture.progress",
                                      {"phase": "rebuild", "fraction": round(0.84 + 0.16 * fraction, 4), "message": message})

        emit("capture.phase", {"phase": "rebuild", "message": "Rebuilding every conversation"})
        result = await asyncio.to_thread(
            normalize, snapshot, directory / "archive.db", contacts, device_store.resolver(udid),
            source_label="iphone", progress=progress,
        )
        snapshot.unlink(missing_ok=True)
        Archive.finalize(directory, threads=result.threads, messages=result.messages, attachments=result.attachments,
                         missing_attachments=result.missing_attachments, first_ts=result.first_ts, last_ts=result.last_ts)
        return directory.name
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def _snapshot_sqlite(src: Path, dest: Path) -> Path:
    """Consistent copy of a SQLite database (folds in any -wal file)."""
    import sqlite3

    source = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(dest)
    with target:
        source.backup(target)
    source.close()
    target.close()
    return dest
