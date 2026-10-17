"""Score the recording reader against a fixture's ground truth.

    python tests/eval_recording.py FIXTURE_DIR [video]
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unbubble.recording import read_recording  # noqa: E402


_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]+")


def as_read(text: str) -> str:
    """What the reader can produce for a truth message: emoji become one "[Emoji]" marker."""
    return re.sub(r"\s+", " ", _EMOJI.sub(" [Emoji] ", text)).strip()


def score(fixture: Path, video: Path) -> dict:
    truth = json.loads((fixture / "truth.json").read_text(encoding="utf-8"))
    started = time.time()
    result = read_recording(video, recorded_at=datetime.fromisoformat(truth["recorded_at"]))
    took = time.time() - started
    want = truth["messages"]
    got = result.messages
    # Order-preserving alignment on (text, side).
    a = [f"{'M' if m['sender'] == 'Me' else 'T'}|{as_read(m['text']).lower()}" for m in want]
    b = [f"{'M' if m.from_me else 'T'}|{m.text.lower()}" for m in got]
    # Align on text alone, then judge side, sender and time on the aligned pairs.
    ta = [x.split("|", 1)[1] for x in a]
    tb = [x.split("|", 1)[1] for x in b]
    sm = SequenceMatcher(None, ta, tb, autojunk=False)
    exact = 0
    pairs = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            exact += i2 - i1
            pairs += [(i1 + k, j1 + k) for k in range(i2 - i1)]
        elif op == "replace":
            for i, j in zip(range(i1, i2), range(j1, j2)):      # near-identical OCR readings
                if SequenceMatcher(None, ta[i], tb[j]).ratio() >= 0.85:
                    pairs.append((i, j))
    near = len(pairs)
    side_ok = sum(1 for i, k in pairs if (want[i]["sender"] == "Me") == got[k].from_me)
    sender_ok = sum(1 for i, k in pairs if (want[i]["sender"] == got[k].sender) or (want[i]["sender"] != "Me" and not truth["group"]))
    # Times: each message's truth header time is the last header at/before it.
    time_ok = 0
    for i, k in pairs:
        t = got[k].ts
        w_at = datetime.fromisoformat(want[i]["at"])
        if t is not None and t <= w_at and (w_at - t).total_seconds() < 6 * 3600:
            time_ok += 1
    return {
        "truth": len(want), "read": len(got), "exact_in_order": exact, "near_match": near,
        "extra": len(got) - near, "side_ok": side_ok, "sender_ok": sender_ok, "time_ok": time_ok,
        "title": result.title, "group": result.is_group, "frames": result.frames,
        "key_frames": result.key_frames, "seconds": round(took, 1), "warnings": result.warnings,
    }


if __name__ == "__main__":
    fx = Path(sys.argv[1])
    video = Path(sys.argv[2]) if len(sys.argv) > 2 else fx / "recording.mp4"
    print(json.dumps(score(fx, video), indent=1))
