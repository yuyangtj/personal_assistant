# Personal Assistant Core

The working foundation of a persistent, capability-driven personal AI manager.
It provides the task shell and capability-selection layer beneath future model,
agent, tool, approval, and Slack integrations.

## What works

- Create, inspect, list, annotate, and cancel tasks through HTTP.
- Persist tasks and immutable, ordered task events in PostgreSQL.
- Create, list, and resume durable chat sessions whose linked tasks provide model context.
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
- Run coding changes in isolated Git worktrees and publish dedicated draft pull requests.
- Require an exact-head-SHA approval before the GitHub merge operation.

The fake executor remains as a credential-free fallback, and a scripted client
exercises the manager inference boundary in tests. Kimi and MiniMax conversational
execution and opt-in manager analysis are implemented. A Codex-backed coding-to-draft-PR
adapter is implemented as an opt-in runtime capability. The separate Kimi Code
capability remains disabled until its CLI runner is implemented and tested.

The before-and-after architecture diagrams are in
[`docs/architecture.md`](docs/architecture.md).

## Run with Docker

```bash
docker compose up --build
```

This starts PostgreSQL, applies the ordered SQL files in `migrations/`, and runs the API
and worker. The API is available at <http://localhost:8000>; interactive documentation
is at <http://localhost:8000/docs>.

The built-in browser console is available at <http://localhost:8000/>. To publish that
console on a remote server with automatic HTTPS, login protection, and private backend
containers, use the Phase 1 deployment in
[`docs/remote-hosting.md`](docs/remote-hosting.md). Remote Android connectivity is kept
as a separate Phase 2.

Create a task:

```bash
curl -X POST http://localhost:8000/tasks \
  -H 'content-type: application/json' \
  -d '{"request":"Research PostgreSQL hosting"}'
```

Create a persistent conversation and attach tasks to it:

```bash
CHAT_ID=$(curl -s -X POST http://localhost:8000/chat-sessions \
  -H 'content-type: application/json' -d '{}' | jq -r .id)
curl -X POST http://localhost:8000/tasks \
  -H 'content-type: application/json' \
  -d "{\"request\":\"Remember that my project is Milo\",\"chat_session_id\":\"$CHAT_ID\"}"
curl http://localhost:8000/chat-sessions
curl http://localhost:8000/chat-sessions/$CHAT_ID/tasks
```

## Chats and tasks

Chats are where you think and communicate; tasks are work launched from them. The two are
linked by id, never by copying content, so task status is never duplicated or stale and
the task event stream stays authoritative.

Talking in a chat launches nothing. Posting a message stores the turn and, when it reads
as a request for work, returns a `proposal` the client offers as "create a task?":

```bash
MESSAGE=$(curl -s -X POST http://localhost:8000/chat-sessions/$CHAT_ID/messages \
  -H 'content-type: application/json' \
  -d '{"content":"Add authentication to the API"}')
echo "$MESSAGE" | jq .proposal
```

A proposal is advisory. Work starts only when the message is confirmed, which links the
task to the message it came from (`origin_message_id`):

```bash
MESSAGE_ID=$(echo "$MESSAGE" | jq -r .message.id)
curl -X POST http://localhost:8000/chat-sessions/$CHAT_ID/messages/$MESSAGE_ID/task \
  -H 'content-type: application/json' -d '{}'
```

Confirming the same message twice returns the original task rather than starting the work
again. A proposal marked `consequential` — anything touching a repository or a deployment
— is the case clients must confirm before a conversation launches costly work.

Going the other way, `POST /tasks/TASK_ID/chat-session` opens a task's conversation
(backfilling one for older tasks) and posts a reference to the task: its status, result
and artifacts. A follow-up continues the work as a new task that records its
`parent_task_id`:

```bash
curl http://localhost:8000/tasks/TASK_ID/context
curl -X POST http://localhost:8000/tasks/TASK_ID/follow-up \
  -H 'content-type: application/json' \
  -d '{"request":"Finish the Android part"}'
```

`GET /tasks/TASK_ID/context` is the curated view that follow-up prompts and chat
references are built from: the original request, goal, status, final answer, validation
verdict, artifact and pull-request references, and the failure message. Polling events,
raw tool responses, diffs, execution inputs and token usage stay in the event stream and
never reach a model prompt. Attach anything more by hand.

