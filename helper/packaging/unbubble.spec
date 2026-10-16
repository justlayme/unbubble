# PyInstaller spec for Unbubble.   pyinstaller packaging/unbubble.spec
# Builds the windowed (no console) Windows app folder that the Inno Setup installer packages.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
PKG = ROOT / "unbubble"
VERSION = "1.0.0"

# Only the parts of pymobiledevice3 that capture uses. Collecting the whole package drags in screen
# recording (PyAV/ffmpeg) and instruments analytics (scipy, pandas): ~300 MB the helper never runs.
hidden = [
    "pymobiledevice3.lockdown", "pymobiledevice3.usbmux", "pymobiledevice3.exceptions",
    "pymobiledevice3.services.mobilebackup2", "pymobiledevice3.services.device_link",
    "pymobiledevice3.services.afc", "pymobiledevice3.services.notification_proxy",
] + collect_submodules("pyiosbackup") + collect_submodules("typedstream")
if sys.platform == "win32":
    hidden += ["pystray._win32"]

datas = [
    (str(PKG / "fonts"), "unbubble/fonts"),
    (str(PKG / "assets"), "unbubble/assets"),
    (str(PKG / "exporters" / "viewer"), "unbubble/exporters/viewer"),
    (str(PKG / "ui"), "unbubble/ui"),
] + collect_data_files("pymobiledevice3", excludes=["**/*.dylib", "**/resources/dsc*"])

a = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "av", "scipy", "pandas", "numpy", "cv2",
              "pymobiledevice3.services.dvt", "pymobiledevice3.services.screenshot", "pymobiledevice3.cli"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Unbubble",
    console=False,
    icon=str(ROOT / "packaging" / "icon.ico"),
    version=str(ROOT / "packaging" / "windows" / "version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="Unbubble")
