"""Per-iPhone sync store: what makes every export after the first one fast.

    devices/<udid>/
      backup/<udid>/Manifest.db, Manifest.plist, Status.plist, Info.plist
                                     ← the phone's full file index, kept so the next run is incremental
      backup/<udid>/xx/<hash>        ← message files received this run (moved out after unpacking)
      unpacked/HomeDomain/Library/SMS/sms.db
      unpacked/MediaDomain/Library/SMS/Attachments/…
      state.json

During the stream we keep only Messages and Contacts and drain everything else. The Manifest.db
we keep still lists every file on the phone, so on the next run the phone diffs against it and
sends only what changed since last time: new messages and their attachments, not the camera roll.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Callable, Optional

from .util import data_dir
from .windows_support import longpath

KEEP_PREFIXES = (
    ("HomeDomain", "Library/SMS/"),
    ("MediaDomain", "Library/SMS/"),
    ("HomeDomain", "Library/AddressBook/AddressBook.sqlitedb"),
)
METADATA = ("Manifest.db", "Manifest.plist", "Status.plist", "Info.plist")


def store_dir(udid: str) -> Path:
    if not udid or any(c in udid for c in "/\\.") or len(udid) > 64:
        raise ValueError("Invalid device id")
    d = data_dir() / "devices" / udid
    d.mkdir(parents=True, exist_ok=True)
    return d


def backup_root(udid: str) -> Path:
    return store_dir(udid) / "backup"


def device_dir(udid: str) -> Path:
    return backup_root(udid) / udid


def unpacked_dir(udid: str) -> Path:
    return store_dir(udid) / "unpacked"


def has_sync_state(udid: str) -> bool:
    d = device_dir(udid)
    return all((d / f).is_file() and (d / f).stat().st_size > 0 for f in ("Manifest.plist", "Manifest.db", "Status.plist"))


def read_state(udid: str) -> dict:
    try:
        return json.loads((store_dir(udid) / "state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(udid: str, **fields) -> None:
    state = read_state(udid)
    state.update(fields)
    (store_dir(udid) / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


def reset_sync_state(udid: str) -> None:
    """Forget the incremental index (forces the next run to be a first run)."""
    shutil.rmtree(backup_root(udid), ignore_errors=True)


def forget_device(udid: str) -> None:
    """Remove everything stored for this iPhone."""
    shutil.rmtree(store_dir(udid), ignore_errors=True)


def list_devices() -> list[dict]:
    root = data_dir() / "devices"
    if not root.exists():
        return []
    out = []
    for d in root.iterdir():
        if d.is_dir():
            out.append({"udid": d.name, **read_state(d.name), "synced": has_sync_state(d.name)})
    return out


def _kept(domain: str, rel: str) -> bool:
    return any(domain == d and (rel == p or rel.startswith(p)) for d, p in KEEP_PREFIXES)


def unpack_new_files(udid: str, password: str, progress: Callable[[float, str], None] = lambda f, s: None) -> dict:
    """Move (or decrypt) the message files received this run into `unpacked/`.

    Only files actually present in the backup folder are touched, so a repeat run that received
    200 new attachments does 200 file operations, not a full re-extraction.
    """
    from pyiosbackup import Backup

    src_root = device_dir(udid)
    dest_root = unpacked_dir(udid)
    backup = Backup.from_path(src_root, password)
    encrypted = backup.keybag is not None
    manifest_path = Path(backup._manifest_db.path)  # decrypted copy when the backup is encrypted
    con = sqlite3.connect(f"file:{manifest_path.as_posix()}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT fileID, domain, relativePath FROM Files WHERE flags = 1 AND domain IN ('HomeDomain', 'MediaDomain')"
        " AND (relativePath LIKE 'Library/SMS/%' OR relativePath LIKE 'Library/AddressBook/AddressBook.sqlitedb%')"
    ).fetchall()
    con.close()

    present = [(fid, dom, rel) for fid, dom, rel in rows if _kept(dom, rel) and (src_root / fid[:2] / fid).is_file()]
    moved = 0
    for i, (fid, domain, rel) in enumerate(present):
        if i % 100 == 0:
            progress(i / max(len(present), 1), f"Unpacking new files ({i:,} of {len(present):,})")
        src = src_root / fid[:2] / fid
        dest = dest_root / domain / rel
        os.makedirs(longpath(dest.parent), exist_ok=True)
        if encrypted:
            entry = backup.get_entry_by_id(fid)
            tmp = dest.with_name(dest.name + ".part")
            with open(longpath(tmp), "wb") as fh:
                fh.write(entry.read_bytes())
            os.replace(longpath(tmp), longpath(dest))
            os.unlink(longpath(src))
        else:
            os.replace(longpath(src), longpath(dest))
        moved += 1
    # The hash-prefix folders are now empty; tidy them so the store stays small and readable.
    for sub in src_root.iterdir():
        if sub.is_dir() and len(sub.name) == 2:
            try:
                sub.rmdir()
            except OSError:
                pass
    progress(1.0, "Unpacked")
    write_state(udid, last_sync=int(time.time() * 1000), encrypted=encrypted)
    return {"files": moved, "encrypted": encrypted}


def resolver(udid: str) -> Callable[[str], Optional[Path]]:
    media = unpacked_dir(udid) / "MediaDomain"

    def resolve(filename: str) -> Optional[Path]:
        rel = filename
        for prefix in ("~/", "/var/mobile/", "/private/var/mobile/"):
            if rel.startswith(prefix):
                rel = rel[len(prefix):]
                break
        return media / rel

    return resolve
