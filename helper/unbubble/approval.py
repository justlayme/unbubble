"""Ask the person at the computer before a new browser may pair over the relay.

A malicious page could fire a `unbubble://pair` link pointing the helper at a room the attacker
controls. So the first pairing from any browser key needs a native confirmation showing a short
code that must match the one on thegreymirror.com. Approved browser keys are remembered.

Direct connections (Chrome, Edge, Firefox, Brave) from the website get the same kind of gate:
the first one after the helper starts needs an Allow on the computer (see `allow_direct`), so a
page cannot read anyone's messages without the person at the computer saying yes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .util import data_dir

_TRUST_FILE = "trusted_browsers.json"


def _trust_path() -> Path:
    return data_dir() / _TRUST_FILE


def _fingerprint(browser_pk: bytes) -> str:
    return hashlib.sha256(browser_pk).hexdigest()


def is_trusted(browser_pk: bytes) -> bool:
    try:
        return _fingerprint(browser_pk) in json.loads(_trust_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False


def remember(browser_pk: bytes) -> None:
    path = _trust_path()
    try:
        trusted = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        trusted = []
    fp = _fingerprint(browser_pk)
    if fp not in trusted:
        trusted.append(fp)
        path.write_text(json.dumps(trusted[-50:]), encoding="utf-8")


def _ask_blocking(code: str) -> bool:
    return _dialog(
        f"thegreymirror.com wants to connect to Unbubble.\n\n"
        f"Only allow this if the website is showing the code {code}."
    )


def _dialog(text: str) -> bool:
    title = "Unbubble"
    if os.environ.get("GM_AUTO_APPROVE") == "1":
        return True
    if sys.platform == "darwin":
        script = (
            f'display dialog {json.dumps(text)} with title {json.dumps(title)} '
            f'buttons {{"Don\'t Allow", "Allow"}} default button "Allow" cancel button "Don\'t Allow" '
            f'with icon caution giving up after 120'
        )
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        return r.returncode == 0 and "button returned:Allow" in r.stdout and "gave up:true" not in r.stdout
    if sys.platform == "win32":
        import ctypes

        MB_YESNO, MB_ICONWARNING, MB_TOPMOST, MB_SETFOREGROUND, IDYES = 0x4, 0x30, 0x40000, 0x10000, 6
        flags = MB_YESNO | MB_ICONWARNING | MB_TOPMOST | MB_SETFOREGROUND
        return ctypes.windll.user32.MessageBoxW(None, text + "\n\nAllow?", title, flags) == IDYES
    for cmd in (["zenity", "--question", "--title", title, "--text", text],
                ["kdialog", "--title", title, "--yesno", text]):
        try:
            return subprocess.run(cmd).returncode == 0
        except FileNotFoundError:
            continue
    return False


async def ask(code: str) -> bool:
    return await asyncio.to_thread(_ask_blocking, code)


# Direct connections: one Allow per run of the helper. A refusal holds for a minute so a page
# cannot reopen the dialog in a loop; after that the person can be asked again.
_direct_lock = asyncio.Lock()
_direct_allowed: set[str] = set()
_direct_refused: dict[str, float] = {}
REFUSAL_HOLD = 60.0


def _direct_text(origin: str) -> str:
    site = origin.split("://", 1)[-1]
    return (
        f"{site} wants to open your messages in Unbubble.\n\n"
        f"Allow only if you are using {site} right now. Unbubble asks again the next time it starts."
    )


async def allow_direct(origin: str) -> bool:
    """True once the person at the computer has allowed `origin` in this run of the helper."""
    async with _direct_lock:  # several sockets at once show one dialog, not a stack of them
        if origin in _direct_allowed:
            return True
        if time.monotonic() - _direct_refused.get(origin, -REFUSAL_HOLD) < REFUSAL_HOLD:
            return False
        if await asyncio.to_thread(_dialog, _direct_text(origin)):
            _direct_allowed.add(origin)
            _direct_refused.pop(origin, None)
            return True
        _direct_refused[origin] = time.monotonic()
        return False


def forget_direct() -> None:
    """Withdraw every direct-connection approval (tests, and the tray's sign-out)."""
    global _direct_lock
    _direct_allowed.clear()
    _direct_refused.clear()
    _direct_lock = asyncio.Lock()  # a fresh lock, in case the old one belonged to another loop
