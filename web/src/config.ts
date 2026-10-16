import { isAppHost } from "./connect/client";

const env = import.meta.env;

/** Where this UI is running: inside thegreymirror.com, or as the free standalone app. */
export const HOST: "site" | "app" = isAppHost() ? "app" : "site";

export const RELAY_URL: string = env.VITE_GM_RELAY_URL ?? "wss://relay.thegreymirror.com";
export const SITE_URL: string = env.VITE_GM_SITE_URL ?? "https://thegreymirror.com";
export const SOURCE_URL: string = env.VITE_GM_SOURCE_URL ?? "https://github.com/justlayme/unbubble";

/** The Windows installer (built and attached to every GitHub release). */
export const WINDOWS_DOWNLOAD: string =
  env.VITE_GM_DOWNLOAD_WIN ?? `${SOURCE_URL}/releases/latest/download/Unbubble-Setup.exe`;

export function guessPlatform(): "mac" | "windows" | "linux" {
  const p = (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData?.platform ?? navigator.platform;
  if (/win/i.test(p)) return "windows";
  if (/mac/i.test(p)) return "mac";
  return "linux";
}
