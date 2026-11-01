# Unbubble for iPhone

Export a conversation from Messages on an iPhone, with no computer, as a PDF or a CSV, with its photos and files if you want them. Free and open source.

## How it works

Apple doesn't let apps read Messages, but it does let a Shortcut you approve. So Unbubble has two parts:

- **The app** (`Sources/`) listens on the iPhone's loopback address, `127.0.0.1:47821`, for the current export only, using a random token it hands the Shortcut.
- **The Shortcut** (`Shortcut/Unbubble Export.plist`, or [add it from iCloud](https://www.icloud.com/shortcuts/0e0ba46174fc41ab8ba55d1cb21ead01)) is a plain loop. It asks the app which stretch of time to read, runs Find Messages over it and hands back what it found.

Find Messages can't filter by conversation and stops at about 500 results, so the app steers: it sizes each stretch from how busy the last one was, narrows and rereads one that comes back near the limit, and keeps only the chosen conversation. Everything else is dropped on arrival. A Shortcut run's memory only grows, so the app also tells it when to restart itself and carry on from the same place.

Nothing leaves the phone. See [PRIVACY.md](PRIVACY.md).

## Output

- **PDF:** a printable record with a cover page, dates, times, senders, numbered messages and pages, photos, and a SHA-256 fingerprint of its contents. Unbubble doesn't certify a record. Ask your attorney or the court what they require.
- **CSV:** timestamp with zone, date, time, sender, direction, type, text and attachment paths. Cells a spreadsheet would run as formulas are escaped.
- **CSV with attachments:** a zip of the CSV and a folder of the conversation's files.

## Building

Needs Xcode and [XcodeGen](https://github.com/yonaskolb/XcodeGen). iOS 26 or later.

```bash
xcodegen generate
open Unbubble.xcodeproj
```

Debug builds accept `-ub-token <token>`, which drives the receiver with scripted requests and no Shortcut behind it. The Simulator shares the Mac's loopback address, so a script on the Mac can play the part of the Shortcut.
