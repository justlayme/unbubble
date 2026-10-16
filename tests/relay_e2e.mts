// End-to-end: the web SDK's real crypto (web/src/connect/crypto.ts) ↔ dev relay ↔ Python helper.
// Simulates the OS delivering the unbubble://pair link by posting it to the helper's control port.
import WebSocket from "../relay/node_modules/ws/wrapper.mjs";
import { Channel, b64u, fromB64u, shortCode } from "../web/src/connect/crypto.ts";

const RELAY = "ws://localhost:8787";
const keyPair = (await crypto.subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, false, ["deriveBits"])) as CryptoKeyPair;
const identity = { keyPair, publicRaw: new Uint8Array(await crypto.subtle.exportKey("raw", keyPair.publicKey)) };
const room = b64u(crypto.getRandomValues(new Uint8Array(18)));

const ws = new WebSocket(`${RELAY}/r/${room}?role=web`, { headers: { Origin: "http://localhost:5173" } });
ws.binaryType = "arraybuffer";
await new Promise((r, j) => { ws.on("open", r); ws.on("error", j); });

// Reject check: a page from another origin can't join as "web".
const evil = new WebSocket(`${RELAY}/r/${b64u(crypto.getRandomValues(new Uint8Array(18)))}?role=web`, { headers: { Origin: "https://evil.example" } });
const evilResult = await new Promise((r) => { evil.on("open", () => r("ACCEPTED")); evil.on("unexpected-response", (_q, res) => r(res.statusCode)); evil.on("error", () => r("error")); });
console.log("relay rejects foreign origin:", evilResult);

const link = `unbubble://pair?room=${room}&pk=${b64u(identity.publicRaw)}&relay=${encodeURIComponent(RELAY)}`;
const ctl = await fetch("http://127.0.0.1:47613/control/url", { method: "POST", headers: { "Content-Type": "application/json", "X-GM-Local": "1" }, body: JSON.stringify({ url: link }) });
console.log("link delivered to helper:", ctl.status);

let channel: Channel | null = null;
const pending = new Map<number, (v: any) => void>();
let queue = Promise.resolve();
const ready = new Promise<void>((resolve) => {
  ws.on("message", (data: ArrayBuffer | Buffer, isBinary: boolean) => {
    if (!isBinary) {
      const msg = JSON.parse(data.toString());
      if (msg.type === "hello") {
        queue = queue.then(async () => {
          channel = await Channel.create(identity, fromB64u(msg.pk), room);
          console.log("short code (web side):", await shortCode(identity.publicRaw, fromB64u(msg.pk)));
        });
      } else if (msg.type === "awaiting_approval") console.log("helper shows code:", msg.code);
      else if (msg.type === "ready") queue = queue.then(() => resolve());
      return;
    }
    const buf = data instanceof ArrayBuffer ? data : (data as Buffer).buffer.slice((data as Buffer).byteOffset, (data as Buffer).byteOffset + (data as Buffer).byteLength);
    queue = queue.then(async () => {
      const text = await channel!.open(buf as ArrayBuffer);
      if (text) { const m = JSON.parse(text); if (m.id && pending.has(m.id)) { pending.get(m.id)!(m); pending.delete(m.id); } }
    });
  });
});
await ready;
let id = 0;
async function call(method: string, params: object = {}) {
  const myId = ++id;
  const p = new Promise<any>((r) => pending.set(myId, r));
  for (const f of await channel!.seal(JSON.stringify({ id: myId, method, params }))) ws.send(f);
  return p;
}
const hello = await call("hello");
console.log("hello over encrypted relay:", hello.result.app, hello.result.transport);
const archives = (await call("archives.list")).result;
const threads = (await call("archive.threads", { archive_id: archives[0].id })).result;
const maya = threads.find((t: any) => t.title === "Maya Okafor");
const dump = await call("archive.thread_dump", { archive_id: archives[0].id, thread_id: maya.id });
console.log(`thread_dump: ${dump.result.messages.length} messages (${(JSON.stringify(dump).length / 1e6).toFixed(1)} MB → fragmented frames reassembled)`);
const big = "x".repeat(700_000); // web→helper fragmentation
const echo = await call("archive.search", { archive_id: archives[0].id, query: "dinner " + big.slice(0, 10) });
console.log("search ok:", Array.isArray(echo.result));
const heavy = await call("archive.threads", { archive_id: archives[0].id, query: big });
console.log("700 KB request fragmented web→helper:", Array.isArray(heavy.result));
ws.close();
process.exit(0);