Inspect it using the returned ID:

```bash
curl http://localhost:8000/tasks/TASK_ID
curl http://localhost:8000/tasks/TASK_ID/events
curl http://localhost:8000/tasks/TASK_ID/pending-approval
```

The pending-approval endpoint returns the latest approval request while the task is
waiting for approval, or `404` when no approval is pending.

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

That example routes to the coding PR adapter only when its operator configuration is
enabled. Otherwise it fails safely; no fallback executor silently receives work it
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

There is no Slack or general tool integration yet. Kimi or MiniMax can independently
provide conversational execution and manager analysis. Manager analysis is opt-in; the
production worker keeps deterministic routing by default. The Codex CLI coding adapter
is also opt-in and targets one operator-configured repository. Kimi Code remains a
separate disabled provider until its CLI runner is implemented and tested.

## Coding agent and GitHub review workflow

The `coding-pull-request` capability implements the first real agent workflow. It fetches
the configured base branch, creates an isolated worktree and `assistant/task-...` branch,
runs Codex with workspace-only write access, validates and commits the result, pushes the
branch, and opens a draft pull request. The task then pauses in `waiting_for_approval`.

Merge is intentionally not a manager capability. After human review and marking the PR
ready, `POST /tasks/{task_id}/pull-request-approval` requires the exact reviewed head SHA
and a separate `X-Assistant-Approval-Token`, then asks GitHub to merge. A changed SHA,
draft PR, closed PR, failed protection rule, or missing credential leaves the task
unmerged. Configuration and examples are in
[`docs/coding-pull-request-workflow.md`](docs/coding-pull-request-workflow.md).

## Optional manager-model analysis

Set `ASSISTANT_MANAGER_MODEL_ENABLED=true` to let Kimi or MiniMax infer a task's
required capabilities before deterministic routing. Explicit `required_capabilities`
still bypass the model. Responses are validated against the existing `TaskAnalysis`
schema, invented capabilities are rejected, and one repair attempt is allowed.

| Variable | Default |
| --- | --- |
| `ASSISTANT_MANAGER_MODEL_ENABLED` | `false` |
| `ASSISTANT_MANAGER_MODEL_PROVIDER` | `kimi` (`kimi` or `minimax`) |
| `ASSISTANT_MANAGER_MODEL_FALLBACK_PROVIDER` | `auto` (the other routine provider) |
| `ASSISTANT_MANAGER_MODEL_BASE_URL` | unset; use the selected provider's URL |
| `ASSISTANT_MANAGER_MODEL` | unset; use the selected provider's model |
| `ASSISTANT_MANAGER_MODEL_TIMEOUT_SECONDS` | `30` |
| `ASSISTANT_MANAGER_MODEL_MINIMUM_CONFIDENCE` | `0.5` |

| Provider | Key | Provider URL | Default manager model |
| --- | --- | --- | --- |
| Kimi | `KIMI_API_KEY` / `ASSISTANT_KIMI_API_KEY` | `https://api.kimi.com/coding/v1` | `kimi-for-coding-highspeed` |
| MiniMax | `MINIMAX_API_KEY` / `ASSISTANT_MINIMAX_API_KEY` | `https://api.minimax.chat/v1` | `MiniMax-M2.7-highspeed` |

When manager analysis is enabled, an ordinary request can make two model calls:
one to infer capabilities and one to execute the selected capability. Those calls
may use different providers. Provider, model, latency, attempts, and token usage are
recorded with the task analysis.

For example, to analyze with Kimi while keeping the default MiniMax conversation
provider:

```bash
ASSISTANT_MANAGER_MODEL_ENABLED=true \
ASSISTANT_MANAGER_MODEL_PROVIDER=kimi \
ASSISTANT_MANAGER_MODEL=kimi-for-coding-highspeed \
docker compose up --build
```

## Conversational replies with Kimi or MiniMax

The worker installs the provider-neutral `model-conversation` capability (priority 20)
when at least one configured provider key is available. It answers everyday requests with short,
speakable replies through an OpenAI-compatible API. Without that key, routing falls
back to the fake executor, because only capabilities whose adapter is installed can be
selected (`CapabilityRegistry.restricted_to_adapters`).

