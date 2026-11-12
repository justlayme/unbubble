#!/usr/bin/env python3
"""Unbubble Export 4: "Unbubble Export 3.plist" (the iCloud copy of Export 3,
https://www.icloud.com/shortcuts/bf241069f92f4928991cbb8d7e045060), changed only in
how a restarted run finds its chat again.

Every so often a run reopens itself (shortcuts://run-shortcut, input "token<newline>
chat name") because only the end of a run frees memory, and the app reopens it the same
way after a pause. The new run looked the chat up with Find Conversation "Display Name
is <name>" and, unless exactly one chat came back, showed "Recent chats / Search by
name", which stops the export. Grey Mirror's Shortcut, built from the same template, did
exactly this to chats named by a phone number ("+1 (…) …-…") on 2026-11-07, while chats
named with words restarted cleanly: Shortcuts' URL Encode can leave "+" and "&" as they
are, and Messages' name filter may not match a number the way it prints it.

Export 4:
  1. Escapes "+" and "&" in its restart link after URL Encode (the app's own link already
     escapes "+").
  2. On a restart never asks which chat: when the lookup does not return exactly one chat
     it carries on with the name it was given and skips the recipients and last-active
     reads. The app keeps the chat, its recipients and its start day from the first run
     (ExportEngine.begin only takes them while progress.chat is empty).
  3. Continues past an attachment whose upload fails, so one bad file cannot stop the
     attachment stretch before it reports completion to the app.
The name it restarts itself by becomes Unbubble Export 4.

Usage: build-export-4.py <out.shortcut>   (unsigned; sign with `shortcuts sign`)
"""
import copy, os, plistlib, sys, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
src = plistlib.load(open(os.path.join(HERE, 'published', 'unbubble-export-3.plist'), 'rb'))
A = src['WFWorkflowActions']
OBJ = '￼'
IS, HAS_VALUE, NO_VALUE = 4, 100, 101


def U(): return str(uuid.uuid4()).upper()
def action(ident, params): return {'WFWorkflowActionIdentifier': ident, 'WFWorkflowActionParameters': {'UUID': U(), **params}}
def uid(a): return a['WFWorkflowActionParameters']['UUID']
def output(a, name): return {'Value': {'OutputUUID': uid(a), 'Type': 'ActionOutput', 'OutputName': name}, 'WFSerializationType': 'WFTextTokenAttachment'}
def output_text(a, name): return {'Value': {'string': OBJ, 'attachmentsByRange': {'{0, 1}': {'OutputUUID': uid(a), 'Type': 'ActionOutput', 'OutputName': name}}}, 'WFSerializationType': 'WFTextTokenString'}
def setvar(a, name, var): return action('is.workflow.actions.setvariable', {'WFInput': output(a, name), 'WFVariableName': var})
def text(s): return {'Value': {'string': s}, 'WFSerializationType': 'WFTextTokenString'}
def var_text(name): return {'Value': {'string': OBJ, 'attachmentsByRange': {'{0, 1}': {'VariableName': name, 'Type': 'Variable'}}}, 'WFSerializationType': 'WFTextTokenString'}
def var_input(name): return {'Type': 'Variable', 'Variable': {'Value': {'VariableName': name, 'Type': 'Variable'}, 'WFSerializationType': 'WFTextTokenAttachment'}}
def set_text(value, name):
    t = action('is.workflow.actions.gettext', {'WFTextActionText': value if isinstance(value, dict) else text(value)})
    return [t, setvar(t, 'Text', name)]
def set_number(value, name):
    n = action('is.workflow.actions.number', {'WFNumberActionNumber': str(value)})
    return [n, setvar(n, 'Number', name)]
def if_block(variable, condition, then, otherwise=None):
    g = U()
    head = action('is.workflow.actions.conditional', {'GroupingIdentifier': g, 'WFControlFlowMode': 0, 'WFCondition': condition, 'WFInput': var_input(variable)})
    middle = ([action('is.workflow.actions.conditional', {'GroupingIdentifier': g, 'WFControlFlowMode': 1})] + otherwise) if otherwise is not None else []
    return [head] + then + middle + [action('is.workflow.actions.conditional', {'GroupingIdentifier': g, 'WFControlFlowMode': 2})]
def params(i): return A[i]['WFWorkflowActionParameters']
def ident(i): return A[i]['WFWorkflowActionIdentifier']
def name_of(i): return params(i).get('WFVariableName')


# The parts being changed, checked against Export 3 so a different source fails loudly.
CHAT_NOTHING, PICKED_INIT = 12, 29
CHAT_NAME = (67, 68)
RECIPIENTS = range(73, 89)
LAST_ACTIVE = (89, 90)
ENCODE, RELAUNCH = 154, 155
assert name_of(CHAT_NOTHING) == 'Chat' and ident(CHAT_NOTHING - 1).endswith('nothing'), 'Chat init moved'
assert name_of(17) == 'ResumeName' and ident(18) == 'com.apple.MobileSMS.ConversationEntity', 'resume lookup moved'
assert params(18)['WFContentItemFilter']['Value']['WFActionParameterFilterTemplates'][0]['Operator'] == IS, 'resume lookup changed'
assert params(27)['WFCondition'] == NO_VALUE and params(27)['WFInput']['Variable']['Value']['VariableName'] == 'Chat', 'ask-if moved'
assert name_of(PICKED_INIT) == 'Picked' and params(PICKED_INIT - 1).get('WFNumberActionNumber') == '0', 'Picked init moved'
assert params(34).get('WFMenuItems') == ['Recent chats', 'Search by name'], 'menu moved'
assert ident(CHAT_NAME[0]).endswith('gettext') and name_of(CHAT_NAME[1]) == 'ChatName', 'ChatName moved'
assert params(69)['WFCondition'] == NO_VALUE and ident(71).endswith('exit'), 'no-chat check moved'
assert params(73).get('WFConditionalActionString', {}).get('Value', {}).get('string') == 'x26', 'recipients moved'
assert params(88).get('WFControlFlowMode') == 2 and params(88)['GroupingIdentifier'] == params(73)['GroupingIdentifier'], 'recipients block moved'
assert name_of(LAST_ACTIVE[1]) == 'ChatLastActive', 'last active moved'
assert ident(ENCODE).endswith('urlencode') and ident(RELAUNCH).endswith('gettext'), 'relaunch moved'

