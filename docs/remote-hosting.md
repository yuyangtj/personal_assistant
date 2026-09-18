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

## Start and verify

Validate the rendered Compose file before starting it:

```bash
docker compose --env-file .env.remote -f compose.remote.yaml config --quiet
docker compose --env-file .env.remote -f compose.remote.yaml up -d --build
docker compose --env-file .env.remote -f compose.remote.yaml ps
```

Visit `https://YOUR_DOMAIN/` and enter the configured browser credentials. You should
see the conversation console, where you can create chats, send messages, confirm task
proposals, inspect task status/results, continue a task, and return to its chat.

Useful operational commands:

```bash
docker compose --env-file .env.remote -f compose.remote.yaml logs -f api worker caddy
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
- Leave `ASSISTANT_CODE_AGENT_ENABLED=false` until a remote coding runtime and target
  repository are deliberately provisioned.

For a public multi-user service, replace this single-operator login with real accounts,
per-user authorization, rate limiting, and audit/retention policies. The Phase 1 stack
is intended for one trusted operator.
