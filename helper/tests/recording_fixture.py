"""Synthetic iPhone Messages screen recordings with exact ground truth.

Renders an iOS Messages look-alike (light or dark, one-to-one or group) as one
tall HTML page, then `record_fixture.mjs` scrolls it in a real browser at
iPhone resolution the way a person flicks through a thread, and this module
stitches the frames into an MP4. The ground truth JSON is what the recording
reader must reproduce.

    python tests/recording_fixture.py OUT_DIR [--theme dark] [--group]
"""
from __future__ import annotations

import argparse
import html
import json
import random
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path

LINES_THEM = [
    "hey are you still up", "lol", "did you see what she posted", "I can't do this again tonight",
    "ok fine", "where are you", "Can we talk tomorrow? I don't want to fight over text",
    "you always say that 🙄", "I'm at my mom's until 6", "omg yes", "thank you for today, really",
    "Did you pay the electric bill? It's due on the 14th", "k", "I miss you",
    "Why didn't you call back", "We need to figure out the weekend, Sam has a game at 10:30",
    "that's not what I said and you know it", "haha ok", "sure", "Are you coming or not?",
    "I left the keys under the mat", "can you pick up milk + eggs on the way home",
    "Honestly I think we both need some space right now. Not forever, just a few days to think.",
]
LINES_ME = [
    "yeah still up", "No I didn't, what happened?", "I'm sorry", "Ok let's talk tomorrow",
    "On my way", "😂😂", "I did, paid it yesterday", "Love you", "I was driving",
    "That works for me", "I'll grab them", "Can you send me the address?",
    "I never said that. Please don't put words in my mouth", "Good night",
    "Running 10 min late", "Yes, I'll be there by 7", "thanks!!", "We'll figure it out",
    "Fine.", "I think that's fair. Let's just take the weekend.",
]
GROUP_PEOPLE = ["Jordan", "Alexis", "Sam"]


@dataclass
class Item:
    kind: str            # "time" | "msg"
    text: str
    sender: str = ""     # "Me" or a name, for messages
    at: str = ""         # ISO time the message was sent (minute precision)
    header: str = ""     # for "time" rows: the header text iOS shows


def ios_header(when: datetime, now: datetime) -> str:
    """The time header iOS Messages shows above a block of messages."""
    t = when.strftime("%-I:%M %p")
    days = (now.date() - when.date()).days
    if days == 0:
        return f"Today {t}"
    if days == 1:
        return f"Yesterday {t}"
    if days < 7:
        return f"{when.strftime('%A')} {t}"
    if when.year == now.year:
        return f"{when.strftime('%a, %b %-d')} at {t}"
    return f"{when.strftime('%b %-d, %Y')} at {t}"


def make_thread(seed: int, count: int, group: bool, now: datetime) -> list[Item]:
    rng = random.Random(seed)
    items: list[Item] = []
    when = now - timedelta(days=12, hours=rng.randint(0, 5))
    sender = "Me"
    for i in range(count):
        if i == 0 or rng.random() < 0.12:
            when += timedelta(hours=rng.randint(2, 40), minutes=rng.randint(0, 59))
            when = min(when, now - timedelta(minutes=5 * (count - i)))
            items.append(Item("time", "", header=ios_header(when, now), at=when.isoformat(timespec="minutes")))
        else:
            when += timedelta(minutes=rng.randint(0, 6))
        if rng.random() < 0.45:
            sender = "Me" if sender != "Me" else (rng.choice(GROUP_PEOPLE) if group else "Jordan")
        elif group and sender != "Me" and rng.random() < 0.3:
            sender = rng.choice(GROUP_PEOPLE)
        text = rng.choice(LINES_ME if sender == "Me" else LINES_THEM)
        items.append(Item("msg", text, sender=sender, at=when.isoformat(timespec="minutes")))
    return items


