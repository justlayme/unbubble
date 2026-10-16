# Unbubble

*Get your messages out of their bubbles.*

**Free, open-source iPhone message exporter for Windows.** Plug in an iPhone and export every iMessage and text conversation, with photos, reactions, replies and edits, without making a full backup.

It powers the upload flow on [thegreymirror.com](https://thegreymirror.com), and it works on its own as a standalone Windows app.

## Where it fits on thegreymirror.com

Grey Mirror's upload wizard asks where someone's messages live. People with only an iPhone and a Windows PC are sent here: they install Unbubble, plug in the iPhone, and pick the conversation to upload, all without leaving the site. Mac users get the Grey Mirror Mac app instead, which reads Messages on the Mac directly. The wizard itself lives in the site; this repository is the exporter it drives.

## What Unbubble does (Windows)

1. **Sets up Apple's iPhone driver in one click.** Windows can only reach an iPhone through Apple Mobile Device Service. The app installs Apple's official standalone package (`Apple.AppleMobileDeviceSupport`, via winget) after a single Windows permission prompt. There's no iTunes and no detour to another site.
2. **Streams only your messages off the iPhone.** iOS releases the Messages database only through its backup service, so the app drives that service itself and keeps only Messages and Contacts as the bytes arrive. Photos, apps and everything else are drained and discarded on the fly, so there's no backup folder and no need for 100+ GB free.
3. **Makes every export after the first fast.** It keeps the phone's small file index, so the next run is incremental: the iPhone sends only what changed.
4. **Rebuilds every conversation faithfully**, including:
   - SMS and iMessage threads with the same person, merged into one conversation
   - group names and member changes
   - tapbacks and emoji reactions, with removals replayed
   - inline replies, full edit history, unsent messages and message effects
   - text Apple stores only in `attributedBody`
   - HEIC photos converted for every browser
   - attachments offloaded to iCloud, clearly flagged
5. **Saves to your PC** as a browsable offline HTML archive, one PDF per conversation, CSV, or JSON. It defaults to your real Documents folder and warns before saving into OneDrive, which would upload your messages to the cloud.
6. **Uploads to Grey Mirror only when you choose to,** one conversation at a time.

No accounts, no analytics, no telemetry.

## How the website reaches the app

```
thegreymirror.com (browser)
   │  Chrome, Edge, Firefox, Brave:  ws://127.0.0.1:47613  (origin-locked to thegreymirror.com / justlay.me)
   │  Safari:  wss://relay.thegreymirror.com  (end-to-end encrypted; the relay sees only ciphertext)
   ▼
Unbubble (notification-area app on the PC)  ──USB──►  iPhone
```

- **Direct.** The app listens only on loopback and accepts WebSocket connections whose `Origin` is thegreymirror.com or justlay.me (or its own UI). It rejects mismatched `Host` headers, which blocks DNS rebinding.
- **Relay, for Safari on Windows.** The page's ECDH P-256 public key reaches the app through the `unbubble://` link handler, never through the relay. Keys come from HKDF-SHA256 with one AES-256-GCM key per direction, and counter nonces reject replays. The first pairing from a browser needs a native **Allow** prompt with a matching code.

## Repository layout

| Folder | What | License |
| --- | --- | --- |
| `helper/` | Unbubble: iPhone capture, driver setup, rebuild, exporters, local server, relay client, tray app, Windows installer | GPL-3.0-or-later |
| `web/` | The export interface (standalone or embedded in a site) and the `ConnectClient` SDK (React 19, Vite 8, TypeScript 7) | MIT |
| `relay/` | The Safari relay (Cloudflare Worker + Durable Objects) and a Node twin for local development | MIT |
| `tests/` | Cross-language end-to-end test of the encrypted relay | MIT |

The app is GPL because it builds on [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) (GPL-3.0) and [pyiosbackup](https://github.com/doronz88/pyiosbackup). The web and relay code talk to it over a network protocol and are MIT, so any site can embed the flow.

## Install

Download **Unbubble-Setup.exe** from the [latest release](https://github.com/justlayme/unbubble/releases/latest). It needs Windows 10 (1809) or 11 and installs per-user, without admin rights; only Apple's driver asks for permission.

## Develop

```bash
# App (Python 3.11+)
cd helper && pip install -e ".[dev]" && pytest -q
python tests/make_fixture.py /tmp/fixture 20000      # a realistic sms.db with every edge case

# Run against a mock iPhone, including the Windows driver flow (no hardware needed)
GM_DEV=1 GM_MOCK_DEVICE=1 GM_MOCK_FIXTURE=/tmp/fixture GM_MOCK_PLATFORM=windows GM_MOCK_DRIVER_MISSING=1 \
  python -m unbubble serve

# Web, in another terminal
cd web && npm install && npm test && npm run dev     # http://127.0.0.1:5173/export/
```

Useful app commands:

- `unbubble import-db <sms.db>` imports a Messages database directly.
- `unbubble export <archive-id> --formats html,pdf,json,csv` runs an export from the command line.

## Embed on thegreymirror.com

Build `web/` (`npm run build`) and serve `dist/` at `/export/`, or mount it into an existing page:

```tsx
import { UnbubbleApp } from "unbubble-web";

<UnbubbleApp
  uploadLabel="Upload to Grey Mirror"
  // Called with one conversation when the person chooses to upload it.
  onUpload={async ({ thread, messages, source }) => {
    await fetch("/api/upload", { method: "POST", body: JSON.stringify({ thread, messages, source }) });
  }}
  onBack={() => history.back()}
/>
```

Sites that want their own interface can use `ConnectClient` directly and call the app's RPC methods (`devices.list`, `capture.start`, `archive.threads`, `archive.thread_dump`, …).

Configure with `VITE_GM_RELAY_URL`, `VITE_GM_SITE_URL`, `VITE_GM_SOURCE_URL` and `VITE_GM_DOWNLOAD_WIN`.

## Release

Push a tag like `v1.0.0`. `.github/workflows/release.yml` runs the tests on Windows, builds the app and the installer, and attaches `Unbubble-Setup.exe` to a GitHub Release, which is what the website's download button points at.

Sign the installer to avoid SmartScreen warnings. Open-source projects can get free code signing from the [SignPath Foundation](https://signpath.org); Azure Trusted Signing also works.

## Limits worth knowing

- The **first** export takes about as long as an iPhone backup, because iOS sends everything once and the app discards what isn't Messages. Later exports are incremental. A USB 3 cable with a USB‑C iPhone is much faster.
- If the iPhone has **"Encrypt local backup"** on, the person enters that password once. It never leaves their PC.
- Attachments iOS **offloaded to iCloud** aren't on the phone, so they're listed but can't be exported.

See [SECURITY.md](SECURITY.md) to report a vulnerability privately.
