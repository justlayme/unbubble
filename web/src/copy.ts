// What went wrong and what to do next, in plain language. Keyed by the helper's error codes.

export interface Explanation {
  title: string;
  body: string;
  action?: { label: string; kind: "link"; href: string } | { label: string; kind: "install-driver" } | { label: string; kind: "retry" };
}

export function explain(code: string, hint?: string, fallback?: string): Explanation {
  switch (code) {
    case "usbmux_unavailable":
      if (hint === "windows_drivers")
        return {
          title: "Windows needs Apple's iPhone driver",
          body: "It's free from Apple and takes about a minute. Windows will ask for permission to install it.",
          action: { label: "Install iPhone driver", kind: "install-driver" },
        };
      if (hint === "linux_usbmuxd")
        return { title: "iPhone service isn't running", body: "Install usbmuxd (for example: sudo apt install usbmuxd), then plug your iPhone back in." };
      return { title: "Your computer isn't responding to the iPhone", body: "Unplug your iPhone, plug it back in, and try again.", action: { label: "Try again", kind: "retry" } };
    case "trust_denied":
      return { title: "Your iPhone didn't trust this computer", body: "Unplug it, plug it back in, unlock it and tap Trust.", action: { label: "Try again", kind: "retry" } };
    case "trust_timeout":
      return { title: "No answer from your iPhone", body: "Unlock your iPhone and tap Trust when it asks, then try again.", action: { label: "Try again", kind: "retry" } };
    case "device_gone":
      return { title: "Your iPhone was disconnected", body: "Plug it back in and start the export again.", action: { label: "Try again", kind: "retry" } };
    case "wrong_password":
      return { title: "That backup password didn't work", body: "Check it and try again. It's the password set for encrypted backups in Finder or iTunes, not your iPhone passcode." };
    case "no_messages":
      return { title: "No Messages database came back", body: "Restart your iPhone, plug it in again and retry.", action: { label: "Try again", kind: "retry" } };
    case "driver_declined":
      return { title: "The driver wasn't installed", body: "Windows needs your permission to install Apple's iPhone driver. Try again and choose Yes.", action: { label: "Install iPhone driver", kind: "install-driver" } };
    case "driver_failed":
      return {
        title: "The driver didn't install",
        body: "Install the free Apple Devices app from the Microsoft Store instead; it includes the same driver. Then plug your iPhone back in.",
        action: { label: "Open Microsoft Store", kind: "link", href: "ms-windows-store://pdp/?productid=9NP83LWLPZ9K" },
      };
    case "recording":
      return { title: "That recording couldn't be read", body: fallback ?? "Try another recording.", action: { label: "Try again", kind: "retry" } };
    case "device":
      return { title: "Couldn't see your iPhone's videos", body: fallback ?? "Unlock your iPhone and make sure it trusts this PC.", action: { label: "Try again", kind: "retry" } };
    case "busy":
      return { title: "Another export is running", body: "Wait for it to finish, then try again." };
    case "disconnected":
      return { title: "Lost connection to Unbubble", body: "Make sure it's still running, then reconnect.", action: { label: "Reconnect", kind: "retry" } };
    case "unpack_failed":
      return { title: "Couldn't unpack your messages", body: fallback ?? "Try the export again.", action: { label: "Try again", kind: "retry" } };
    default:
      return { title: "Something went wrong", body: fallback ?? "Try again.", action: { label: "Try again", kind: "retry" } };
  }
}