# A run is a restart when its input has a second line (the chat's name). Counted, never
# tested for "has any value": Shortcuts counts an empty Text as a value, so a check on an
# empty ResumeName skipped "which chat" on every fresh run (owner's iPhone, 2026-11-08).
GREATER = 2
assert name_of(14) == 'InputCount' and params(15)['WFCondition'] == GREATER and params(15)['WFNumberValue'] == '1', 'InputCount moved'
def when(variable, condition, then, otherwise=None, **extra):
    g = U()
    head = action('is.workflow.actions.conditional', {'GroupingIdentifier': g, 'WFControlFlowMode': 0, 'WFCondition': condition, 'WFInput': var_input(variable), **extra})
    middle = ([action('is.workflow.actions.conditional', {'GroupingIdentifier': g, 'WFControlFlowMode': 1})] + otherwise) if otherwise is not None else []
    return [head] + then + middle + [action('is.workflow.actions.conditional', {'GroupingIdentifier': g, 'WFControlFlowMode': 2})]
restart = {'WFNumberValue': '1'}
resume_init = set_text('', 'ResumeName')
skip_ask = when('InputCount', GREATER, set_number(1, 'Picked'), **restart)
# On a restart the name it was given is the chat's name, found again or not.
chat_name = ([copy.deepcopy(A[i]) for i in CHAT_NAME] + set_text('0', 'NameOnly')
             + when('InputCount', GREATER, set_text(var_text('ResumeName'), 'ChatName') + set_text('1', 'NameOnly'), **restart))
chat_reads = (set_text('', 'RecipientText') + set_number(0, 'RecipientCount') + set_text('', 'ChatLastActive')
              + when('NameOnly', IS, [],
                         [copy.deepcopy(A[i]) for i in RECIPIENTS] + [copy.deepcopy(A[i]) for i in LAST_ACTIVE], WFConditionalActionString=text('1')))
plus = action('is.workflow.actions.text.replace', {'WFInput': output_text(A[ENCODE], 'URL Encoded Text'), 'WFReplaceTextFind': '+', 'WFReplaceTextReplace': '%2B',
                                                   'WFReplaceTextRegularExpression': False, 'WFReplaceTextCaseSensitive': True})
amp = action('is.workflow.actions.text.replace', {'WFInput': output_text(plus, 'Updated Text'), 'WFReplaceTextFind': '&', 'WFReplaceTextReplace': '%26',
                                                  'WFReplaceTextRegularExpression': False, 'WFReplaceTextCaseSensitive': True})

out = []
file_uploads = 0
for i, a in enumerate(A):
    if i in RECIPIENTS or i in LAST_ACTIVE or i in CHAT_NAME:
        if i == CHAT_NAME[0]: out += chat_name
        if i == RECIPIENTS.start: out += chat_reads
        continue
    a = copy.deepcopy(a)
    p = a['WFWorkflowActionParameters']
    if (a['WFWorkflowActionIdentifier'] == 'is.workflow.actions.downloadurl'
            and p.get('WFHTTPMethod') == 'POST' and p.get('WFHTTPBodyType') == 'File'
            and '/file' in p.get('WFURL', {}).get('Value', {}).get('string', '')):
        a['WFActionContinueAfterError'] = True
        file_uploads += 1
    if i == RELAUNCH:
        link = a['WFWorkflowActionParameters']['WFTextActionText']['Value']
        old, new = 'name=Unbubble%20Export%203&', 'name=Unbubble%20Export%204&'
        assert link['string'].count(old) == 1 and len(old) == len(new), 'relaunch link moved'
        link['string'] = link['string'].replace(old, new)  # same length: the attachment range holds
        (attachment,) = link['attachmentsByRange'].values()
        assert attachment['OutputUUID'] == uid(A[ENCODE]), 'relaunch link no longer uses URL Encode'
        attachment.update({'OutputUUID': uid(amp), 'OutputName': 'Updated Text'})
    out.append(a)
    if i == CHAT_NOTHING: out += resume_init
    if i == PICKED_INIT: out += skip_ask
    if i == ENCODE: out += [plus, amp]

assert file_uploads == 1, f'expected one attachment upload, found {file_uploads}'
sc = {k: copy.deepcopy(v) for k, v in src.items() if k != 'WFWorkflowActions'}
sc['WFWorkflowActions'] = out
flat = plistlib.dumps(sc).decode('utf-8', 'replace')
assert 'Export%203' not in flat and 'Export 3' not in flat, 'a reference to 3 is left'
with open(sys.argv[1], 'wb') as handle:
    plistlib.dump(sc, handle, fmt=plistlib.FMT_BINARY)
print(f'Unbubble Export 4: {len(out)} actions (3 had {len(A)})')