CSS = """
:root { --bg:#fff; --text:#000; --grey:#e9e9eb; --greytext:#000; --meta:#8e8e93; --blue:#0b84fe; --bar:rgba(249,249,249,.94); --hair:rgba(0,0,0,.12); }
.dark { --bg:#000; --text:#fff; --grey:#262629; --greytext:#fff; --meta:#8d8d93; --bar:rgba(22,22,24,.94); --hair:rgba(255,255,255,.12); }
* { box-sizing:border-box; margin:0; }
html, body { background:var(--bg); color:var(--text); font-family:"Inter", -apple-system, "Helvetica Neue", sans-serif; -webkit-font-smoothing:antialiased; }
body { width:393px; }
.status { position:fixed; top:0; left:0; right:0; height:54px; z-index:3; display:flex; justify-content:space-between; align-items:center; padding:0 30px 0 40px; font:600 17px Inter; background:var(--bar); }
.nav { position:fixed; top:54px; left:0; right:0; height:86px; z-index:3; background:var(--bar); border-bottom:.5px solid var(--hair); text-align:center; }
.nav .back { position:absolute; left:10px; top:22px; color:var(--blue); font-size:30px; line-height:1; }
.nav .avatar { width:46px; height:46px; border-radius:50%; margin:6px auto 4px; background:linear-gradient(#a7abb6,#858994); color:#fff; font:600 19px/46px Inter; }
.nav .name { font:400 12px Inter; color:var(--text); }
.thread { padding:150px 0 110px; }
.time { text-align:center; color:var(--meta); font:400 11.5px Inter; margin:18px 0 8px; }
.time b { font-weight:600; }
.row { display:flex; padding:0 14px; margin-top:2px; }
.row.first { margin-top:9px; }
.row.me { justify-content:flex-end; }
.who { color:var(--meta); font:400 11px Inter; margin:9px 0 2px 58px; }
.av { width:28px; height:28px; border-radius:50%; background:#9a9ea9; color:#fff; font:600 12px/28px Inter; text-align:center; margin-right:8px; align-self:flex-end; flex:none; }
.av.blank { background:transparent; }
.bubble { max-width:72%; padding:7px 12px 8px; border-radius:18px; font:400 17px/22px Inter; letter-spacing:-.2px; word-wrap:break-word; }
.me .bubble { background:var(--blue); color:#fff; }
.them .bubble { background:var(--grey); color:var(--greytext); }
.status-line { text-align:right; color:var(--meta); font:400 11px Inter; padding:3px 18px 0; }
.input { position:fixed; bottom:0; left:0; right:0; height:88px; z-index:3; background:var(--bar); display:flex; align-items:flex-start; padding:8px 12px; gap:10px; }
.input .plus { width:34px; height:34px; border-radius:50%; background:var(--grey); color:var(--meta); font:300 26px/32px Inter; text-align:center; }
.input .field { flex:1; height:36px; border:1px solid var(--hair); border-radius:18px; color:var(--meta); font:400 17px/34px Inter; padding:0 14px; }
"""


def render_html(items: list[Item], theme: str, group: bool) -> str:
    out = []
    title = "Family" if group else "Jordan"
    initials = "F" if group else "J"
    last_me = max((i for i, it in enumerate(items) if it.kind == "msg" and it.sender == "Me"), default=-1)
    prev_sender = None
    for i, it in enumerate(items):
        if it.kind == "time":
            day, _, rest = it.header.partition(" ")
            out.append(f'<div class="time"><b>{html.escape(day)}</b> {html.escape(rest)}</div>')
            prev_sender = None
            continue
        me = it.sender == "Me"
        first = it.sender != prev_sender
        nxt = next((x for x in items[i + 1:]), None)
        last_of_run = not (nxt and nxt.kind == "msg" and nxt.sender == it.sender)
        if group and not me and first:
            out.append(f'<div class="who">{html.escape(it.sender)}</div>')
        av = ""
        if group and not me:
            av = f'<div class="av">{it.sender[0]}</div>' if last_of_run else '<div class="av blank"></div>'
        cls = "me" if me else "them"
        out.append(f'<div class="row {cls}{" first" if first else ""}">{av}<div class="bubble">{html.escape(it.text)}</div></div>')
        if i == last_me:
            out.append('<div class="status-line">Delivered</div>')
        prev_sender = it.sender
    return f"""<!doctype html><html class="{theme}"><head><meta charset="utf-8"><style>{CSS}</style></head>
<body><div class="status"><span>9:41</span><span>▂▄▆ ◔ ▮</span></div>
<div class="nav"><div class="back">‹</div><div class="avatar">{initials}</div><div class="name">{title} ›</div></div>
<div class="thread">{''.join(out)}</div>
<div class="input"><div class="plus">+</div><div class="field">iMessage</div></div></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--theme", default="light", choices=["light", "dark"])
    ap.add_argument("--group", action="store_true")
    ap.add_argument("--count", type=int, default=70)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    now = datetime(2026, 10, 16, 21, 30)
    items = make_thread(args.seed, args.count, args.group, now)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "chat.html").write_text(render_html(items, args.theme, args.group), encoding="utf-8")
    truth = {
        "recorded_at": now.isoformat(timespec="minutes"),
        "title": "Family" if args.group else "Jordan",
        "group": args.group,
        "messages": [asdict(it) for it in items if it.kind == "msg"],
        "headers": [it.header for it in items if it.kind == "time"],
    }
    (args.out / "truth.json").write_text(json.dumps(truth, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(truth['messages'])} messages, {len(truth['headers'])} time headers -> {args.out}")


if __name__ == "__main__":
    main()
