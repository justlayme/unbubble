"""Windows shell: a notification-area (tray) icon around the background helper.

The tray menu is how people open the app, keep it starting at sign-in, or quit it. The asyncio
server runs on a background thread; pystray owns the main thread, as Windows requires.
"""

from __future__ import annotations

import asyncio
import threading
import webbrowser
from pathlib import Path
from typing import Optional

from . import windows_support as win

SITE = "https://thegreymirror.com/export"


class _Server:
    def __init__(self) -> None:
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.port: Optional[int] = None
        self.ready = threading.Event()

    def run(self, initial_url: Optional[str]) -> None:
        from .archive import Archive
        from .relay_client import handle_pair_url
        from .server import start

        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        Archive.discard_incomplete()
        self.port = self.loop.run_until_complete(start())
        if initial_url:
            handle_pair_url(initial_url)
        self.ready.set()
        self.loop.run_forever()

    def open_ui(self) -> None:
        self.ready.wait(10)
        webbrowser.open(f"http://127.0.0.1:{self.port or 47613}/")


def _icon_image():
    from PIL import Image

    path = Path(__file__).parent / "assets" / "tray.png"
    return Image.open(path)


def run_windows_app(initial_url: Optional[str], open_ui: bool) -> None:
    import pystray

    server = _Server()
    threading.Thread(target=server.run, args=(initial_url,), name="unbubble-server", daemon=True).start()
    if open_ui and not initial_url:
        threading.Thread(target=server.open_ui, daemon=True).start()
    from .util import data_dir

    choice = data_dir() / "signin_choice"
    if not choice.exists() and not win.installed_by_installer() and not win.start_at_signin():
        # First run without the installer's sign-in task: default it on, like the installer does.
        try:
            win.set_start_at_signin(True)
        except OSError:
            pass

    def toggle_signin(icon, _item):
        win.set_start_at_signin(not win.start_at_signin())
        choice.write_text("set", encoding="utf-8")  # never override the person's choice again
        icon.update_menu()

    def quit_app(icon, _item):
        icon.stop()
        if server.loop:
            server.loop.call_soon_threadsafe(server.loop.stop)

    menu = pystray.Menu(
        pystray.MenuItem("Open Unbubble", lambda *_: server.open_ui(), default=True),
        pystray.MenuItem("Open thegreymirror.com", lambda *_: webbrowser.open(SITE)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Start when I sign in", toggle_signin, checked=lambda _i: win.start_at_signin()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_app),
    )
    pystray.Icon("Unbubble", _icon_image(), "Unbubble", menu).run()
