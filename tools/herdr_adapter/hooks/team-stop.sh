#!/bin/bash
# team-stop.sh <record-root>: Claude Stop feedback for one admitted team worker. TEMPLATE: install it per project.
#
# FEEDBACK ONLY. Every hook failure lets the agent through, so this can never be the gate: the lead accepts only
# through its own check (lead.review_receipt with the plan's verifiers), and nothing in lead.py or resume.py reads
# this hook's output or its log.
#
# Scope: HERDR_PANE_ID plus the record root fixed in the hook command. A session with no pane id, no record, or a pane
# that is neither a lead.py member with a task in flight nor pinned in <record-root>/assignments/ is left alone.
#
# Claude Code fires Stop at every turn end, not only at completion, so the hook gives feedback only when the worker
# claims completion. A claim is either a line of the last assistant message that starts "DONE <attempt>" (sentence
# punctuation . , : ; ! may follow the attempt), or a return file that already holds a Return section closing the attempt.
# Everything else is a mid-task stop: exit 0, silently.
#   last message starts with HELP or HELD              -> exit 0 always (a question for the lead is not a completion)
#   any DONE line names the pane's attempt              -> a claim, whatever other attempts the message also names
#   DONE lines name only other attempts                 -> exit 0, one "attempt mismatch" line in the log
#
# Mapping for a claimed completion, preflight verdict -> hook exit. The validator's own exit status is never
# forwarded: it exits 1 for an invalid receipt, and a hook exit of 1 is a non-blocking error, so forwarding it would
# pass every invalid receipt.
#   preflight-ok      -> exit 0   (exit 2 once only when the Stop payload lists background_tasks still running)
#   missing           -> exit 2   the precise errors on stderr; the worker sees them and may fix its return
#   cannot-evaluate   -> exit 0   one line in <record-root>/stop-hook.log; the worker is not told
#   anything else     -> exit 0   (crash, no JSON, missing interpreter or script, unknown verdict) logged the same way
# Early exit 0: stop_hook_active is true (the retry escape: the stop is allowed and the task stays unaccepted).
# This script never runs a plan verifier; preflight.py is read-only and runs none.
#
# Environment: TEAM_STOP_PYTHON (an absolute interpreter; never a version-manager shim), TEAM_STOP_PREFLIGHT.

RECORD="$1"
PYTHON="${TEAM_STOP_PYTHON:-/usr/bin/python3}"
PREFLIGHT="${TEAM_STOP_PREFLIGHT:-$HOME/.agents/mifal/tools/herdr_adapter/preflight.py}"
PANE="$HERDR_PANE_ID"

log() {
  [ -d "$RECORD" ] && printf '%s pane=%s %s\n' "$(date -u +%FT%TZ)" "$PANE" "$*" >> "$RECORD/stop-hook.log" 2>/dev/null
  return 0
}

[ -n "$PANE" ] && [ -n "$RECORD" ] && [ -d "$RECORD" ] || exit 0
[ -f "$RECORD/lead-state.json" ] || [ -d "$RECORD/assignments" ] || exit 0

payload="$(cat)"
[ -x "$PYTHON" ] || { log "cannot-evaluate: interpreter $PYTHON is not executable"; exit 0; }

flags="$(printf '%s' "$payload" | "$PYTHON" -I -c 'import json, re, sys
d = json.load(sys.stdin)
msg = d.get("last_assistant_message")
msg = msg if isinstance(msg, str) else ""
word = re.match(r"[A-Za-z]+", re.sub(r"^[\s`*_>#-]+", "", msg))
done = re.findall(r"(?m)^[\s`*_>#-]*DONE[ \t]+([0-9]+(?:\.[0-9]+)*)[.,:;!]*(?![\w.])", msg)
print("active" if d.get("stop_hook_active") is True else "inactive", len(d.get("background_tasks") or []),
      word.group(0) if word else "-", ",".join(dict.fromkeys(done)) or "-")' 2>/dev/null)" ||
  { log "cannot-evaluate: unreadable Stop payload"; exit 0; }
set -- $flags
[ "$1" = active ] && exit 0
background="${2:-0}"
case "$3" in HELP|HELD) exit 0 ;; esac
said_done="${4:--}"   # every attempt a DONE line names, comma separated

request="$("$PYTHON" -I -c 'import json, sys
print(json.dumps({"command": "preflight", "params": {"record_root": sys.argv[1], "pane_id": sys.argv[2]}}))' "$RECORD" "$PANE" 2>/dev/null)" ||
  { log "cannot-evaluate: could not build the preflight request"; exit 0; }

# The preflight's exit status is deliberately ignored: only its typed verdict counts.
reply="$(printf '%s' "$request" | "$PYTHON" -B "$PREFLIGHT" 2>/dev/null)"

# First line: kind, the attempt the preflight judged, and whether the return already closes it (tab separated).
mapped="$(printf '%s' "$reply" | "$PYTHON" -I -c 'import json, sys
def head(kind, result=None):
    result = result if isinstance(result, dict) else {}
    print(kind, str(result.get("attempt") or "-"), "1" if result.get("closes") is True else "0", sep="\t")
try:
    result = json.load(sys.stdin)["result"]
    kind = result["verdict"]
    if kind == "preflight-ok":
        head("ok", result)
        print("")
    elif kind == "missing":
        head("missing", result)
        print("\n".join("- " + str(error) for error in result["errors"][:20])[:3000])
    elif kind == "cannot-evaluate":
        head("cannot")
        print(str(result.get("reason")) + ": " + str(result.get("detail", ""))[:300])
    else:
        head("other")
        print("unexpected verdict " + str(kind)[:80])
except Exception as error:
    head("other")
    print("unreadable preflight reply (" + error.__class__.__name__ + ")")' 2>/dev/null)"
IFS=$'\t' read -r kind judged closes <<< "${mapped%%$'\n'*}"
detail="${mapped#*$'\n'}"

# A mid-task stop is not a completion claim: leave it alone, silently.
if [ "$said_done" != - ]; then
  if [ "$judged" != - ]; then
    case ",$said_done," in
      *",$judged,"*) ;;
      *) log "attempt mismatch: the last message says DONE ${said_done//,/, DONE }, this pane is on attempt $judged"
         exit 0 ;;
    esac
  fi
elif [ "$closes" != 1 ]; then
  exit 0
fi

case "$kind" in
  ok)
    if [ "$background" -gt 0 ] 2>/dev/null; then
      printf 'team-stop: %s background task(s) are still running; wait for them, then stop again.\n' "$background" >&2
      exit 2
    fi
    exit 0 ;;
  missing)
    printf 'team-stop: your return is not ready for the lead. Fix this, then stop again:\n%s\n' "$detail" >&2
    exit 2 ;;
  cannot)
    log "cannot-evaluate $detail"
    exit 0 ;;
  *)
    log "${detail:-no preflight reply}"
    exit 0 ;;
esac
