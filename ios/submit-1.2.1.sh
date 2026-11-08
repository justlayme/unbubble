#!/bin/zsh
# Unbubble iOS 1.2.1 build 9: the app runs Unbubble Export 4, whose restart never asks
# which chat to read, and the finished screen makes the case for The Grey Mirror. Build 8
# (the fixed Export 3) was in review; this cancels it. Export 3 looked the chat up again by its exact
# name on every restart and showed Recent chats / Search by name when that failed, which
# stopped the export of a chat saved under a phone number. The fixed one carries on with
# the name it was given and escapes "+" and "&" in its restart link. Same name.
#
#   --dry-run   archive, export and validate only (allowed before the iCloud link exists)
#   --upload    also upload and wait for processing, then stop
#   --prepare   also create the version and attach the build, then stop before
#               the review submission
set -u
# Xcode's IPA export expects Apple's rsync, not Homebrew's.
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
cd "$(dirname "$0")"
DRY=0; [[ "${1:-}" == "--dry-run" ]] && DRY=1
PREPARE=0; [[ "${1:-}" == "--prepare" ]] && PREPARE=1
[[ "${1:-}" == "--upload" ]] && PREPARE=2
VERSION=1.2.1
BUILD=9
OUT=build/v121-b$BUILD
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
KEY=(--apiKey 43V66WY2W9 --apiIssuer 216cb34c-1d5e-47e5-bef9-a0bf617b1f79)
fail() { echo "FAILED: $*"; exit 1; }

OS_BUILD=$(sw_vers -buildVersion)
if [[ "$OS_BUILD" =~ [0-9][a-z]$ ]]; then
  if (( DRY )); then echo "note: macOS $OS_BUILD is a pre-release build — fine for a dry run, refused for upload"
  else fail "macOS $OS_BUILD is a pre-release build; Apple rejects binaries built on it (ITMS-90111)."; fi
fi
xcodebuild -version | grep -qi beta && fail "active Xcode is a beta: $(xcodebuild -version | tail -1)"
if grep -q 'UNBUBBLE_EXPORT_4_LINK' Sources/Engine/ExportEngine.swift; then (( DRY )) && echo "note: Shortcut link is a placeholder (dry run only)" || fail "the Shortcut link is still a placeholder"; fi

sed -i '' "s/CURRENT_PROJECT_VERSION: \"[0-9]*\"/CURRENT_PROJECT_VERSION: \"$BUILD\"/; s/MARKETING_VERSION: \"[0-9.]*\"/MARKETING_VERSION: \"$VERSION\"/" project.yml
xcodegen generate >/dev/null || fail xcodegen
if [[ -f "$OUT/uploaded" ]]; then echo "  build $BUILD already built and uploaded — skipping to App Store Connect"
else
rm -rf "$OUT" && mkdir -p "$OUT"
xcodebuild -project Unbubble.xcodeproj -scheme Unbubble -configuration Release \
  -destination 'generic/platform=iOS' -archivePath "$OUT/Unbubble.xcarchive" \
  -allowProvisioningUpdates archive > "$OUT/archive.log" 2>&1 || fail "archive (see $OUT/archive.log)"
P="$OUT/Unbubble.xcarchive/Products/Applications/Unbubble.app/Info.plist"
for k in CFBundleShortVersionString CFBundleVersion DTSDKName DTXcodeBuild BuildMachineOSBuild MinimumOSVersion; do
  echo "  $k = $(/usr/libexec/PlistBuddy -c "Print :$k" "$P")"
done
xcodebuild -exportArchive -archivePath "$OUT/Unbubble.xcarchive" -exportOptionsPlist build/export.plist \
  -exportPath "$OUT/export" -allowProvisioningUpdates > "$OUT/export.log" 2>&1 || fail "export (see $OUT/export.log)"
IPA="$OUT/export/Unbubble.ipa"
xcrun altool --validate-app -f "$IPA" -t ios "${KEY[@]}" 2>&1 | tail -3
fi
IPA="$OUT/export/Unbubble.ipa"
(( DRY )) && { echo "DRY RUN COMPLETE — archive, export and validation passed"; exit 0; }

if [[ -f "$OUT/uploaded" ]]; then echo "  build $BUILD already uploaded"
else xcrun altool --upload-app -f "$IPA" -t ios "${KEY[@]}" 2>&1 | tail -3 || fail upload; touch "$OUT/uploaded"; fi
python3 - "$BUILD" "$PREPARE" "$VERSION" <<'PY'
import sys, time
sys.path.insert(0, __import__('os').path.expanduser('~/.appstoreconnect'))
import asc
build_no, mode, VERSION = sys.argv[1], sys.argv[2], sys.argv[3]
APP = '6829522001'  # Unbubble: iMessage Exporter (asc.APP_ID is Grey Mirror)
for _ in range(60):
    b = asc.get(f'builds?filter[app]={APP}&filter[version]={build_no}&fields[builds]=version,processingState')['data']
    state = b[0]['attributes']['processingState'] if b else 'NOT_YET'
    print('  build', build_no, state)
    if state == 'VALID': break
    if state in ('FAILED', 'INVALID'): raise SystemExit('build processing failed')
    time.sleep(60)
else:
    raise SystemExit('build never became VALID')
build_id = b[0]['id']
if mode == '2':
    print('  UPLOADED — build', build_no, 'is processed; App Store Connect is otherwise untouched')
    raise SystemExit(0)
