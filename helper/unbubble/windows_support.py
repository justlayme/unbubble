"""Everything Windows-specific: Apple's iPhone driver, real folders, file dialogs, start at sign-in.

Windows can only talk to iPhones through Apple Mobile Device Service. Apple publishes it on its own
as the `Apple.AppleMobileDeviceSupport` winget package (the driver without iTunes), so the helper
can install it in one click: winget shows the Windows admin prompt and does the rest.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

IS_WINDOWS = sys.platform == "win32"
SERVICE = "Apple Mobile Device Service"
WINGET_ID = "Apple.AppleMobileDeviceSupport"
STORE_FALLBACK = "ms-windows-store://pdp/?productid=9NP83LWLPZ9K"  # Apple Devices, includes the same driver
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Unbubble"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def _run(args: list[str], timeout: float = 15) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          creationflags=NO_WINDOW if IS_WINDOWS else 0)


# ---- Apple driver -----------------------------------------------------------------------------

def driver_status() -> dict:
    """`installed` and `running` for Apple Mobile Device Service; always ready off Windows."""
    if not IS_WINDOWS:
        return {"installed": True, "running": True, "winget": False}
    try:
        out = _run(["sc", "query", SERVICE]).stdout
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    installed = "SERVICE_NAME" in out
    return {"installed": installed, "running": "RUNNING" in out, "winget": winget_available()}


def winget_available() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return _run(["winget", "--version"]).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


async def install_driver(progress: Callable[[str, str], None]) -> dict:
    """Install Apple's driver with winget. Windows shows its admin prompt; we wait for the service."""
    if not IS_WINDOWS:
        return {"ok": True}
    if not winget_available():
        os.startfile(STORE_FALLBACK)  # type: ignore[attr-defined]
        return {"ok": False, "fallback": "store"}
    progress("installing", "Installing Apple's iPhone driver. Choose Yes if Windows asks.")
    proc = await asyncio.create_subprocess_exec(
        "winget", "install", "--exact", "--id", WINGET_ID, "--silent",
        "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, creationflags=NO_WINDOW,
    )
    output, _ = await proc.communicate()
    text = output.decode(errors="replace")
    if proc.returncode != 0 and "already installed" not in text.lower():
        if "cancel" in text.lower() or "denied" in text.lower():
            return {"ok": False, "reason": "declined"}
        return {"ok": False, "reason": "failed", "detail": text[-400:]}
    progress("starting", "Starting the driver")
    for _ in range(30):
        status = driver_status()
        if status["running"]:
            return {"ok": True}
        if status["installed"]:
            _run(["sc", "start", SERVICE])
        await asyncio.sleep(1)
    return {"ok": False, "reason": "not_running"}


# ---- folders ------------------------------------------------------------------------------------

def documents_dir() -> Path:
    """The real Documents folder (respects OneDrive redirection and moved libraries)."""
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8)]

        documents = GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
        path = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(documents), 0, None, ctypes.byref(path)) == 0:
            try:
                return Path(path.value)
            finally:
                ctypes.windll.ole32.CoTaskMemFree(path)
    docs = Path.home() / "Documents"
    return docs if docs.exists() else Path.home()


def is_cloud_synced(path: Path) -> bool:
    """True when a folder is inside OneDrive (and so would upload private messages to the cloud)."""
    roots = [os.environ.get(k) for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")]
    p = str(path.resolve()).lower()
    return any(r and p.startswith(str(Path(r).resolve()).lower()) for r in roots) or "\\onedrive" in p


def choose_folder(initial: Path) -> Optional[str]:
    """Native folder picker. Returns None if the person cancels."""
    try:
        if IS_WINDOWS:
            script = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
                "$d.Description = 'Choose where to save your messages';"
                "$d.UseDescriptionForTitle = $true;"
                f"$d.SelectedPath = '{str(initial).replace(chr(39), chr(39) * 2)}';"
                "$f = New-Object System.Windows.Forms.Form -Property @{TopMost=$true; ShowInTaskbar=$false};"
                "if ($d.ShowDialog($f) -eq 'OK') { [Console]::Out.Write($d.SelectedPath) }"
            )
            r = _run(["powershell", "-NoProfile", "-STA", "-Command", script], timeout=600)
        elif sys.platform == "darwin":
            r = _run(["osascript", "-e", 'POSIX path of (choose folder with prompt "Choose where to save your messages")'],
                     timeout=600)
        else:
            r = _run(["zenity", "--file-selection", "--directory", f"--filename={initial}/"], timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return None
    chosen = r.stdout.strip()
    return chosen or None


def reveal(path: Path) -> None:
    if IS_WINDOWS:
        subprocess.Popen(["explorer", str(path)], creationflags=NO_WINDOW)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


# ---- start at sign-in ---------------------------------------------------------------------------

def _exe() -> str:
    return sys.executable if getattr(sys, "frozen", False) else f'"{sys.executable}" -m unbubble'


def start_at_signin() -> bool:
    if not IS_WINDOWS:
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
            return True
    except OSError:
        return False


def set_start_at_signin(on: bool) -> None:
    if not IS_WINDOWS:
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            exe = _exe()
            value = f'"{exe}" --background' if getattr(sys, "frozen", False) else f"{exe} --background"
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, value)
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except OSError:
                pass


def installed_by_installer() -> bool:
    """The installer records itself here; it already asked about starting at sign-in."""
    if not IS_WINDOWS:
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Unbubble"):
            return True
    except OSError:
        return False


def longpath(path: Path) -> str:
    """Extended-length form on Windows, so deep attachment paths don't hit the 260-character limit."""
    p = str(Path(path).absolute())
    if IS_WINDOWS and not p.startswith("\\\\?\\"):
        return "\\\\?\\UNC\\" + p[2:] if p.startswith("\\\\") else "\\\\?\\" + p
    return p
