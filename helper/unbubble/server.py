"""Local server on 127.0.0.1.

* `GET  /status`      — lets thegreymirror.com detect the helper (CORS-limited to allowed origins).
* `GET  /ws`          — the direct transport for Chrome, Edge, Firefox, Brave and Arc.
* `GET  /`            — the standalone app: the same UI as thegreymirror.com/export, bundled in
                        the helper and served from this address (works offline, any browser).
* `POST /control/url` — a second launch of the helper (e.g. Windows opening an unbubble:// link)
                        hands its URL to the running instance here. Browsers can't reach it: any
                        request carrying an Origin header is refused.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

from aiohttp import WSMsgType, web

from . import PROTOCOL_VERSION, __version__
from .relay_client import handle_pair_url
from .rpc import Rpc

PORTS = (47613, 47614, 47615, 47616)
UI_DIR = Path(__file__).parent / "ui"
BOUND_PORT: Optional[int] = None
PROD_ORIGINS = {
    "https://thegreymirror.com", "https://www.thegreymirror.com",
}
DEV_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4173"}


def allowed_origins() -> set[str]:
    origins = set(PROD_ORIGINS)
    if os.environ.get("GM_DEV") == "1":
        origins |= DEV_ORIGINS
    if BOUND_PORT:  # the bundled app UI talks to its own origin
        origins |= {f"http://127.0.0.1:{BOUND_PORT}", f"http://localhost:{BOUND_PORT}"}
    extra = os.environ.get("GM_ALLOWED_ORIGINS")
    if extra:
        origins |= {o.strip().rstrip("/") for o in extra.split(",") if o.strip()}
    return origins


def _cors(request: web.Request, response: web.StreamResponse) -> web.StreamResponse:
    origin = request.headers.get("Origin")
    if origin in allowed_origins():
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Private-Network"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


async def status(request: web.Request) -> web.StreamResponse:
    if request.method == "OPTIONS":
        return _cors(request, web.Response(status=204))
    return _cors(request, web.json_response({"app": "Unbubble", "version": __version__,
                                             "protocol": PROTOCOL_VERSION}))


async def ws_handler(request: web.Request) -> web.StreamResponse:
    origin = request.headers.get("Origin")
    if origin not in allowed_origins() or request.host.split(":")[0] not in ("127.0.0.1", "localhost"):
        return web.Response(status=403, text="Origin not allowed")
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4 * 1024 * 1024)
    await ws.prepare(request)

    async def send(msg: dict) -> None:
        await ws.send_str(json.dumps(msg, separators=(",", ":")))

    rpc = Rpc(send, "direct")
    try:
        async for message in ws:
            if message.type == WSMsgType.TEXT:
                try:
                    payload = json.loads(message.data)
                except json.JSONDecodeError:
                    continue
                asyncio.create_task(rpc.handle(payload))
            elif message.type == WSMsgType.ERROR:
                break
    finally:
        rpc.close()
    return ws


async def control_url(request: web.Request) -> web.StreamResponse:
    if "Origin" in request.headers or request.headers.get("X-GM-Local") != "1":
        return web.Response(status=403)
    data = await request.json()
    ok = handle_pair_url(str(data.get("url", "")))
    return web.json_response({"ok": ok})


CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "font-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; "
    "connect-src 'self' ws://127.0.0.1:* ws://localhost:*; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)


async def ui(request: web.Request) -> web.StreamResponse:
    """Serve the bundled app UI with a strict CSP; unknown paths fall back to index.html."""
    if request.host.split(":")[0] not in ("127.0.0.1", "localhost"):
        return web.Response(status=421)  # DNS-rebinding guard
    if not UI_DIR.exists():
        return web.Response(status=404, text="The app UI isn't bundled in this build.")
    rel = request.match_info.get("path", "") or "index.html"
    target = (UI_DIR / rel).resolve()
    if UI_DIR.resolve() not in target.parents or not target.is_file():
        target = UI_DIR / "index.html"
    resp = web.FileResponse(target)
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-cache" if target.name == "index.html" else "public, max-age=31536000, immutable"
    return resp


def build_app() -> web.Application:
    app = web.Application(client_max_size=1024 * 1024)
    app.router.add_route("GET", "/status", status)
    app.router.add_route("OPTIONS", "/status", status)
    app.router.add_get("/ws", ws_handler)
    app.router.add_post("/control/url", control_url)
    app.router.add_get("/", ui)
    app.router.add_get("/{path:.*}", ui)
    return app


async def start(host: str = "127.0.0.1") -> Optional[int]:
    """Bind the first free port. Returns the port, or None if another instance already runs."""
    runner = web.AppRunner(build_app(), access_log=None)
    await runner.setup()
    for port in PORTS:
        try:
            site = web.TCPSite(runner, host, port)
            await site.start()
            global BOUND_PORT
            BOUND_PORT = port
            print(f"[server] Unbubble {__version__} listening on http://{host}:{port}", file=sys.stderr)
            return port
        except OSError:
            continue
    await runner.cleanup()
    return None


async def find_running_instance() -> Optional[int]:
    import aiohttp

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=1.5)) as http:
        for port in PORTS:
            try:
                async with http.get(f"http://127.0.0.1:{port}/status") as r:
                    if r.status == 200 and (await r.json()).get("app") == "Unbubble":
                        return port
            except Exception:
                continue
    return None


async def forward_url(port: int, url: str) -> bool:
    import aiohttp

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as http:
        async with http.post(f"http://127.0.0.1:{port}/control/url", json={"url": url},
                             headers={"X-GM-Local": "1"}) as r:
            return r.status == 200
