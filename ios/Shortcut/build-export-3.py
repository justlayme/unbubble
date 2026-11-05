#!/usr/bin/env python3
"""Unbubble Export 3: the published Unbubble Export 2 (published/unbubble-export-2.plist,
the iCloud copy), changed only in the Recent chats list.

Recent chats asked Messages for the 500 most recently active conversations with no
filter. On an iPhone with a long history (the owner's iPhone 15 on iOS 27, 1,874 chats)
that Find fails before anything reaches the app, while Search by name (at most 50,
filtered by name) works (2026-11-05). Recent chats now lists the 50 most recent; an
older chat is found by name. The name it restarts itself by becomes Unbubble Export 3:
a new name keeps run-by-name exact, since a second same-named copy makes Shortcuts run
neither.

Usage: build-export-3.py <out.shortcut>   (unsigned; sign with `shortcuts sign`)
"""
import copy, os, plistlib, sys

HERE = os.path.dirname(os.path.abspath(__file__))
src = plistlib.load(open(os.path.join(HERE, 'published', 'unbubble-export-2.plist'), 'rb'))
out = copy.deepcopy(src)
A = out['WFWorkflowActions']
RECENT_LIMIT = 50

def params(i): return A[i]['WFWorkflowActionParameters']

RECENT, RELAUNCH = 36, 155
assert params(RECENT - 1).get('WFMenuItemTitle') == 'Recent chats', 'menu moved'
assert A[RECENT]['WFWorkflowActionIdentifier'] == 'com.apple.MobileSMS.ConversationEntity', 'Recent Find moved'
assert params(RECENT)['WFContentItemLimitNumber'] == 500 and 'WFContentItemFilter' not in params(RECENT), 'Recent Find changed'
params(RECENT)['WFContentItemLimitNumber'] = RECENT_LIMIT

link = params(RELAUNCH)['WFTextActionText']['Value']
old, new = 'name=Unbubble%20Export%202&', 'name=Unbubble%20Export%203&'
assert link['string'].count(old) == 1 and len(old) == len(new), 'relaunch link moved'
link['string'] = link['string'].replace(old, new)  # same length: the attachment range holds

flat = plistlib.dumps(out).decode('utf-8', 'replace')
assert 'Export%202' not in flat, 'a reference to Export 2 is left'

with open(sys.argv[1], 'wb') as handle:
    plistlib.dump(out, handle, fmt=plistlib.FMT_BINARY)
print(f'wrote {sys.argv[1]}: {len(A)} actions, Recent chats limit {RECENT_LIMIT}')
