# Working in this repository

Read `README.md` for what the assistant is and the repository map. These are the rules
that aren't obvious from the code; a change that skips one usually works in tests and
fails on the server.

## Checks

```bash
uv run pytest                                      # everything except the browser tests
uv run ruff check . && uv run ruff format --check .
uv run pytest -m ui                                # the console in Chromium, when you change app/web
```

A change to a chat-agent prompt (`app/execution/conversation.py`, `app/assistant/`) also
needs `uv run python scripts/chat_eval/run.py --repeat 3`; it calls real models, so
mention in the PR if you couldn't run it.

## A new setting or secret

1. Add the field to `Settings` in `app/config.py`. A field is read from
   `ASSISTANT_<NAME>` automatically; use `env("NAME")` only for an existing outside name
   such as `GITHUB_TOKEN`.
2. Add its name to `tests/test_settings_env.py`.
3. Document it in `docs/configuration.md`, `.env.example` (local) and
   `.env.remote.example` (the server).
4. **Pass it to every service that reads it in `compose.remote.yaml`.** Each service lists
   its environment one variable at a time; nothing reaches a container otherwise. The
   chat agent and its tools run in `worker`, the web API in `api` (it also needs a key
   if it decides whether an ability shows as available), coding runs in `coding-worker`.
   Don't give a secret to a service that doesn't use it. `tests/test_deploy_wiring.py`
   fails when a key in `.env.remote.example` reaches no service.

## Database

A new table or column needs a migration in `migrations/` (the next number,
`NNN_what.sql`) and the matching change in `app/persistence/models.py`. Migrations run on
every deploy, so write them to be re-runnable (`IF NOT EXISTS`). The coding worker has
its own limited database role (`migrations/035_coding_worker_role.sql`); grant it what a
new coding table needs.

## A new tool for the chat agent

Tools are registered in `app/assistant/tools.py` and built in `app/worker/build.py`. Each
belongs to an ability in `abilities/*.yaml`, which also says when it is available
(`requires`). Mark a tool `changes=False` only when it changes nothing. Whatever a tool
returns from outside (web pages, files, store data) reaches the model as data, never as
instructions.

## Calling an outside API

Tests fake the API's replies, so they only prove what you assumed. Before writing the
client, check the API's documentation for the cases a first version usually misses:

- pagination and result limits;
- items that need extra parameters to be visible (shared or team spaces, archived items);
- error codes that mean more than one thing (a 403 can be "no access", a rate limit or a
  size limit; read the error body's reason);
- token expiry and refresh, and how long a refresh token lives;
- large responses: limit what you read, and say when you cut something off.

Never put credentials or raw response bodies in errors or logs.

## Deploys

The server runs `compose.remote.yaml` through `scripts/redeploy-remote.sh`; see
`docs/remote-hosting.md`. Every merge to `main` deploys automatically once CI passes,
so whatever merges must be ready to run on the server. A new image file or folder must also be copied in the
`Dockerfile` (or `Dockerfile.coding-worker`) that needs it, and removing one must remove
its `COPY`.

## Coding runs

When the assistant runs you on a task, don't commit, push or open a pull request: the
host does that after validation and review.
