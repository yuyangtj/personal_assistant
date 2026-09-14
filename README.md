# Personal Assistant Core

The working foundation of a persistent, capability-driven personal AI manager.
It provides the task shell and capability-selection layer beneath future model,
agent, tool, approval, and Slack integrations.

## What works

- Create, inspect, list, annotate, and cancel tasks through HTTP.
- Persist tasks and immutable, ordered task events in PostgreSQL.
- Claim queued tasks safely with `FOR UPDATE SKIP LOCKED`.
- Execute a deterministic fake capability in a separate worker.
- Enforce task-state transitions in application code.
- Validate executor output before completing a task.
- Cooperatively cancel active executions.
- Deduplicate externally sourced tasks with an idempotency key.
- Load validated model, agent, and tool capability manifests from YAML.
- Select the highest-priority enabled capability satisfying task requirements.
- Record schema-validated manager decisions in the task event stream.
- Route execution through configured adapters instead of hard-coded agents.
- Inspect enabled and planned capabilities through the HTTP API.
- Analyze tasks through a provider-neutral, strictly validated manager-model contract.
- Retry malformed model output once without persisting raw responses.
- Keep capability selection deterministic after model inference.

The fake executor remains intentional. A scripted manager-model client exercises
the inference boundary in tests. Kimi is represented by a disabled
manifest, so the orchestration design can be inspected without allowing the CLI
to run before its adapter and sandbox are tested.

The before-and-after architecture diagrams are in
[`docs/architecture.md`](docs/architecture.md).

## Run with Docker

```bash
docker compose up --build
```

This starts PostgreSQL, applies `migrations/001_initial.sql`, and runs the API and
worker. The API is available at <http://localhost:8000>; interactive documentation
is at <http://localhost:8000/docs>.

Create a task:

```bash
curl -X POST http://localhost:8000/tasks \
  -H 'content-type: application/json' \
  -d '{"request":"Research PostgreSQL hosting"}'
```

Inspect it using the returned ID:

```bash
curl http://localhost:8000/tasks/TASK_ID
curl http://localhost:8000/tasks/TASK_ID/events
```

Inspect the capability registry:

```bash
curl http://localhost:8000/capabilities
curl 'http://localhost:8000/capabilities?include_disabled=false'
```

A caller may request capabilities without naming an executor:

```bash
curl -X POST http://localhost:8000/tasks \
  -H 'content-type: application/json' \
  -d '{
    "request":"Inspect this repository",
    "required_capabilities":["repository_analysis"]
  }'
```

That example currently fails safely because only the disabled Kimi manifest
provides `repository_analysis`. No fallback executor silently receives work it
cannot perform.

## Run locally

Start PostgreSQL and apply the migration, then:

```bash
cp .env.example .env
uv sync
uv run uvicorn app.main:app --reload
```

In another terminal:

```bash
uv run python -m app.worker
```

Environment variables are read directly by the service; load `.env` with your
preferred shell or process manager.

## Tests

```bash
uv run pytest
uv run ruff check .
```

Tests use a temporary SQLite database. PostgreSQL remains the production and
Docker Compose backend.

## Current boundaries

There is no real model, coding-agent, Slack, or application integration yet.
The production worker uses the deterministic manager. The provider-neutral
model-assisted manager is implemented and tested, but it requires a real model
client before being enabled in the worker process.

The next slice should implement one real provider client behind the existing
manager-model contract. After its structured-output behavior is verified, Kimi
can separately be added as a coding-agent adapter inside an isolated Git
worktree.

## Android assistant

[`android-assistant/`](android-assistant/README.md) contains the Android client
prototype: a Unity avatar with pronunciation-accurate English lip-sync, a native
Kotlin shell, and two characters.

## Credits

- **"Cool Man" 3D character** by [ardhanaputra](https://sketchfab.com/ardhanaputra),
  from [Sketchfab](https://sketchfab.com/3d-models/cool-man-ad14b71697dd4ea7836c1f06c75e5f72),
  licensed under [CC BY 4.0](http://creativecommons.org/licenses/by/4.0/).
  Modified for this project (rig clean-up, merged meshes, generated facial
  blendshapes, mouth interior, status light). Full details are in
  [`android-assistant/docs/credits.md`](android-assistant/docs/credits.md).
- English pronunciations derive from the CMU Pronouncing Dictionary
  (Carnegie Mellon University, BSD-style licence).
