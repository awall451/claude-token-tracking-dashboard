# Claude Token Tracker

Dashboard for tracking Claude Code token usage from local session data. Useful on a Claude Pro or Max subscription when you want visibility into how much you're consuming and when.

![Dashboard screenshot](docs/screenshot.png)

## What it tracks

- **Total usage** — sessions, turns, cost tokens, cache hit rate, subagent transcripts included
- **API-equivalent cost** — Claude Code's own `cost-state` tally per session (USD at API list prices, not your subscription bill), including side calls the transcripts don't log
- **Rolling windows** — 1h / 5h / 24h token usage with color-coded gauges (5h = Anthropic's reset window)
- **Burn rate** — tokens/hour with projection of when the 5h window fills
- **Caveman vs normal** — avg tokens/turn comparison for the [caveman plugin](https://github.com/JuliusBrussee/caveman). Auto-detects plugin install, breaks down by mode (lite/full/ultra/wenyan), and shows daily adoption trend. Onboarding callout if plugin not installed.
- **By day / by project** — usage breakdown over time
- **Model breakdown** — Sonnet vs Opus vs Haiku split
- **Cache efficiency** — reads vs writes vs fresh input

## How it works

Reads `~/.claude/projects/*/` — the JSONL session files Claude Code writes locally, plus subagent transcripts under `<session>/subagents/`. No API calls, no external services. All data stays local.

> **Privacy:** the container mounts `~/.claude` (your full prompt history and transcripts). `docker-compose.yml` publishes the port on `127.0.0.1` only. Don't change that to `0.0.0.0` — anyone on your LAN or tailnet could then browse the dashboard.

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
