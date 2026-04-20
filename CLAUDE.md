# CLAUDE.md

## Overview

Token usage tracker and dashboard for Claude Code ($20/mo plan). Parses local Claude session data, visualizes usage over time, and compares efficiency of caveman mode vs normal mode.

## Data Source

All session data lives in `~/.claude/projects/*/`. Each project directory contains one or more `.jsonl` files (one per session, named by session UUID).

**Path pattern:** `~/.claude/projects/<encoded-project-path>/<session-uuid>.jsonl`

The encoded project path replaces `/` with `-` (e.g. `/home/dillon/lab/foo` → `-home-dillon-lab-foo`).

## JSONL Entry Types

Each line is a JSON object with a `type` field:

| Type | Relevant fields |
|------|----------------|
| `assistant` | `message.usage`, `message.model`, `timestamp`, `sessionId` |
| `user` | `message.content`, `timestamp`, `sessionId` |
| `permission-mode` | `permissionMode` |

## Token Usage Fields (on `assistant` entries)

```json
{
  "message": {
    "model": "claude-sonnet-4-6",
    "usage": {
      "input_tokens": 3,
      "cache_creation_input_tokens": 9077,
      "cache_read_input_tokens": 11819,
      "output_tokens": 136,
      "cache_creation": {
        "ephemeral_1h_input_tokens": 9077,
        "ephemeral_5m_input_tokens": 0
      },
      "service_tier": "standard"
    }
  },
  "timestamp": 1776615377614,
  "sessionId": "..."
}
```

**Effective token cost** = `input_tokens + cache_creation_input_tokens + output_tokens`
Cache reads (`cache_read_input_tokens`) are cheaper — track separately for efficiency metrics.

## Caveman Mode Detection

Caveman sessions are detectable by scanning `user` entries for:
- `message.content` containing `<command-name>/caveman` or `caveman:caveman`
- Or `isMeta: true` entries injected by the caveman hook

Tag a session as "caveman" if any user turn in that session contains the caveman activation signal. The caveman plugin reactivates each turn via the `UserPromptSubmit` hook, so presence in `isMeta` entries is the most reliable signal.

## Stack

**Backend:** Python 3 script that parses all `~/.claude/projects/` JSONL files and outputs aggregated JSON stats.

**Frontend:** Single-page HTML/JS with Chart.js for graphs. No build step — serve static files.

**Dev server:** `python3 -m http.server 8080` from the `frontend/` dir.

## Directory Layout

```
claude-token-tracking/
  parser/
    parse.py          # Main parser — reads ~/.claude, outputs stats.json
    models.py         # Data models / aggregation logic
  frontend/
    index.html        # Main dashboard
    app.js            # Chart rendering + fetch from stats.json
    style.css
  stats.json          # Generated output (gitignored)
```

## Key Metrics to Track

- **Total tokens per session** (input + output + cache_creation)
- **Cache hit rate** = `cache_read / (cache_read + cache_creation)` — higher = more efficient
- **Tokens per day / per project** — timeline view
- **Model breakdown** — Sonnet vs Opus vs Haiku usage
- **Caveman vs normal** — avg tokens/turn in caveman sessions vs non-caveman sessions
- **Output token ratio** — output / total, useful for measuring response verbosity

## Commands

```bash
# Parse all local session data → stats.json
python3 parser/parse.py

# Serve dashboard (must run from project root so stats.json is accessible)
python3 -m http.server 9420
# Then open: http://localhost:9420/frontend/
```

## Notes

- `history.jsonl` at `~/.claude/history.jsonl` tracks prompt display text + project path per sessionId — useful for joining project names to sessions.
- Session files may have duplicate assistant entries (streaming chunks) — deduplicate by `requestId` when summing usage.
- Timestamps are Unix ms.
- The `$20/mo` Claude Max plan has usage limits; no public API for querying remaining quota — track trends locally as proxy.
