// ConnectClient: finds the Unbubble helper and talks to it.
//
// Transport order:
//   1. Same origin — the page is the standalone app served by the helper itself.
//   2. Direct      — ws://127.0.0.1:<port>/ws (Chrome, Edge, Firefox, Brave, Arc). Chrome asks
//                    once for "local network access"; the helper only accepts allowed origins.
//   3. Relay       — Safari blocks HTTPS pages from reaching localhost, so the page and helper meet
//                    in an end-to-end encrypted relay room (see crypto.ts).

import { Channel, b64u, fromB64u, loadIdentity, shortCode, type Identity } from "./crypto";
import {
  HelperError,
  PORTS,
  type Hello,
  type Method,
  type Params,
  type Result,
  type ServerEvent,
} from "./protocol";

export type ConnectStatus =
  | { state: "idle" }
  | { state: "searching" }
  | { state: "not_found" }
  | { state: "awaiting_approval"; code: string }
  | { state: "denied" }
  | { state: "connected"; hello: Hello; transport: "same-origin" | "direct" | "relay" }
  | { state: "lost" };

export interface ConnectOptions {
  relayUrl: string;
  /** How long to wait for the helper to answer a relay pairing before deciding it isn't installed. */
  relayTimeoutMs?: number;
}

type Listener<T> = (value: T) => void;

interface Transport {
  send(msg: unknown): void;
  close(): void;
  onMessage: (msg: unknown) => void;
  onClose: () => void;
}

export function isAppHost(): boolean {
  return (
    (location.hostname === "127.0.0.1" || location.hostname === "localhost") &&
    PORTS.includes(Number(location.port) as (typeof PORTS)[number])
  );
}

export function prefersRelay(): boolean {
  // WebKit (Safari, and every iOS browser) treats http/ws loopback from HTTPS pages as mixed content.
  const ua = navigator.userAgent;
  const webkitOnly = /AppleWebKit/.test(ua) && !/Chrome|Chromium|Edg|OPR|Firefox|FxiOS|CriOS/.test(ua);
  return webkitOnly && location.protocol === "https:";
}

// ---- direct ---------------------------------------------------------------------------------

async function probe(port: number, signal: AbortSignal): Promise<boolean> {
  try {
    // Loopback is a "potentially trustworthy" origin in Chromium and Firefox, so this isn't mixed
    // content. Chrome shows its one-time Local Network Access prompt on the first request.
    const res = await fetch(`http://127.0.0.1:${port}/status`, { signal, cache: "no-store" });
    return res.ok && (await res.json()).app === "Unbubble";
  } catch {
    return false;
  }
}

function openSocket(url: string): Promise<Transport> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    const t: Transport = {
      send: (msg) => ws.send(JSON.stringify(msg)),
      close: () => ws.close(),
      onMessage: () => {},
      onClose: () => {},
    };
    ws.onopen = () => resolve(t);
    ws.onerror = () => reject(new Error("socket error"));
    ws.onclose = () => t.onClose();
    ws.onmessage = (e) => t.onMessage(JSON.parse(String(e.data)));
  });
}

async function connectDirect(): Promise<Transport | null> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 2500);
  try {
    for (const port of PORTS) {
      if (await probe(port, ctrl.signal)) return await openSocket(`ws://127.0.0.1:${port}/ws`);
    }
    return null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

// ---- relay ----------------------------------------------------------------------------------

async function connectRelay(
  relayUrl: string,
  timeoutMs: number,
  onApproval: (code: string) => void,
): Promise<Transport | "not_found" | "denied"> {
  const identity: Identity = await loadIdentity();
  const room = b64u(crypto.getRandomValues(new Uint8Array(18)));
  const ws = new WebSocket(`${relayUrl.replace(/\/$/, "")}/r/${room}?role=web`);
  ws.binaryType = "arraybuffer";

  await new Promise<void>((resolve, reject) => {
    ws.onopen = () => resolve();
    ws.onerror = () => reject(new Error("relay unreachable"));
  });

  // Hand our public key to the helper through the OS (never through the relay) and wake it.
  const link = `unbubble://pair?room=${room}&pk=${b64u(identity.publicRaw)}&relay=${encodeURIComponent(relayUrl)}`;
  window.location.assign(link);

  return new Promise((resolve) => {
    let channel: Channel | null = null;
    let settled = false;
    const t: Transport = {
      send: (msg) => {
        if (!channel) return;
        void channel.seal(JSON.stringify(msg)).then((frames) => frames.forEach((f) => ws.send(f)));
      },
      close: () => ws.close(),
      onMessage: () => {},
      onClose: () => {},
    };
    const finish = (v: Transport | "not_found" | "denied") => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (v !== t) ws.close();
      resolve(v);
    };
    // No hello in time: the helper isn't installed (or the person dismissed the open prompt).
    let timer = setTimeout(() => finish("not_found"), timeoutMs);

    // Decrypt strictly in order: frames are processed one after another.
    let queue = Promise.resolve();
    ws.onmessage = (e) => {
      if (typeof e.data === "string") {
        const ctl = JSON.parse(e.data) as { type: string; pk?: string; code?: string };
        if (ctl.type === "hello" && ctl.pk) {
          clearTimeout(timer);
          timer = setTimeout(() => finish("not_found"), 150_000); // time for the person to approve
          const helperPk = fromB64u(ctl.pk);
          queue = queue.then(async () => {
            channel = await Channel.create(identity, helperPk, room);
            // Show the code we computed ourselves; the relay can't alter it.
            onApproval(await shortCode(identity.publicRaw, helperPk));
          });
        } else if (ctl.type === "ready") {
          queue = queue.then(() => finish(t));
        } else if (ctl.type === "denied") {
          finish("denied");
        } else if (ctl.type === "peer_left") {
          t.onClose();
        }
        return;
      }
      const frame = e.data as ArrayBuffer;
      queue = queue.then(async () => {
        if (!channel) return;
        try {
          const text = await channel.open(frame);
          if (text) t.onMessage(JSON.parse(text));
        } catch {
          ws.close(); // tampered or replayed frame
        }
      });
    };
    ws.onclose = () => {
      if (!settled) finish("not_found");
      else t.onClose();
    };
  });
}

