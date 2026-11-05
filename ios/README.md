# Unbubble for iPhone

**Export iMessage to PDF free or save iMessage to CSV directly on your iPhone. No Mac or PC required.** Free and open source, with no subscription, account or paid export unlock. Include available photos and files when you choose.

[Download Unbubble free on the App Store](https://apps.apple.com/us/app/unbubble-imessage-exporter/id6829522001) · [Official iPhone export guides and current compatibility](https://thegreymirror.com/grey-mirror/unbubble)

## How it works

Apple doesn't let apps read Messages, but it does let a Shortcut you approve. So Unbubble has two parts:

- **The app** (`Sources/`) listens on the iPhone's loopback address, `127.0.0.1:47821`, for the current export only, using a random token it hands the Shortcut.
- **The Shortcut** (`Shortcut/published/unbubble-export-3.plist`, built from the published Unbubble Export 2 by `Shortcut/build-export-3.py`, or [add it from iCloud](https://www.icloud.com/shortcuts/bf241069f92f4928991cbb8d7e045060)) is a plain loop. It asks the app which stretch of time to read, runs Find Messages over it and hands back what it found.

Find Messages can't filter by conversation and stops at about 500 results, so the app steers: it sizes each stretch from how busy the last one was, narrows and rereads one that comes back near the limit, and keeps only the chosen conversation. Everything else is dropped on arrival. A Shortcut run's memory only grows, so the app also tells it when to restart itself and carry on from the same place.

Nothing leaves the phone. See [PRIVACY.md](PRIVACY.md).

**Needs iOS 26 or later**, where Apple added the Messages actions. On iOS 26 Shortcuts stops with "There was a problem running" when it reads a conversation's recipients, so there the Shortcut skips them, and the app tells a group chat apart by its senders instead: three or more named people.

## Output

- **PDF:** a printable record with a cover page, dates, times, senders, numbered messages and pages, photos, and a SHA-256 fingerprint of its contents. Unbubble doesn't certify a record. Ask your attorney or the court what they require.
- **CSV:** timestamp with zone, date, time, sender, direction, type, text and attachment paths. Cells a spreadsheet would run as formulas are escaped.
- **CSV with attachments:** a zip of the CSV and a folder of the conversation's files.

## Building

Needs Xcode and [XcodeGen](https://github.com/yonaskolb/XcodeGen). The app needs iOS 26 or later.

```bash
xcodegen generate
open Unbubble.xcodeproj
```

Debug builds accept `-ub-token <token>`, which drives the receiver with scripted requests and no Shortcut behind it. The Simulator shares the Mac's loopback address, so a script on the Mac can play the part of the Shortcut.
