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

## Approved post-merge deployments

The task UI can deploy a merged commit only after a second approval has authorized the
exact merge SHA. The server deploys itself: the API writes a request file into a spool
directory, and a systemd-managed **host deployer** outside Docker Compose runs
`scripts/redeploy-remote.sh` for it. The API never gets the Docker socket, and no SSH
key or GitHub Actions run is involved. The flow is drawn in
[`host-deployment-flow.svg`](host-deployment-flow.svg).

1. The API checks that the approved SHA is still the head of `main` (GitHub API) and
   that no coding run is executing, then writes `requests/<run>.json`.
2. `assistant-deployer.path` notices the file and starts `assistant-deployer.service`,
   which runs `scripts/host-deployer.sh` as the deploy user.
3. The deployer runs `redeploy-remote.sh` with the expected SHA. The script refuses a
   mismatched `origin/main`, backs up PostgreSQL, builds images tagged
   `personal-assistant:<sha>`, and waits for `/health`.
4. If the new release is unhealthy, the script restarts the previous release's images
   (kept locally, the newest three) and exits `3`. The run is then reported as
   `rolled_back`.
5. The deployer writes `status/<run>.json`. The worker reads it every 10 seconds, so
   the run finishes even though the API restarted during its own rollout. Full logs
   stay in `logs/<run>.log` on the host.

A deployment refuses to start while a coding run is executing, because the rollout
recreates the coding worker. Pass `"force": true` in the deployment workflow input to
override this.

### One-time setup

Harden the server first. These steps use the Hetzner console or your laptop:

- **Cloud Firewall.** Create a Hetzner Cloud Firewall for the server that allows TCP 22
  from your own IP only, plus TCP 80, TCP 443 and UDP 443. Docker publishes ports
  around `ufw`, so the cloud firewall is the layer that actually holds.
- **Swap.** Builds on a 4 GB server can run the stack out of memory. Add swap once:

  ```bash
  sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  ```

- **ARM64.** The cax11 is ARM64. `postgres` and `caddy` publish arm64 images; confirm
  that `docker compose ... --profile coding build coding-worker` succeeds before
  relying on the coding worker.
- **SSH.** Use key-only SSH login (`PasswordAuthentication no`).

Then install the deployer. Run these over SSH as the user that owns the checkout and is
in the `docker` group. The installer must run before the first redeploy, because the
API's spool mounts must exist on the host:

```bash
cd /srv/personal-assistant/repos/personal_assistant   # the trusted deployment checkout
git pull --ff-only origin main
sudo ./scripts/install-host-deployer.sh
./scripts/redeploy-remote.sh
systemctl status assistant-deployer.path
```

The installer creates `/srv/personal-assistant/deploy/{requests,processing,done,status,logs}`
owned by the deploy user, renders the units from `deploy/systemd/`, and enables them.
Use a different spool by setting `ASSISTANT_HOST_DEPLOY_SPOOL` for both the installer and
`.env.remote`.

After the first host deployment succeeds, remove the old GitHub Actions route's
credentials: delete the `DEPLOY_*` secrets from the GitHub `production` environment and
drop **Actions: write** from the API's GitHub token. The disabled
`personal-assistant-production-actions` target and `.github/workflows/deploy.yml` stay
in the repository as a fallback; re-enabling them needs those secrets again.

### Operating it

```bash
journalctl -u assistant-deployer.service -n 50        # what the deployer did
ls /srv/personal-assistant/deploy/status/              # reported runs
less /srv/personal-assistant/deploy/logs/<run>.log     # full redeploy output
```

To check the rollback path without shipping a broken commit:

```bash
ASSISTANT_DEPLOY_SIMULATE_HEALTH_FAILURE=1 ./scripts/redeploy-remote.sh
echo $?   # 3: the previous release is serving again
./scripts/redeploy-remote.sh                           # put the current release back
```

A request that is still in `processing/` after a reboot or a killed deploy is reported
as `failed` the next time the deployer starts, including at boot. Manual deploys with
`./scripts/redeploy-remote.sh` stay available at any time; they use the same image
tagging and rollback.

The ordered SQL migrations are idempotent and run before each API/worker rollout.
Keep them backward compatible, because a rollback runs the previous release's code
against the new schema. PostgreSQL and Caddy certificate state live in named Docker
volumes.

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
- No container has the Docker socket. Containers can only queue a deployment of the
  exact `origin/main` head through the spool; the host deployer re-verifies the SHA,
  accepts only its registered target, and runs as an unprivileged `docker`-group user.

For a public multi-user service, replace this single-operator login with real accounts,
per-user authorization, rate limiting, and audit/retention policies. The Phase 1 stack
is intended for one trusted operator.