// ---- client ---------------------------------------------------------------------------------

export class ConnectClient {
  private transport: Transport | null = null;
  private nextId = 1;
  private pending = new Map<number, { resolve: (v: unknown) => void; reject: (e: unknown) => void }>();
  private eventListeners = new Set<Listener<ServerEvent>>();
  private statusListeners = new Set<Listener<ConnectStatus>>();
  private attachmentCache = new Map<string, string>();
  status: ConnectStatus = { state: "idle" };

  constructor(private readonly options: ConnectOptions) {}

  onStatus(fn: Listener<ConnectStatus>): () => void {
    this.statusListeners.add(fn);
    fn(this.status);
    return () => this.statusListeners.delete(fn);
  }

  onEvent(fn: Listener<ServerEvent>): () => void {
    this.eventListeners.add(fn);
    return () => this.eventListeners.delete(fn);
  }

  private setStatus(s: ConnectStatus) {
    this.status = s;
    this.statusListeners.forEach((fn) => fn(s));
  }

  /** Quiet check used on page load: never opens a pairing prompt. */
  async detect(): Promise<boolean> {
    if (this.status.state === "connected") return true;
    this.setStatus({ state: "searching" });
    if (isAppHost()) return this.attach(await openSocket(`ws://${location.host}/ws`), "same-origin");
    if (!prefersRelay()) {
      const t = await connectDirect();
      if (t) return this.attach(t, "direct");
    }
    this.setStatus({ state: "not_found" });
    return false;
  }

  /**
   * Called from a click. Wakes the helper if it's installed but not running, and pairs over the
   * relay where the browser can't reach localhost.
   */
  async connect(): Promise<boolean> {
    if (await this.detect()) return true;
    this.setStatus({ state: "searching" });
    if (!prefersRelay()) {
      window.location.assign("unbubble://open");
      for (let i = 0; i < 12; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        const t = await connectDirect();
        if (t) return this.attach(t, "direct");
      }
      this.setStatus({ state: "not_found" });
      return false;
    }
    const result = await connectRelay(this.options.relayUrl, this.options.relayTimeoutMs ?? 9000, (code) =>
      this.setStatus({ state: "awaiting_approval", code }),
    );
    if (result === "not_found" || result === "denied") {
      this.setStatus({ state: result });
      return false;
    }
    return this.attach(result, "relay");
  }

  private async attach(t: Transport, kind: "same-origin" | "direct" | "relay"): Promise<boolean> {
    this.transport = t;
    t.onMessage = (raw) => {
      const msg = raw as { id?: number; result?: unknown; error?: { code: string; message: string }; event?: string };
      if (msg.id != null && this.pending.has(msg.id)) {
        const p = this.pending.get(msg.id)!;
        this.pending.delete(msg.id);
        if (msg.error) p.reject(new HelperError(msg.error));
        else p.resolve(msg.result);
      } else if (msg.event) {
        this.eventListeners.forEach((fn) => fn(msg as ServerEvent));
      }
    };
    t.onClose = () => {
      this.transport = null;
      this.pending.forEach((p) => p.reject(new HelperError({ code: "disconnected", message: "Lost connection to Unbubble" })));
      this.pending.clear();
      this.setStatus({ state: "lost" });
    };
    const hello = await this.call("hello", { client: "web" });
    this.setStatus({ state: "connected", hello, transport: kind });
    return true;
  }

  call<M extends Method>(method: M, params: Params<M>, timeoutMs = 120_000): Promise<Result<M>> {
    const t = this.transport;
    if (!t) return Promise.reject(new HelperError({ code: "disconnected", message: "Not connected" }));
    const id = this.nextId++;
    return new Promise<Result<M>>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new HelperError({ code: "timeout", message: `${method} timed out` }));
      }, timeoutMs);
      this.pending.set(id, {
        resolve: (v) => {
          clearTimeout(timer);
          resolve(v as Result<M>);
        },
        reject: (e) => {
          clearTimeout(timer);
          reject(e);
        },
      });
      t.send({ id, method, params });
    });
  }

  /** Object URL for an attachment (HEIC already converted by the helper). Cached per session. */
  async attachmentUrl(archiveId: string, attachmentId: number, size: "thumb" | "preview" = "preview"): Promise<string> {
    const key = `${archiveId}:${attachmentId}:${size}`;
    const hit = this.attachmentCache.get(key);
    if (hit) return hit;
    const res = await this.call("attachment.get", { archive_id: archiveId, attachment_id: attachmentId, size });
    const bytes = fromB64u(res.data.replace(/\+/g, "-").replace(/\//g, "_"));
    const url = URL.createObjectURL(new Blob([bytes], { type: res.mime }));
    this.attachmentCache.set(key, url);
    if (this.attachmentCache.size > 400) {
      const [oldKey, oldUrl] = this.attachmentCache.entries().next().value as [string, string];
      URL.revokeObjectURL(oldUrl);
      this.attachmentCache.delete(oldKey);
    }
    return url;
  }

  disconnect(): void {
    this.transport?.close();
  }
}