| Variable | Default |
| --- | --- |
| `ASSISTANT_CONVERSATION_MODEL_PROVIDER` | `minimax` (`kimi` or `minimax`) |
| `ASSISTANT_CONVERSATION_MODEL_FALLBACK_PROVIDER` | `auto` (the other routine provider) |
| `ASSISTANT_CONVERSATION_MODEL_BASE_URL` | unset; use the selected provider's URL |
| `ASSISTANT_CONVERSATION_MODEL` | unset; use the selected provider's model |
| `ASSISTANT_CONVERSATION_MODEL_TIMEOUT_SECONDS` | unset; use the provider timeout |
| `KIMI_API_KEY` / `ASSISTANT_KIMI_API_KEY` | unset |
| `ASSISTANT_KIMI_BASE_URL` | `https://api.kimi.com/coding/v1` |
| `ASSISTANT_KIMI_MODEL` | `kimi-for-coding-highspeed` (about 1.5–2 s per reply) |
| `ASSISTANT_KIMI_TIMEOUT_SECONDS` | `30` |
| `MINIMAX_API_KEY` / `ASSISTANT_MINIMAX_API_KEY` | unset |
| `ASSISTANT_MINIMAX_BASE_URL` | `https://api.minimax.chat/v1` |
| `ASSISTANT_MINIMAX_MODEL` | `MiniMax-M2.7-highspeed` |
| `ASSISTANT_MINIMAX_TIMEOUT_SECONDS` | `30` |

- Replies are one to three spoken sentences with an emotion (`Warm`, `Curious`,
  `Excited`, `Concerned`, `Neutral`).
- The model can propose confirmation-gated timers, alarms, and calendar events, but it
  cannot read accounts or claim that an action has already happened.
- Tasks with the same `source_context.conversation_id` share context: the last six
  completed turns are sent along with the request.
- A reply may carry one validated phone `action` (`set_timer`, `set_alarm`,
  `create_event`) that clients run only after user confirmation. Unsupported or malformed
  actions are dropped, and a reply that promised one is replaced with an honest failure
  message. Clients send `local_time` and `timezone` in `source_context` so relative dates
  resolve correctly.
- One-time alarms carry a local date and are accepted only when that date is the next
  occurrence of the requested clock time. Arbitrary future dates are rejected rather
  than silently scheduling the wrong day.
- Model, latency and token usage are recorded in `EXECUTION_OUTPUT_RECEIVED`. The API key
  is only read from the environment and never logged.

When both keys are configured, routine calls try the selected provider first and retry
the other provider only after a sanitized timeout or provider failure. `auto` chooses
MiniMax after Kimi, or Kimi after MiniMax. Set either fallback variable to `off` to use
only the primary provider. Events record the provider and model that actually succeeded.

For example, use MiniMax M3 for replies while leaving manager analysis disabled:

```bash
ASSISTANT_CONVERSATION_MODEL_PROVIDER=minimax \
ASSISTANT_CONVERSATION_MODEL=MiniMax-M3 \
docker compose up --build
```

Docker Compose passes both provider keys through from the host shell:

```bash
ASSISTANT_API_PORT=8010 ASSISTANT_POSTGRES_PORT=55433 docker compose up --build -d
```

## Gemini speech only

Set `GEMINI_TTS_API_KEY` to enable `POST /speech`. This is a deliberately narrow,
audio-only integration: the key is passed to the API service but not the worker, so
Gemini cannot be selected for conversation or manager analysis. The endpoint accepts up
to 600 characters and returns a 24 kHz mono WAV using
`gemini-3.1-flash-tts-preview` and the friendly `Achird` voice by default.

Identical text and emotion pairs are cached in memory (128 entries by default) to avoid
repeat billable requests. The Android client downloads the WAV into app-private cache;
if Gemini is unavailable, slow, or the reply exceeds the cloud limit, it automatically
uses Android's on-device English TTS instead.

## Assistant replies

Every finished task gets an `ASSISTANT_REPLY` event just before its terminal event:
`{"text", "emotion", "intensity", "outcome"}`. The text is safe to show or speak to the
user. Completed tasks use the executor's `reply` output (falling back to its `summary`);
failures and cancellations use fixed messages without internal error details.

Other local services may already use ports 8000 and 5432. The Compose host ports are
configurable:

```bash
ASSISTANT_API_PORT=8010 ASSISTANT_POSTGRES_PORT=55433 docker compose up --build -d
```

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
