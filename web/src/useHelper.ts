import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { ConnectClient, type ConnectStatus } from "./connect/client";
import type { ServerEvent } from "./connect/protocol";
import { RELAY_URL } from "./config";

let shared: ConnectClient | null = null;
export function getClient(): ConnectClient {
  shared ??= new ConnectClient({ relayUrl: RELAY_URL });
  return shared;
}

export function useConnectStatus(): ConnectStatus {
  const client = getClient();
  const subscribe = useMemo(() => (cb: () => void) => client.onStatus(cb), [client]);
  return useSyncExternalStore(subscribe, () => client.status);
}

export function useHelperEvents(handler: (e: ServerEvent) => void): void {
  useEffect(() => getClient().onEvent(handler), [handler]);
}

/** Poll a helper call while connected. */
export function usePoll<T>(fn: () => Promise<T>, ms: number, enabled: boolean): T | undefined {
  const [value, setValue] = useState<T>();
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const tick = async () => {
      try {
        const v = await fn();
        if (alive) setValue(v);
      } catch {
        /* transient: next tick */
      }
    };
    void tick();
    const id = setInterval(tick, ms);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [fn, ms, enabled]);
  return value;
}

/** Smoothly animate between screens where the browser supports View Transitions. */
export function withTransition(update: () => void): void {
  const doc = document as Document & {
    startViewTransition?: (cb: () => void) => { ready: Promise<void>; finished: Promise<void>; updateCallbackDone: Promise<void> };
  };
  if (doc.startViewTransition && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
    const t = doc.startViewTransition(update);
    // A newer transition can skip this one; that's expected, not an error.
    for (const p of [t.ready, t.finished, t.updateCallbackDone]) p.catch(() => {});
  } else update();
}
