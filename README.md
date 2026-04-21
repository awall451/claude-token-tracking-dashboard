# Claude Token Tracker

Dashboard for tracking Claude Code token usage from local session data. Useful if you're on the $20/mo Claude Max plan and want visibility into how much you're consuming and when.

![Dashboard screenshot](docs/screenshot.png)

## What it tracks

- **Total usage** — sessions, turns, cost tokens, cache hit rate
- **Rolling windows** — 1h / 5h / 24h token usage with color-coded gauges (5h = Anthropic's reset window)
- **Burn rate** — tokens/hour with projection of when the 5h window fills
- **Caveman vs normal** — avg tokens/turn comparison when using the [caveman plugin](https://github.com/JuliusBrussee/caveman)
- **By day / by project** — usage breakdown over time
- **Model breakdown** — Sonnet vs Opus vs Haiku split
- **Cache efficiency** — reads vs writes vs fresh input

## How it works

Reads `~/.claude/projects/*/` — the JSONL session files Claude Code writes locally. No API calls, no external services. All data stays local.

## Quick start (Docker)

```bash
docker compose up -d
# open http://localhost:9420
```

Mounts `~/.claude` read-only. Stats refresh automatically every 5 minutes. Force a refresh anytime:

```bash
curl -X POST http://localhost:9420/api/refresh
```

### Configuration

Set via environment variables in `docker-compose.yml`:

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `9420` | HTTP port |
| `CLAUDE_DIR` | `/data/.claude` | Path to Claude data dir inside container |
| `REFRESH_INTERVAL` | `300` | Seconds between auto-refresh |
| `STATS_PATH` | `/data/stats.json` | Where parsed stats are written |

## Manual usage (no Docker)

```bash
# Parse session data → stats.json
python3 parser/parse.py

# Anonymize project names (for screenshots/sharing)
python3 parser/parse.py --anonymize

# Serve dashboard from project root
python3 -m http.server 9420
# open http://localhost:9420/frontend/
```

## Soft limit

Dashboard has a configurable soft limit input (default: auto-calibrated from historical peak 5h window + 10% headroom). Gauges go green → orange → red as you approach it.

Anthropic doesn't expose actual quota. If you hit a real rate limit, note the 5h token count shown at that moment and set it as your limit.

## Requirements

Python 3.10+, no dependencies. Docker for containerized deployment. Chart.js loaded from CDN.
