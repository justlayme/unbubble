#!/usr/bin/env python3
"""Unbubble Export 2 and 3, fixed in place: build-export-4.py's restart fix under the
names the apps already run (1.1 runs "Unbubble Export 2", 1.2 runs "Unbubble Export 3"),
so no app needs a new build for it.

A restarted run no longer asks "Which conversation should Unbubble export?" (owner,
2026-11-08: the export stopped at every restart): the restart link puts its input where
its placeholder is, and a run with a second input line never asks. Both get Export 3's
50-chat Recent list too.

Replace, don't add: two same-named Shortcuts make run-by-name run neither.

Usage: build-export-fixed.py 2|3|4 <out.plist>   (unsigned; sign with `shortcuts sign`)
"""
import os, plistlib, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
n, out = sys.argv[1], sys.argv[2]
assert n in ('2', '3', '4'), n
with tempfile.TemporaryDirectory() as tmp:
    built = os.path.join(tmp, '4.plist')
    subprocess.run([sys.executable, os.path.join(HERE, 'build-export-4.py'), built], check=True)
    sc = plistlib.load(open(built, 'rb'))

old, new = 'name=Unbubble%20Export%204&', f'name=Unbubble%20Export%20{n}&'
hits = 0
for a in sc['WFWorkflowActions']:
    t = a['WFWorkflowActionParameters'].get('WFTextActionText', {}).get('Value', {})
    if isinstance(t, dict) and old in t.get('string', ''):
        t['string'] = t['string'].replace(old, new)
        # The token and chat go where the placeholder is. Export 2 lengthened the name
        # ("Unbubble%20Export" -> "...%202", 4 characters) and left the range at 64, so
        # since Export 2 every restart link was garbled: the restarted run got no input
        # and asked "Which conversation should Unbubble export?" (owner, 2026-11-08).
        # Ranges count UTF-16 units.
        (attachment,) = t['attachmentsByRange'].values()
        at = len(t['string'][:t['string'].index('\ufffc')].encode('utf-16-le')) // 2
        t['attachmentsByRange'] = {f'{{{at}, 1}}': attachment}
        hits += 1
assert hits == 1, hits
flat = plistlib.dumps(sc).decode('utf-8', 'replace')
assert n == '4' or ('Export%204' not in flat and 'Export 4' not in flat), 'a reference to Export 4 is left'
assert flat.count(f'Export%20{n}') == 1

plistlib.dump(sc, open(out, 'wb'), fmt=plistlib.FMT_BINARY)
print(f'Unbubble Export {n} (fixed): {len(sc["WFWorkflowActions"])} actions, restarts as Export {n}')
