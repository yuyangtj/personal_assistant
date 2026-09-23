# Remote hosting: Phase 1 browser UI

This deployment publishes the browser console at `https://YOUR_DOMAIN/`. Caddy obtains
and renews the TLS certificate automatically and requires a username/password before it
proxies any request. The API, worker, and PostgreSQL have no public host ports.

The Android app remains a local/USB client in this phase. Its remote authentication is
Phase 2 and does not need to be configured yet.

## Server and DNS

Use a small Linux server with a public IP, Docker Engine, Docker Compose v2, and at
least 2 GB RAM. Point a DNS `A` record (and `AAAA` if applicable) such as
`assistant.example.com` to the server. Allow inbound TCP 22, 80, and 443 and UDP 443 in
the cloud firewall. Do not expose ports 5432 or 8000.

Clone the repository on the server, then create the deployment environment:

```bash
cp .env.remote.example .env.remote
chmod 600 .env.remote
```

Generate two different URL-safe secrets for PostgreSQL and merge approval:

```bash
openssl rand -hex 32
openssl rand -hex 32
```

Generate the browser-login password hash interactively (the password is not printed or
stored in shell history):

```bash
docker run --rm -it caddy:2.10-alpine caddy hash-password
```

Edit `.env.remote`. Set the real domain, login name, single-quoted Caddy hash, the two
random secrets, and at least one routine model key (`KIMI_API_KEY` or
`MINIMAX_API_KEY`). `.env.remote` is ignored by Git through the existing `.env` pattern.

## Provision the optional coding worker

The normal `worker` handles chat and non-coding tasks. The separate `coding-worker`
profile contains Git, uv, Kimi Code, and Claude Code and has no public port or Docker
socket. It runs with all Linux capabilities dropped and only receives the two explicitly
registered repository mounts plus its worktree directory.

Create operator-managed clones and a worktree directory on the server:

```bash
sudo install -d -o "$USER" -g "$USER" /srv/personal-assistant/repos
sudo install -d -o "$USER" -g "$USER" /srv/personal-assistant/worktrees
git clone https://github.com/yuyangtj/personal_assistant.git \
  /srv/personal-assistant/repos/personal_assistant
git clone https://github.com/yuyangtj/analytics-agent-playground.git \
  /srv/personal-assistant/repos/analytics-agent-playground
id -u
id -g
```

Set these `.env.remote` fields to the printed UID/GID and the paths above:

```dotenv
ASSISTANT_CODING_WORKER_UID=1000
ASSISTANT_CODING_WORKER_GID=1000
ASSISTANT_HOST_REPOSITORY_PERSONAL_ASSISTANT_PATH=/srv/personal-assistant/repos/personal_assistant
ASSISTANT_HOST_REPOSITORY_ANALYTICS_AGENT_PLAYGROUND_PATH=/srv/personal-assistant/repos/analytics-agent-playground
ASSISTANT_HOST_CODE_WORKTREE_ROOT=/srv/personal-assistant/worktrees
KIMI_API_KEY=...
MINIMAX_API_KEY=...
ASSISTANT_GITHUB_TOKEN=...
```

Use clean HTTPS clones whose `origin` matches the repository manifests. The fine-grained
GitHub token must be restricted to the registered repositories with Contents and Pull
requests write and Checks read. Keep `ASSISTANT_APPROVAL_TOKEN` separate; it is supplied
only to the API and is never available to either coding CLI.

## Start and verify

Validate the rendered Compose file before starting it:

```bash
docker compose --env-file .env.remote -f compose.remote.yaml config --quiet
docker compose --env-file .env.remote -f compose.remote.yaml --profile coding up -d --build
docker compose --env-file .env.remote -f compose.remote.yaml ps
```

Visit `https://YOUR_DOMAIN/` and enter the configured browser credentials. You should
see the conversation console, where you can create chats, send messages, confirm task
proposals, inspect task status/results, continue a task, and return to its chat.

Useful operational commands:

```bash
docker compose --env-file .env.remote -f compose.remote.yaml logs -f api worker coding-worker caddy
docker compose --env-file .env.remote -f compose.remote.yaml pull
docker compose --env-file .env.remote -f compose.remote.yaml up -d --build
```

After the first deployment, use the checked-in redeploy script. It pulls `origin/main`,
sets MiniMax as the primary conversation provider, validates configuration, creates a
timestamped PostgreSQL backup when the database is already running, rebuilds the stack,
and waits for the API health check:

```bash
./scripts/redeploy-remote.sh
```

MiniMax is the default. To deliberately switch the primary provider while retaining
automatic fallback, pass `kimi` or `minimax`:

```bash
./scripts/redeploy-remote.sh kimi
./scripts/redeploy-remote.sh minimax
```

On a server that predates this script, obtain it once with `git pull --ff-only origin
main`, then use the script for subsequent deployments. Backups are written to the
Git-ignored `backups/` directory with permissions restricted to the current user.

The ordered SQL migrations are idempotent and run before each API/worker rollout.
PostgreSQL and Caddy certificate state live in named Docker volumes.

## Backup and restore

Create a database backup on the server:

```bash
docker compose --env-file .env.remote -f compose.remote.yaml exec -T postgres \
  pg_dump -U assistant -d assistant -Fc > assistant-backup.dump
```

Keep backups outside the server as well. To restore into an empty database, stop the API
and worker, then use `pg_restore -U assistant -d assistant --clean --if-exists` through
the PostgreSQL container.

## Security boundary

- Caddy is the only public container and terminates HTTPS.
- Basic authentication protects the UI and every API path at the proxy.
- PostgreSQL and the worker are reachable only on the private Compose network.
- The merge-approval token is separate from the browser password.
- Provider and GitHub keys stay in the server-only `.env.remote` file.
- The general worker keeps code execution disabled. Coding runs only in the explicit
  `coding` Compose profile after startup preflight verifies clean repository identity,
  required executables, validation commands, and push access.

For a public multi-user service, replace this single-operator login with real accounts,
per-user authorization, rate limiting, and audit/retention policies. The Phase 1 stack
is intended for one trusted operator.
