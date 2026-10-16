// Local twin of the Cloudflare relay (same rules) for development and tests.
//   node dev-relay.mjs [port]      ALLOWED_ORIGINS=http://localhost:5173 by default
import { WebSocketServer } from "ws";
import http from "node:http";

const port = Number(process.argv[2] ?? 8787);
const allowed = (process.env.ALLOWED_ORIGINS ?? "http://localhost:5173,http://127.0.0.1:5173").split(",");
const ROOM_RE = /^[A-Za-z0-9_-]{22,64}$/;
const rooms = new Map(); // room -> { web, helper }

const server = http.createServer((_, res) => res.end("ok"));
const wss = new WebSocketServer({ noServer: true, maxPayload: 1024 * 1024 });

server.on("upgrade", (req, socket, head) => {
  const url = new URL(req.url, "http://x");
  const m = url.pathname.match(/^\/r\/([^/]+)$/);
  const role = url.searchParams.get("role");
  const reject = (code) => { socket.write(`HTTP/1.1 ${code} No\r\n\r\n`); socket.destroy(); };
  if (!m || !ROOM_RE.test(m[1]) || (role !== "web" && role !== "helper")) return reject(404);
  if (role === "web" && !allowed.includes(req.headers.origin ?? "")) return reject(403);
  const room = rooms.get(m[1]) ?? {};
  if (room[role]) return reject(409);
  wss.handleUpgrade(req, socket, head, (ws) => {
    room[role] = ws;
    rooms.set(m[1], room);
    const other = () => room[role === "web" ? "helper" : "web"];
    if (role === "helper") other()?.send(JSON.stringify({ type: "peer_joined" }));
    ws.on("message", (data, isBinary) => {
      if (!isBinary && role === "web") return;
      other()?.send(data, { binary: isBinary });
    });
    ws.on("close", () => {
      delete room[role];
      other()?.send(JSON.stringify({ type: "peer_left" }));
      other()?.close();
      if (!room.web && !room.helper) rooms.delete(m[1]);
    });
  });
});
server.listen(port, () => console.log(`dev relay on ws://localhost:${port}`));
