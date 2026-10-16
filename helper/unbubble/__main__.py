"""Entry point.

    unbubble                       run the helper and open the app
    unbubble --background          run the helper only (used at login)
    unbubble unbubble://pair?... hand a link to the running helper (or start and use it)
    unbubble import-db <sms.db|chat.db> [--contacts AddressBook.sqlitedb] [--media DIR]
    unbubble export <archive-id> [--formats html,pdf,json,csv] [--out DIR]
    unbubble list
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


def _run_helper(initial_url: str | None, open_ui: bool = False) -> None:
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        # A second launch (e.g. an unbubble:// link) hands off to the running helper and exits.
        running = asyncio.run(_handoff(initial_url, open_ui))
        if running:
            return
        from .windows_app import run_windows_app

        run_windows_app(initial_url, open_ui)
        return
    asyncio.run(_serve(initial_url, open_ui))


async def _handoff(initial_url: str | None, open_ui: bool) -> bool:
    from .server import find_running_instance, forward_url

    port = await find_running_instance()
    if not port:
        return False
    if initial_url:
        await forward_url(port, initial_url)
    elif open_ui:
        open_app_ui(port)
    return True


def open_app_ui(port: int) -> None:
    import webbrowser

    webbrowser.open(f"http://127.0.0.1:{port}/")


async def _serve(initial_url: str | None, open_ui: bool = False) -> None:
    from .archive import Archive
    from .relay_client import handle_pair_url
    from .server import find_running_instance, forward_url, start

    running = await find_running_instance()
    if running:
        if initial_url:
            await forward_url(running, initial_url)
        elif open_ui:
            open_app_ui(running)
        return
    Archive.discard_incomplete()
    port = await start()
    if port is None:
        print("No free port for Unbubble", file=sys.stderr)
        return
    if initial_url:
        handle_pair_url(initial_url)
    elif open_ui:
        open_app_ui(port)
    await asyncio.Event().wait()


def _cli_import(args: argparse.Namespace) -> None:
    from .archive import Archive
    from .contacts import ContactBook
    from .normalize import normalize

    src = Path(args.db).expanduser()
    contacts = ContactBook.from_ios(Path(args.contacts)) if args.contacts else ContactBook()
    media = Path(args.media).expanduser() if args.media else None

    def resolve(filename: str):
        if media is None:
            return Path(filename).expanduser()
        rel = filename
        for prefix in ("~/", "/var/mobile/", "/private/var/mobile/"):
            if rel.startswith(prefix):
                rel = rel[len(prefix):]
        return media / rel

    directory = Archive.create("import", src.name, {"device_name": args.label or src.stem})

    def progress(f: float, msg: str) -> None:
        print(f"\r{int(f * 100):3d}%  {msg:<60}", end="", file=sys.stderr)

    result = normalize(src, directory / "archive.db", contacts, resolve, source_label="import", progress=progress)
    Archive.finalize(directory, threads=result.threads, messages=result.messages, attachments=result.attachments,
                     missing_attachments=result.missing_attachments, first_ts=result.first_ts, last_ts=result.last_ts)
    print(f"\nArchive {directory.name}: {result.threads} conversations, {result.messages} messages, "
          f"{result.attachments} attachments ({result.missing_attachments} not on device), {result.reactions} reactions")


def _cli_export(args: argparse.Namespace) -> None:
    from .archive import Archive
    from .exporters import run_export
    from .util import default_export_dir

    archive = Archive(args.archive)

    def progress(f: float, msg: str) -> None:
        print(f"\r{int(f * 100):3d}%  {msg[:70]:<70}", end="", file=sys.stderr)

    out = run_export(archive, Path(args.out).expanduser() if args.out else default_export_dir(),
                     args.formats.split(","), progress=progress)
    print(f"\nSaved to {out}")


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:  # opened by the person: show the app
        _run_helper(None, open_ui=True)
        return
    if argv[0].startswith("unbubble:"):
        _run_helper(argv[0])
        return
    if argv[0] == "--background":
        _run_helper(None)
        return

    parser = argparse.ArgumentParser(prog="unbubble")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("import-db")
    p.add_argument("db")
    p.add_argument("--contacts")
    p.add_argument("--media")
    p.add_argument("--label")
    p = sub.add_parser("export")
    p.add_argument("archive")
    p.add_argument("--formats", default="html,pdf,json,csv")
    p.add_argument("--out")
    sub.add_parser("list")
    sub.add_parser("serve")
    args = parser.parse_args(argv)

    if args.cmd == "import-db":
        _cli_import(args)
    elif args.cmd == "export":
        _cli_export(args)
    elif args.cmd == "list":
        from .archive import Archive

        for a in Archive.list_all():
            print(f"{a['id']}  {a.get('device_name') or a['label']}  {a.get('messages', 0):,} messages")
    elif args.cmd == "serve":
        asyncio.run(_serve(None))


if __name__ == "__main__":
    main()