WHATS_NEW = "Long exports no longer stop partway to ask which chat to read. Unbubble now uses the new Unbubble Export 4 Shortcut: tap Add Shortcut once in the app and you are set. The finished screen also shows what The Grey Mirror, from the same makers, can tell you about the conversation."
existing = [v for v in asc.get(f'apps/{APP}/appStoreVersions?limit=10')['data'] if v['attributes']['versionString'] == VERSION]
version = existing[0] if existing else asc.send('POST', 'appStoreVersions', {'data': {'type': 'appStoreVersions',
    'attributes': {'platform': 'IOS', 'versionString': VERSION},
    'relationships': {'app': {'data': {'type': 'apps', 'id': APP}}}}})['data']
stale = [s for s in asc.get(f'apps/{APP}/reviewSubmissions?limit=10')['data']
         if s['attributes']['state'] in ('UNRESOLVED_ISSUES', 'WAITING_FOR_REVIEW') and s['attributes']['submittedDate']]
for sub in stale:
    asc.send('PATCH', f"reviewSubmissions/{sub['id']}", {'data': {'type': 'reviewSubmissions', 'id': sub['id'], 'attributes': {'canceled': True}}})
    print('  cancelling submission', sub['id'][:8])
for sub in stale:
    for _ in range(40):
        if asc.get(f"reviewSubmissions/{sub['id']}")['data']['attributes']['state'] == 'COMPLETE': break
        time.sleep(15)
for loc in asc.get(f"appStoreVersions/{version['id']}/appStoreVersionLocalizations")['data']:
    asc.send('PATCH', f"appStoreVersionLocalizations/{loc['id']}", {'data': {'type': 'appStoreVersionLocalizations', 'id': loc['id'], 'attributes': {'whatsNew': WHATS_NEW}}})
OLD_LINK = 'https://www.icloud.com/shortcuts/0e0ba46174fc41ab8ba55d1cb21ead01'
EXPORT2_LINK = 'https://www.icloud.com/shortcuts/780a4fd168904283ab8ccf0f2bcdf3e4'
NEW_LINK = 'https://www.icloud.com/shortcuts/f541b52427274f3895f7217b882f6b46'
EXPORT3_LINK = 'https://www.icloud.com/shortcuts/bf241069f92f4928991cbb8d7e045060'
IOS26_NOTE = ('\n\nNew in 1.1: the Shortcut is now "Unbubble Export 2", so it works on iOS 26 as well as iOS 27. '
              'On iOS 26 Apple\'s Shortcuts cannot read a conversation\'s participants, so there the app recognises a group chat by its senders '
              'and asks the user to pick a one-to-one conversation instead.')
detail = asc.get(f"appStoreVersions/{version['id']}/appStoreReviewDetail")['data']
notes = detail['attributes'].get('notes') or ''
new_notes = notes.replace('https://www.icloud.com/shortcuts/62846d33525e465a8cfd9d284204400e', NEW_LINK).replace(OLD_LINK, NEW_LINK).replace(EXPORT2_LINK, NEW_LINK).replace(EXPORT3_LINK, NEW_LINK).replace('iOS opens the Shortcut: tap Add Shortcut', 'iOS opens the Shortcut, Unbubble Export 3: tap Add Shortcut').replace('Unbubble Export 2', 'Unbubble Export 4').replace('Unbubble Export 3', 'Unbubble Export 4')
if 'New in 1.1' not in new_notes: new_notes += IOS26_NOTE
if new_notes != notes:
    asc.send('PATCH', f"appStoreReviewDetails/{detail['id']}", {'data': {'type': 'appStoreReviewDetails', 'id': detail['id'], 'attributes': {'notes': new_notes[:3990]}}})
    print('  review notes updated')
assert NEW_LINK in asc.get(f"appStoreReviewDetails/{detail['id']}")['data']['attributes']['notes'], 'review notes lack the new Shortcut link'
print('  version', VERSION, version['id'][:8], 'ready')
asc.send('PATCH', f"appStoreVersions/{version['id']}/relationships/build", {'data': {'type': 'builds', 'id': build_id}})
print('  attached build', build_no, 'to', VERSION)
if mode == '1':
    print('  PREPARED — version', VERSION, 'has build', build_no, 'attached; run again without a flag to submit')
    raise SystemExit(0)
drafts = [s for s in asc.get(f'apps/{APP}/reviewSubmissions?limit=10')['data']
          if s['attributes']['state'] == 'READY_FOR_REVIEW' and not s['attributes']['submittedDate']]
sub = drafts[0] if drafts else asc.send('POST', 'reviewSubmissions', {'data': {'type': 'reviewSubmissions', 'attributes': {'platform': 'IOS'},
      'relationships': {'app': {'data': {'type': 'apps', 'id': APP}}}}})['data']
try:
    asc.send('POST', 'reviewSubmissionItems', {'data': {'type': 'reviewSubmissionItems',
        'relationships': {'reviewSubmission': {'data': {'type': 'reviewSubmissions', 'id': sub['id']}},
                          'appStoreVersion': {'data': {'type': 'appStoreVersions', 'id': version['id']}}}}})
except RuntimeError as error:
    if 'ALREADY' not in str(error).upper() and 'ANOTHER_SUBMISSION' not in str(error).upper(): raise
print('  items in submission:', len(asc.get(f"reviewSubmissions/{sub['id']}/items")['data']))
asc.send('PATCH', f"reviewSubmissions/{sub['id']}", {'data': {'type': 'reviewSubmissions', 'id': sub['id'], 'attributes': {'submitted': True}}})
print('  SUBMITTED', sub['id'])
PY
