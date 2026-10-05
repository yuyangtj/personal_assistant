# Configuration

Settings and behaviour of the model providers, the coding workflow and speech. All are
read from the environment (`.env` locally, `.env.remote` on the server); see
[`remote-hosting.md`](remote-hosting.md) for the server.

Newer settings, all optional:

| Variable | Default | What it does |
| --- | --- | --- |
| `ASSISTANT_SUPERVISOR_ENABLED` | `true` | Messages about coding work go to the supervisor |
| `ASSISTANT_CODING_CONCURRENCY` | `2` | Coding agents running at once in the coding worker |
| `ASSISTANT_KIMI_CODE_PROTOCOL` | `acp` | `acp` runs Kimi Code as a live session; `prompt` is the one-shot fallback |
| `TAVILY_API_KEY` / `BRAVE_SEARCH_API_KEY` | unset | Enables the web research agent |
| `ASSISTANT_NTFY_TOPIC_URL` | unset | Push notifications through ntfy |

## Coding agent and GitHub review workflow

The `coding-pull-request` capability implements the first real agent workflow. It fetches
the configured base branch, creates an isolated worktree and `assistant/task-...` branch,
runs the first available configured coding runner, validates and commits the result, pushes the
branch, and opens a draft pull request. The task then pauses in `waiting_for_approval`.
Quota and rate-limit failures fall back to the next runner after restoring the disposable
worktree to its clean starting commit. Durable phase checkpoints and worker leases let an
interrupted run reconcile its branch and PR without repeating completed publication steps.

Local Compose keeps coding execution opt-in. The normal `docker compose up` starts only
chat and non-coding work. To test an approved coding workflow locally, export the Kimi,
MiniMax, and GitHub credentials, then start the isolated coding profile:

```bash
docker compose --profile coding up -d --build coding-worker
```

By default it mounts this checkout as the registered `personal-assistant` repository and
uses `.coding-worktrees/` for disposable task worktrees. Override
`ASSISTANT_HOST_REPOSITORY_PERSONAL_ASSISTANT_PATH` when the trusted checkout lives
elsewhere. Coding tasks remain queued when this profile is not running.

Merge is intentionally not a manager capability. After human review,
`GET /tasks/{task_id}/pull-request-status` reports live checks for the exact head. Both
ready-for-review and `POST /tasks/{task_id}/pull-request-approval` require every
manifest-declared check to pass. Merge approval also requires the exact reviewed head SHA
and a separate `X-Assistant-Approval-Token`. A changed SHA, draft or closed PR, merge
conflict, failed/missing check, or missing credential leaves the task unmerged. Explicit
revisions supersede the old approval task, reuse the PR branch, and require a fresh
exact-SHA approval. Configuration and examples are in
[`docs/coding-pull-request-workflow.md`](coding-pull-request-workflow.md).

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
| MiniMax | `MINIMAX_API_KEY` / `ASSISTANT_MINIMAX_API_KEY` | `https://api.minimax.chat/v1` | `MiniMax-M3` |

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
| `ASSISTANT_MINIMAX_MODEL` | `MiniMax-M3` |
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
