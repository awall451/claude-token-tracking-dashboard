#!/usr/bin/env bash
# Statusline wrapper for claude-token-tracker.
#
# Claude Code pipes a JSON blob to the statusLine command on every refresh. For
# Pro/Max subscriptions it includes real quota usage (rate_limits.five_hour /
# seven_day: used_percentage + resets_at). This wrapper appends a compact snapshot
# of those fields to a log (at most once per QUOTA_LOG_INTERVAL seconds), then
# hands the same JSON to your existing statusline command so nothing changes on screen.
#
# settings.json:
#   "statusLine": { "type": "command", "command": "/path/to/statusline-tee.sh ccstatusline" }
#
# Arguments = the downstream statusline command (omit to print nothing).
# Requires jq. Never fails the statusline: logging errors are swallowed.

set -u

QUOTA_LOG="${QUOTA_LOG:-$HOME/.claude/token-tracker/quota.jsonl}"
QUOTA_LOG_INTERVAL="${QUOTA_LOG_INTERVAL:-60}"

input="$(cat)"

{
  now="$(date +%s)"
  last="$(stat -c %Y "$QUOTA_LOG" 2>/dev/null || echo 0)"
  if (( now - last >= QUOTA_LOG_INTERVAL )); then
    line="$(jq -c --argjson ts "$now" '
      select(.rate_limits != null)
      | {ts: $ts, session_id, rate_limits}
    ' <<<"$input" 2>/dev/null)"
    if [[ -n "$line" ]]; then
      mkdir -p "$(dirname "$QUOTA_LOG")"
      printf '%s\n' "$line" >>"$QUOTA_LOG"
    fi
  fi
} 2>/dev/null || true

if (( $# > 0 )); then
  printf '%s' "$input" | "$@"
fi
