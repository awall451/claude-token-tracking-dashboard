# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Token usage tracker and dashboard for Claude Code (Max plan). Parses local Claude session data, visualizes usage over time, and compares efficiency of caveman mode vs normal mode.

## Commands

```bash
# Parse all local session data → stats.json
python3 parser/parse.py

# Parse with pretty-print JSON, summary output, or anonymized project paths
python3 parser/parse.py --pretty --summary --anonymize

# All-in-one server (parses on startup, auto-refreshes every 5 min, serves frontend)
python3 server/serve.py
# open http://localhost:9420

# Simple static server (requires manually running parse.py first)
python3 -m http.server 9420
# open http://localhost:9420/frontend/

# Docker (recommended for persistent use)
docker compose up -d
curl -X POST http://localhost:9420/api/refresh   # force re-parse
```

## Architecture

**Data flow:** `~/.claude/projects/*/` JSONL files → `parser/parse.py` → `stats.json` → `frontend/app.js` (Chart.js)

**`parser/parse.py`** — entry point. Walks all project dirs, calls `parse_session_file()` per JSONL, deduplicates streaming chunks by `requestId`, then calls `build_output()` to produce the final JSON blob.

**`parser/models.py`** — pure data logic with no I/O. `SessionStats` dataclass holds per-session aggregates. Module-level functions (`aggregate_by_day`, `aggregate_by_project`, `caveman_comparison`, `rolling_windows`) transform lists of `SessionStats` into the nested dicts that `stats.json` emits. The 5h rolling window in `rolling_windows()` is the key metric since Anthropic uses a 5-hour reset window for rate limiting.

**`server/serve.py`** — single-file HTTP server. Imports `parser/parse.py` directly (via `sys.path` insertion) and runs `refresh_stats()` on a background thread every `REFRESH_INTERVAL` seconds. Serves `frontend/` as static files and `stats.json` from `STATS_PATH`. Two API endpoints: `POST /api/refresh` (force re-parse) and `GET /api/status`.

**`frontend/app.js`** — fetches `/stats.json` on load, renders all charts and tables with Chart.js. The soft-limit input auto-populates from `rolling.suggested_limit` (historical peak 5h window + 10% headroom). Session rows are clickable and open a detail modal with per-turn context growth and token composition charts.

## Data Source Details

**Path pattern:** `~/.claude/projects/<encoded-project-path>/<session-uuid>.jsonl`

Encoded project path replaces leading `/` with `-` (e.g. `/home/dillon/lab/foo` → `-home-dillon-lab-foo`). `~/.claude/history.jsonl` maps `sessionId → project_path` with a more reliable path string — the parser prefers this over decoding directory names.

**Effective token cost** = `input_tokens + cache_creation_input_tokens + output_tokens`  
Cache reads (`cache_read_input_tokens`) are separately tracked — they're cheaper per-token but still consume quota.

**Duplicate entries:** Streaming causes multiple assistant entries per request. Always deduplicate by `requestId` before summing usage.

**Caveman detection:** Scan `user` entries for `<command-name>/caveman` or `caveman:caveman` in `message.content`. Tag the entire session as caveman if any turn matches. Mode variant (lite/full/ultra/wenyan) extracted from `<command-args>` tag.

**Timestamps:** Unix milliseconds.

## Permissions & Workflow

For any task in this project:
1. Edit any file in the project directory without asking permission
2. After edits, deploy to local Docker: `docker compose build --no-cache --pull=false && docker compose up -d`
3. Validate via `curl http://localhost:9420/api/status` and spot-check `stats.json`
4. If broken, diagnose and fix, repeat until working
5. After completion, suggest further improvements if any

## TODO

- Update README with community-sourced soft limit estimates per plan (empirically, $20 Max plan hits ~600K tokens/5h window; 5x Max ~3M; 20x Max ~12M). Note these are single-user data points, not Anthropic-confirmed values. Invite contributors to share their limit observations.

## Key Invariants

- `stats.json` is gitignored — always generated locally, never committed.
- No external dependencies: Python 3.10+ stdlib only. Chart.js loaded from CDN.
- `server/serve.py` writes stats to `/data/stats.json` by default (Docker volume path); override with `STATS_PATH` env var for local use.
- The `CLAUDE_DIR` env var overrides the `~/.claude` path in both `parse.py` and `serve.py`.
