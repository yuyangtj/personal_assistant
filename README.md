# Personal Assistant

A personal assistant you talk to: it answers, keeps track of ongoing work, and does work
for you. Coding comes first: it starts coding agents on your repositories, reports their
progress, lets you redirect them while they run, and opens pull requests for your review.
Shopping, finance and home come later.

You use it through the web console (installable as an app) or the Android app, which is
the same console plus voice and phone actions (timers, alarms, calendar drafts). It runs on one Hetzner
server.

## How it works

- **One assistant.** Every message goes to one chat agent with tools: lists, memories,
  reminders, routines, notes (save only), shopping, research, reading code, and coding runs.
  It sees the whole conversation, so "yes please do" means what it just offered. Only
  "note: …" is saved without a model. Replies may carry suggestion buttons; tapping one
  just says it.
- **Coding runs** are started, checked, steered and stopped by the agent's tools.
- **Coding agents** (Kimi Code, with Claude Code on MiniMax as fallback) work in isolated
  worktrees, two at a time. Their plan and steps stream back live. Each opens a draft PR,
  and only you can merge or deploy, with the approval token.
- **Work items** (goals, lists, routines) hold a brief that later chats and runs build on.
  Mention one with `#tag`.
- **Stats** (`#/stats`) show reply times, failures and sizes by kind of work, computed from
  what is already stored (`GET /stats`).
- **Notes** are a private, Keep-like pile of notes with tags and checklists, with an import
  from Google Keep. No model ever sees them: [`docs/notes.md`](docs/notes.md).

Where it is heading, and the rules for new features: [`docs/assistant-design.md`](docs/assistant-design.md).
How a message flows through the code: [`docs/architecture.md`](docs/architecture.md).
Chats, tasks and their API: [`docs/chats-and-tasks.md`](docs/chats-and-tasks.md).
What is built and what is next:
[`docs/personal-assistant-target-architecture.svg`](docs/personal-assistant-target-architecture.svg).

## Repository map

| Path | What it is |
|---|---|
| `app/api` | HTTP routes, one module per resource |
| `app/chat` | Message handling: the note rule, reply blocks, bookkeeping actions, routines |
| `app/assistant` | The chat agent's toolbox and tools, and the overview of coding runs |
| `app/coding` | Coding runs and agents: runners, live sessions, git, reports, PR executor |
| `app/services` | The task service: chat, tasks, executions, approvals, progress |
| `app/work` | Work items, briefs, spaces, memory, notes, stats |
| `app/execution` | Chat model and research agent executors |
| `app/web` | The console (`index.html`, `static/`) and its PWA files |
| `app/worker.py`, `app/main.py` | Worker and API entry points |
| `abilities/`, `workflows/`, `repositories/`, `spaces/` | YAML configuration |
| `migrations/` | Ordered SQL migrations (re-run on every deploy, so idempotent) |
| `scripts/`, `deploy/` | Server deployment: host deployer, redeploy with rollback, Caddy |
| `android-assistant/` | The Android app (`shell/`) |
| `docs/` | Architecture, configuration, hosting, workflows |

## Run it locally

```bash
docker compose up --build          # PostgreSQL, migrations, API and worker
open http://localhost:8000         # the console; API docs at /docs
```

Or without Docker, with PostgreSQL running and the migrations applied:

```bash
cp .env.example .env
uv sync
uv run uvicorn app.main:app --reload
uv run python -m app.worker        # in another terminal
```

Coding agents run in a separate, opt-in coding worker:
`docker compose --profile coding up -d --build coding-worker`.
Providers, models and other settings are described in
[`docs/configuration.md`](docs/configuration.md).

## Tests

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run playwright install chromium && uv run pytest -m ui   # the console, in a browser
```

Tests use a temporary SQLite database; PostgreSQL is used in Docker and production. The
browser tests (`tests/ui`) start the app on a free port and drive the real console in
Chromium; they are left out of the plain `pytest` run (set `UI_BROWSER_CHANNEL=chrome` to
use the Chrome you already have). CI runs everything on every pull request, the browser
tests as their own `ui` job.

## Deploy

Merged changes are deployed from the console through an approved deployment workflow. The
server's host deployer builds tagged images, checks health, and rolls back automatically.
Setup and operations are covered in [`docs/remote-hosting.md`](docs/remote-hosting.md).

## The 3D avatar, retired

A Unity 3D-character client (`android-assistant/avatar/`: a Kotlin host, the Unity
project, design sources, lip-sync tools, and credits for the CC BY 4.0 "Cool Man" model
and the CMU Pronouncing Dictionary) was paused and then removed on 2026-10-25. The web
console's SVG face (`app/web/static/avatar.js`) replaces it. To get it back:
`git checkout e053acb -- android-assistant/avatar`.
