/**
 * Unbubble relay — relay.thegreymirror.com
 *
 * Pairs one web page with one Unbubble helper per room and forwards frames between
 * them. Frames are AES-256-GCM encrypted end to end with keys the relay never sees (the page's
 * public key reaches the helper through the OS via an unbubble:// link, not through here), so
 * the relay can read nothing and can't impersonate either side.
 *
 * Route: GET /r/<room>?role=web|helper  (WebSocket upgrade)
 */

export interface Env {
  ROOMS: DurableObjectNamespace;
  ALLOWED_ORIGINS: string; // comma-separated
}

const ROOM_RE = /^[A-Za-z0-9_-]{22,64}$/;
const MAX_FRAME = 1024 * 1024;
const IDLE_MS = 30 * 60 * 1000;

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/health") return new Response("ok");
    const match = url.pathname.match(/^\/r\/([^/]+)$/);
    if (!match || !ROOM_RE.test(match[1]!)) return new Response("Not found", { status: 404 });
    if (request.headers.get("Upgrade")?.toLowerCase() !== "websocket") {
      return new Response("Expected WebSocket", { status: 426 });
    }
    const role = url.searchParams.get("role");
    if (role !== "web" && role !== "helper") return new Response("Bad role", { status: 400 });
    if (role === "web") {
      const allowed = env.ALLOWED_ORIGINS.split(",").map((s) => s.trim());
      if (!allowed.includes(request.headers.get("Origin") ?? "")) return new Response("Forbidden", { status: 403 });
    }
    const stub = env.ROOMS.get(env.ROOMS.idFromName(match[1]!));
    return stub.fetch(request);
  },
} satisfies ExportedHandler<Env>;

export class Room implements DurableObject {
  constructor(private readonly state: DurableObjectState) {}

  async fetch(request: Request): Promise<Response> {
    const role = new URL(request.url).searchParams.get("role") as "web" | "helper";
    if (this.state.getWebSockets(role).length > 0) return new Response("Role taken", { status: 409 });
    const { 0: client, 1: server } = new WebSocketPair();
    this.state.acceptWebSocket(server, [role]);
    await this.state.storage.setAlarm(Date.now() + IDLE_MS);
    if (role === "helper") this.peer("web")?.send(JSON.stringify({ type: "peer_joined" }));
    return new Response(null, { status: 101, webSocket: client });
  }

  private roleOf(ws: WebSocket): "web" | "helper" {
    return this.state.getTags(ws)[0] as "web" | "helper";
  }

  private peer(role: "web" | "helper"): WebSocket | undefined {
    return this.state.getWebSockets(role)[0];
  }

  async webSocketMessage(ws: WebSocket, message: string | ArrayBuffer): Promise<void> {
    const size = typeof message === "string" ? message.length : message.byteLength;
    if (size > MAX_FRAME) {
      ws.close(1009, "Frame too large");
      return;
    }
    const role = this.roleOf(ws);
    // Pages only ever send encrypted binary frames; helpers also send small plaintext control
    // messages (their public key, "awaiting approval", "ready").
    if (typeof message === "string" && role === "web") return;
    this.peer(role === "web" ? "helper" : "web")?.send(message);
    await this.state.storage.setAlarm(Date.now() + IDLE_MS);
  }

  async webSocketClose(ws: WebSocket): Promise<void> {
    const other = this.peer(this.roleOf(ws) === "web" ? "helper" : "web");
    other?.send(JSON.stringify({ type: "peer_left" }));
    other?.close(1000, "Peer left");
  }

  async webSocketError(ws: WebSocket): Promise<void> {
    await this.webSocketClose(ws);
  }

  async alarm(): Promise<void> {
    for (const ws of this.state.getWebSockets()) ws.close(1000, "Idle");
  }
}
