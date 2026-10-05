# Personal Assistant

A personal assistant you talk to: it answers, keeps track of ongoing work, and does work
for you. Coding comes first: it starts coding agents on your repositories, reports their
progress, lets you redirect them while they run, and opens pull requests for your review.
Shopping, finance and home come later.

You use it through the web console (installable as an app) or the Android app, which is
the same console plus voice, phone actions and on-device answers. It runs on one Hetzner
server.

## How it works

- **Chat first.** Each message is triaged:
  - small talk gets a chat reply;
  - bookkeeping ("add oat milk to #groceries", "remind me at 9") is done instantly;
  - anything about coding work goes to the **supervisor**.
- **The supervisor** sees every run. It can start a coding agent, look closer at one,
  pass it new instructions while it works, or stop it. When something needs a decision,
  the reply carries **choices** to click or answer with "yes".
- **Coding agents** (Kimi Code, with Claude Code on MiniMax as fallback) work in isolated
  worktrees, two at a time. Their plan and steps stream back live. Each opens a draft PR,
  and only you can merge or deploy, with the approval token.
- **Work items** (goals, lists, routines) hold a brief that later chats and runs build on.
  Mention one with `#tag`.

How a message flows through the code: [`docs/architecture.md`](docs/architecture.md).
Chats, tasks and their API: [`docs/chats-and-tasks.md`](docs/chats-and-tasks.md).
What is built and what is next:
[`docs/personal-assistant-target-architecture.svg`](docs/personal-assistant-target-architecture.svg).

## Repository map

| Path | What it is |
|---|---|
| `app/api` | HTTP routes, one module per resource |
| `app/chat` | Message handling: triage, offers and choices, quick actions |
| `app/assistant` | The supervisor and its overview of the work |
| `app/coding` | Coding runs and agents: runners, live sessions, git, reports, PR executor |
| `app/services` | The task service: chat, tasks, executions, approvals, progress |
| `app/work` | Work items, briefs, spaces, memory |
| `app/execution` | Chat model and research agent executors |
| `app/web` | The console (`index.html`, `static/`) and its PWA files |
| `app/worker.py`, `app/main.py` | Worker and API entry points |
| `capabilities/`, `workflows/`, `repositories/`, `spaces/` | YAML configuration |
| `migrations/` | Ordered SQL migrations (re-run on every deploy, so idempotent) |
| `scripts/`, `deploy/` | Server deployment: host deployer, redeploy with rollback, Caddy |
| `android-assistant/` | The Android app (`shell/`) and the paused 3D avatar (`avatar/`) |
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
```

Tests use a temporary SQLite database; PostgreSQL is used in Docker and production. CI
runs the same checks on every pull request.

## Deploy

Merged changes are deployed from the console through an approved deployment workflow. The
server's host deployer builds tagged images, checks health, and rolls back automatically.
Setup and operations are covered in [`docs/remote-hosting.md`](docs/remote-hosting.md).

## Credits

- **"Cool Man" 3D character** (paused avatar client) by
  [ardhanaputra](https://sketchfab.com/ardhanaputra), from
  [Sketchfab](https://sketchfab.com/3d-models/cool-man-ad14b71697dd4ea7836c1f06c75e5f72),
  licensed under [CC BY 4.0](http://creativecommons.org/licenses/by/4.0/) and modified for
  this project. Details:
  [`android-assistant/avatar/docs/credits.md`](android-assistant/avatar/docs/credits.md).
- English pronunciations derive from the CMU Pronouncing Dictionary (Carnegie Mellon
  University, BSD-style licence).
