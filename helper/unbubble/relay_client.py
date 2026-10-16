"""End-to-end encrypted channel through relay.thegreymirror.com (used by Safari).

Safari refuses to let HTTPS pages talk to localhost, so the page and the helper meet in a relay
room instead. The relay only forwards opaque frames:

* The page makes an ECDH P-256 key pair and opens `unbubble://pair?room=…&pk=…`. Its public key
  reaches the helper locally through the OS, never through the relay.
* The helper makes its own key pair and sends its public key through the relay.
* Both sides derive AES-256-GCM keys with HKDF-SHA256(ECDH secret, salt=room, info=v1), one per
  direction. Nonces are a direction byte plus a 64-bit counter; replays and reordering are rejected.
* Both sides show a short code derived from the two public keys, so the person can confirm
  they're connected to their own browser.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import struct
from typing import Optional
from urllib.parse import urlparse

import aiohttp
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import approval
from .rpc import Rpc

INFO = b"unbubble-relay-v1"
MAX_FRAME = 512 * 1024
DEFAULT_RELAY_HOSTS = {"relay.thegreymirror.com"}
DIR_HELPER_TO_WEB = 1
DIR_WEB_TO_HELPER = 2


def b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def allowed_relay(url: str) -> bool:
    hosts = set(DEFAULT_RELAY_HOSTS)
    extra = os.environ.get("GM_RELAY_HOSTS")
    if extra:
        hosts |= {h.strip() for h in extra.split(",") if h.strip()}
    parsed = urlparse(url)
    local_dev = parsed.hostname in hosts and parsed.hostname in ("127.0.0.1", "localhost")
    return parsed.hostname in hosts and (parsed.scheme == "wss" or local_dev)


def short_code(web_pk: bytes, helper_pk: bytes) -> str:
    digest = hashlib.sha256(web_pk + helper_pk).hexdigest().upper()
    return f"{digest[:3]}-{digest[3:6]}"


class Channel:
    def __init__(self, room: str, web_pk_raw: bytes) -> None:
        self.room = room
        self.web_pk_raw = web_pk_raw
        self.sk = ec.generate_private_key(ec.SECP256R1())
        self.pk_raw = self.sk.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), web_pk_raw)
        secret = self.sk.exchange(ec.ECDH(), peer)
        okm = HKDF(algorithm=hashes.SHA256(), length=64, salt=room.encode(), info=INFO).derive(secret)
        self.send_key = AESGCM(okm[:32])  # helper -> web
        self.recv_key = AESGCM(okm[32:])  # web -> helper
        self.send_ctr = 0
        self.recv_ctr = -1
        self.code = short_code(web_pk_raw, self.pk_raw)
        self._partial = bytearray()

    def seal(self, payload: bytes) -> list[bytes]:
        frames = []
        chunks = [payload[i : i + MAX_FRAME] for i in range(0, len(payload), MAX_FRAME)] or [b""]
        for i, chunk in enumerate(chunks):
            more = 1 if i < len(chunks) - 1 else 0
            nonce = struct.pack(">IQ", DIR_HELPER_TO_WEB, self.send_ctr)
            self.send_ctr += 1
            frames.append(nonce + self.send_key.encrypt(nonce, bytes([more]) + chunk, None))
        return frames

    def open(self, frame: bytes) -> Optional[bytes]:
        if len(frame) < 12 + 16 + 1:
            raise ValueError("short frame")
        direction, ctr = struct.unpack(">IQ", frame[:12])
        if direction != DIR_WEB_TO_HELPER or ctr <= self.recv_ctr:
            raise ValueError("replayed or misdirected frame")
        plain = self.recv_key.decrypt(frame[:12], frame[12:], None)
        self.recv_ctr = ctr
        self._partial += plain[1:]
        if plain[0] == 1:
            return None
        out = bytes(self._partial)
        self._partial.clear()
        return out


ACTIVE: dict[str, asyncio.Task] = {}


async def run_relay_session(relay_url: str, room: str, web_pk_b64: str) -> None:
    if not allowed_relay(relay_url):
        print(f"[relay] refusing untrusted relay {relay_url}")
        return
    web_pk = b64u_decode(web_pk_b64)
    if len(web_pk) != 65 or len(room) < 16:
        return
    channel = Channel(room, web_pk)
    url = f"{relay_url.rstrip('/')}/r/{room}?role=helper"
    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(url, heartbeat=25, max_msg_size=2 * 1024 * 1024) as ws:
            await ws.send_str(json.dumps({"type": "hello", "v": 1, "pk": b64u(channel.pk_raw)}))
            trusted = approval.is_trusted(web_pk)
            if not trusted:
                await ws.send_str(json.dumps({"type": "awaiting_approval", "code": channel.code}))
                if not await approval.ask(channel.code):
                    await ws.send_str(json.dumps({"type": "denied"}))
                    return
                approval.remember(web_pk)
            await ws.send_str(json.dumps({"type": "ready"}))

            async def send(msg: dict) -> None:
                for frame in channel.seal(json.dumps(msg, separators=(",", ":")).encode()):
                    await ws.send_bytes(frame)

            rpc = Rpc(send, "relay")
            try:
                async for message in ws:
                    if message.type == aiohttp.WSMsgType.BINARY:
                        try:
                            plain = channel.open(message.data)
                        except Exception:
                            break  # tampering or replay: drop the session
                        if plain is not None:
                            asyncio.create_task(rpc.handle(json.loads(plain)))
                    elif message.type == aiohttp.WSMsgType.TEXT:
                        ctl = json.loads(message.data)
                        if ctl.get("type") == "peer_left":
                            break
                    elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
            finally:
                rpc.close()


def start_relay_session(relay_url: str, room: str, web_pk_b64: str) -> None:
    if room in ACTIVE and not ACTIVE[room].done():
        return  # the same link delivered twice

    async def guarded() -> None:
        try:
            await run_relay_session(relay_url, room, web_pk_b64)
        except Exception as exc:  # surface failures instead of losing them in a background task
            print(f"[relay] session ended: {type(exc).__name__}: {exc}")

    task = asyncio.create_task(guarded())
    ACTIVE[room] = task
    task.add_done_callback(lambda _t: ACTIVE.pop(room, None))


def handle_pair_url(url: str) -> bool:
    """Accepts `unbubble://pair?room=…&pk=…&relay=wss://…`."""
    from urllib.parse import parse_qs

    parsed = urlparse(url)
    if parsed.scheme != "unbubble":
        return False
    action = parsed.netloc or parsed.path.strip("/")
    if action != "pair":
        return action in ("open", "")  # plain launch from the website
    q = parse_qs(parsed.query)
    try:
        room, pk = q["room"][0], q["pk"][0]
    except (KeyError, IndexError):
        return False
    relay = q.get("relay", ["wss://relay.thegreymirror.com"])[0]
    start_relay_session(relay, room, pk)
    return True
