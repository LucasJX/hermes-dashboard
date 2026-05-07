# Hermes Dashboard 🦞

A modern, Apple-inspired monitoring dashboard for [Hermes Agent](https://github.com/LucasJX/hermes-agent). Real-time overview of channels, sessions, models, skills, logs, and quota usage.

## Features

- **Liquid Glass UI** — backdrop-filter blur, translucent surfaces, luminous borders
- **Dashboard** — Hero stats + quota card + channel status + active sessions
- **Channels** — Platform status (Telegram, Weixin, Discord, Slack, etc.) with per-platform session/token stats
- **Sessions** — Collapsible source groups, message viewer, session details
- **Models** — Provider management (MiniMax, xiaomi, etc.), model list, config editor
- **Skills** — Installed skills browser with SKILL.md viewer
- **Logs** — Multi-file log viewer with level/keyword filters, newest-first display
- **Quota** — MiniMax quota bars (per-round/weekly/monthly) + generic provider usage stats (tokens/cost from sessions DB)
- **Account** — Login, password management, session tokens (bypasses proxy cookie issues)

## Quick Start

```bash
git clone https://github.com/LucasJX/hermes-dashboard.git
cd hermes-dashboard
chmod +x start.sh
./start.sh
```

- Frontend: `http://localhost:3800`
- Backend: `http://localhost:3801`

Default login: `admin` / `admin` (change on first use)

## Requirements

- Python 3.10+
- Hermes Agent installed and configured (`~/.hermes/`)
- Dependencies: `flask`, `flask-cors`, `psutil`, `pyyaml`

## Architecture

```
server.py (port 3800)  →  Static files + proxy to backend
backend/app.py (port 3801)  →  Flask API, reads Hermes state.db + auth.json
frontend/index.html  →  Single-page app (vanilla JS, no framework)
```

## Environment

| Variable | Default | Description |
|----------|---------|-------------|
| `HERMES_HOME` | `~/.hermes` | Hermes agent home directory |
| `DASHBOARD_SECRET` | random | Flask session secret key |

## License

MIT
