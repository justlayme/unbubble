// End-to-end encryption for the relay transport. Mirrors unbubble/relay_client.py.
//
//   shared   = ECDH-P256(web private key, helper public key)
//   okm      = HKDF-SHA256(shared, salt = room id, info = "unbubble-relay-v1", 64 bytes)
//   helper→web key = okm[0:32], web→helper key = okm[32:64]   (AES-256-GCM)
//   nonce    = uint32 direction (1 helper→web, 2 web→helper) ‖ uint64 counter, big-endian
//   payload  = 1 byte "more fragments follow" ‖ up to 512 KiB of JSON

const INFO = new TextEncoder().encode("unbubble-relay-v1");
const MAX_FRAME = 512 * 1024;
const DIR_HELPER_TO_WEB = 1;
const DIR_WEB_TO_HELPER = 2;

export type Bytes = Uint8Array<ArrayBuffer>;

export const b64u = (bytes: Uint8Array): string =>
  btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

export const fromB64u = (s: string): Bytes => {
  const bin = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (s.length % 4)) % 4));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
};

// ---- long-lived browser identity -------------------------------------------------------------
// The private key is non-extractable and lives in IndexedDB, so once the person approves this
// browser on their computer it reconnects without asking again.

const DB = "unbubble";
const STORE = "keys";

function idb<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open(DB, 1);
    open.onupgradeneeded = () => open.result.createObjectStore(STORE);
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const tx = open.result.transaction(STORE, mode);
      const req = fn(tx.objectStore(STORE));
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
      tx.oncomplete = () => open.result.close();
    };
  });
}

export interface Identity {
  keyPair: CryptoKeyPair;
  publicRaw: Bytes;
}

export async function loadIdentity(): Promise<Identity> {
  let keyPair: CryptoKeyPair | undefined;
  try {
    keyPair = await idb<CryptoKeyPair | undefined>("readonly", (s) => s.get("identity") as IDBRequest<CryptoKeyPair | undefined>);
  } catch {
    keyPair = undefined; // private browsing: fall back to a per-session key
  }
  if (!keyPair) {
    keyPair = await crypto.subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, false, ["deriveBits"]);
    try {
      await idb("readwrite", (s) => s.put(keyPair, "identity"));
    } catch {
      /* not persisted; the helper will ask for approval again next time */
    }
  }
  const publicRaw = new Uint8Array(await crypto.subtle.exportKey("raw", keyPair.publicKey));
  return { keyPair, publicRaw };
}

export async function shortCode(webPk: Bytes, helperPk: Bytes): Promise<string> {
  const joined = new Uint8Array(webPk.length + helperPk.length);
  joined.set(webPk);
  joined.set(helperPk, webPk.length);
  const hex = [...new Uint8Array(await crypto.subtle.digest("SHA-256", joined))]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
    .toUpperCase();
  return `${hex.slice(0, 3)}-${hex.slice(3, 6)}`;
}

export class Channel {
  private sendCtr = 0n;
  private recvCtr = -1n;
  private partial: Uint8Array[] = [];
  private readonly sendKey: CryptoKey;
  private readonly recvKey: CryptoKey;

  private constructor(sendKey: CryptoKey, recvKey: CryptoKey) {
    this.sendKey = sendKey;
    this.recvKey = recvKey;
  }

  static async create(identity: Identity, helperPkRaw: Bytes, room: string): Promise<Channel> {
    const helperPk = await crypto.subtle.importKey("raw", helperPkRaw, { name: "ECDH", namedCurve: "P-256" }, false, []);
    const shared = await crypto.subtle.deriveBits({ name: "ECDH", public: helperPk }, identity.keyPair.privateKey, 256);
    const hkdfKey = await crypto.subtle.importKey("raw", shared, "HKDF", false, ["deriveBits"]);
    const okm = new Uint8Array(
      await crypto.subtle.deriveBits(
        { name: "HKDF", hash: "SHA-256", salt: new TextEncoder().encode(room), info: INFO },
        hkdfKey,
        512,
      ),
    );
    const key = (bytes: Bytes, usage: KeyUsage) =>
      crypto.subtle.importKey("raw", bytes, { name: "AES-GCM" }, false, [usage]);
    return new Channel(await key(okm.slice(32), "encrypt"), await key(okm.slice(0, 32), "decrypt"));
  }

  private static nonce(direction: number, ctr: bigint): Bytes {
    const n = new Uint8Array(12);
    const v = new DataView(n.buffer);
    v.setUint32(0, direction);
    v.setBigUint64(4, ctr);
    return n;
  }

  async seal(json: string): Promise<ArrayBuffer[]> {
    const bytes = new TextEncoder().encode(json);
    const frames: ArrayBuffer[] = [];
    const count = Math.max(1, Math.ceil(bytes.length / MAX_FRAME));
    for (let i = 0; i < count; i++) {
      const chunk = bytes.subarray(i * MAX_FRAME, (i + 1) * MAX_FRAME);
      const plain = new Uint8Array(chunk.length + 1);
      plain[0] = i < count - 1 ? 1 : 0;
      plain.set(chunk, 1);
      const nonce = Channel.nonce(DIR_WEB_TO_HELPER, this.sendCtr++);
      const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce }, this.sendKey, plain));
      const frame = new Uint8Array(12 + ct.length);
      frame.set(nonce);
      frame.set(ct, 12);
      frames.push(frame.buffer);
    }
    return frames;
  }

  /** Returns the complete JSON text once the last fragment arrives, otherwise null. */
  async open(frame: ArrayBuffer): Promise<string | null> {
    const bytes: Bytes = new Uint8Array(frame);
    if (bytes.length < 12 + 16 + 1) throw new Error("short frame");
    const view = new DataView(frame);
    const direction = view.getUint32(0);
    const ctr = view.getBigUint64(4);
    if (direction !== DIR_HELPER_TO_WEB || ctr <= this.recvCtr) throw new Error("replayed or misdirected frame");
    const plain = new Uint8Array(
      await crypto.subtle.decrypt({ name: "AES-GCM", iv: bytes.subarray(0, 12) }, this.recvKey, bytes.subarray(12)),
    );
    this.recvCtr = ctr;
    this.partial.push(plain.subarray(1));
    if (plain[0] === 1) return null;
    const total = this.partial.reduce((n, p) => n + p.length, 0);
    const out = new Uint8Array(total);
    let at = 0;
    for (const p of this.partial) {
      out.set(p, at);
      at += p.length;
    }
    this.partial = [];
    return new TextDecoder().decode(out);
  }
}
