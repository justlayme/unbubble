#!/bin/zsh
# Finishes Unbubble 1.2.1: takes the iCloud link of Unbubble Export 4 (made on this Mac
# or a phone with Share > Copy iCloud Link), checks the shared copy is Export 4 as built
# (name, action count, the restart link it opens), writes the link into the app and the
# review notes, then runs submit-1.2.1.sh (pass --prepare or --upload to stop early).
# Run it in Terminal on this Mac (signing needs the logged-in session):
#   ./finish-export-4.sh https://www.icloud.com/shortcuts/<32 hex> [--prepare|--upload]
set -eu
cd "$(dirname "$0")"
LINK="${1:-}"; [[ $# -gt 0 ]] && shift
ID="${LINK##*/}"
[[ "$LINK" =~ '^https://www\.icloud\.com/shortcuts/[0-9a-f]{32}$' ]] || { echo "not an iCloud Shortcut link: $LINK"; exit 1; }
TMP=$(mktemp -d)
curl -sf "https://www.icloud.com/shortcuts/api/records/$ID" -o "$TMP/rec.json"
python3 - "$TMP" <<'PY'
import json, plistlib, sys, urllib.request, os
rec = json.load(open(os.path.join(sys.argv[1], "rec.json")))
name = rec["fields"]["name"]["value"]
assert name == "Unbubble Export 4", f"that link is {name!r}, not Unbubble Export 4"
A = plistlib.loads(urllib.request.urlopen(rec["fields"]["shortcut"]["value"]["downloadURL"]).read())["WFWorkflowActions"]
flat = json.dumps(A, default=str)
assert len(A) == 239, f"{len(A)} actions, the build has 239"
assert "name=Unbubble%20Export%204&" in flat and "%2B" in flat, "restart link is not Export 4"
uploads = [a for a in A if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.downloadurl"
           and a["WFWorkflowActionParameters"].get("WFHTTPMethod") == "POST"
           and a["WFWorkflowActionParameters"].get("WFHTTPBodyType") == "File"
           and "/file" in a["WFWorkflowActionParameters"].get("WFURL", {}).get("Value", {}).get("string", "")]
assert len(uploads) == 1 and uploads[0].get("WFActionContinueAfterError") is True, \
    "attachment upload must continue after a download error"
for a in A:
    t = a["WFWorkflowActionParameters"].get("WFTextActionText", {}).get("Value", {})
    if isinstance(t, dict) and "run-shortcut" in t.get("string", ""):
        at = len(t["string"][:t["string"].index("\ufffc")].encode("utf-16-le")) // 2
        assert list(t["attachmentsByRange"]) == ["{%d, 1}" % at], "restart link input is not on its placeholder"
print("iCloud copy checked:", name, len(A), "actions, restart link input on its placeholder")
PY
sed -i "" "s#https://www.icloud.com/shortcuts/UNBUBBLE_EXPORT_4_LINK#$LINK#" Sources/Engine/ExportEngine.swift submit-1.2.1.sh
if grep -q shortcuts/UNBUBBLE_EXPORT_4_LINK Sources/Engine/ExportEngine.swift submit-1.2.1.sh; then echo "placeholder left"; exit 1; fi
grep -n "shortcutLink =" Sources/Engine/ExportEngine.swift
exec ./submit-1.2.1.sh "$@"
