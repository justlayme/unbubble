#!/usr/bin/env python3
"""Unbubble Export 2: "Unbubble Export.plist", changed only where iOS 26 fails.

On iOS 26 Shortcuts stops with "There was a problem running" when it reads a
conversation's recipients (seen on an iPhone 11 on iOS 26.6.3, 2026-11-02: every
other step this Shortcut takes ran). So on iOS 26, and only there, Unbubble Export 2
skips the two blocks that read them: the group check after the pick, and the
recipient list sent to the app. The app then catches a group chat from its senders
(ExportEngine.keep). On iOS 27 nothing changes. The new name keeps run-by-name exact:
adding a same-named copy leaves two, and Shortcuts then runs neither.

Usage: build-export-2.py <out.shortcut>   (unsigned; sign with `shortcuts sign`)
"""
import copy, os, plistlib, sys, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
src = plistlib.load(open(os.path.join(HERE, 'Unbubble Export.plist'), 'rb'))
A = src['WFWorkflowActions']
OBJ = '￼'
OLD, NEW = 'Unbubble%20Export', 'Unbubble%20Export%202'

def U(): return str(uuid.uuid4()).upper()
def action(ident, params): return {'WFWorkflowActionIdentifier': ident, 'WFWorkflowActionParameters': {'UUID': U(), **params}}
def uid(a): return a['WFWorkflowActionParameters']['UUID']
def output(a, name): return {'Value': {'OutputUUID': uid(a), 'Type': 'ActionOutput', 'OutputName': name}, 'WFSerializationType': 'WFTextTokenAttachment'}
def setvar(a, name, var): return action('is.workflow.actions.setvariable', {'WFInput': output(a, name), 'WFVariableName': var})
def text(s): return {'Value': {'string': s}, 'WFSerializationType': 'WFTextTokenString'}

def if_ios26(then, otherwise):
    g = U()
    return ([action('is.workflow.actions.conditional', {'GroupingIdentifier': g, 'WFControlFlowMode': 0, 'WFCondition': 4,
                'WFConditionalActionString': text('x26'),
                'WFInput': {'Type': 'Variable', 'Variable': {'Value': {'VariableName': 'OSTag', 'Type': 'Variable'}, 'WFSerializationType': 'WFTextTokenAttachment'}}})]
            + then + [action('is.workflow.actions.conditional', {'GroupingIdentifier': g, 'WFControlFlowMode': 1})]
            + otherwise + [action('is.workflow.actions.conditional', {'GroupingIdentifier': g, 'WFControlFlowMode': 2})])

GROUP_CHECK = range(44, 51)
RECIPIENTS = range(63, 72)
assert A[44]['WFWorkflowActionIdentifier'].endswith('.count') and A[50]['WFWorkflowActionIdentifier'].endswith('.conditional'), 'group check moved'
assert A[63]['WFWorkflowActionParameters'].get('WFVariableName') == 'Recipients', 'recipients block moved'
assert A[71]['WFWorkflowActionParameters'].get('WFVariableName') == 'RecipientText', 'recipients block moved'

dd = action('is.workflow.actions.getdevicedetails', {'WFDeviceDetail': 'System Version'})
sp = action('is.workflow.actions.text.split', {'text': output(dd, 'Device Details'), 'WFTextSeparator': 'Custom', 'WFTextCustomSeparator': '.'})
fi = action('is.workflow.actions.getitemfromlist', {'WFInput': output(sp, 'Split Text'), 'WFItemSpecifier': 'First Item'})
gt = action('is.workflow.actions.gettext', {'WFTextActionText': {'Value': {'string': 'x' + OBJ, 'attachmentsByRange': {'{1, 1}': {'OutputUUID': uid(fi), 'Type': 'ActionOutput', 'OutputName': 'Item from List'}}}, 'WFSerializationType': 'WFTextTokenString'}})
os_tag = [dd, sp, fi, gt, setvar(gt, 'Text', 'OSTag')]
one = action('is.workflow.actions.number', {'WFNumberActionNumber': '1'})
accept_pick = [one, setvar(one, 'Number', 'Picked')]
empty = action('is.workflow.actions.gettext', {'WFTextActionText': text('')})
zero = action('is.workflow.actions.number', {'WFNumberActionNumber': '0'})
no_recipients = [empty, setvar(empty, 'Text', 'RecipientText'), zero, setvar(zero, 'Number', 'RecipientCount')]

out = []
for i, a in enumerate(A):
    if i == 6:
        out += os_tag
    if i == GROUP_CHECK.start:
        out += if_ios26(accept_pick, [copy.deepcopy(A[j]) for j in GROUP_CHECK])
    if i == RECIPIENTS.start:
        out += if_ios26(no_recipients, [copy.deepcopy(A[j]) for j in RECIPIENTS])
    if i in GROUP_CHECK or i in RECIPIENTS:
        continue
    out.append(copy.deepcopy(a))

renamed = 0
for a in out:
    t = a['WFWorkflowActionParameters'].get('WFTextActionText', {}).get('Value', {})
    if isinstance(t, dict) and OLD in t.get('string', '') and NEW not in t.get('string', ''):
        t['string'] = t['string'].replace(OLD, NEW); renamed += 1
assert renamed == 1, renamed

sc = {k: copy.deepcopy(v) for k, v in src.items() if k != 'WFWorkflowActions'}
sc['WFWorkflowActions'] = out
plistlib.dump(sc, open(sys.argv[1], 'wb'), fmt=plistlib.FMT_BINARY)
print(f'Unbubble Export 2: {len(out)} actions (1 had {len(A)})')
