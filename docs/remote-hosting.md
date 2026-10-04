# Remote hosting

This deployment publishes the browser console at `https://YOUR_DOMAIN/`. Caddy obtains
and renews the TLS certificate automatically and requires a username/password before it
proxies any request. The API, worker, and PostgreSQL have no public host ports.

The Android app remains a local/USB client in this phase. Its remote authentication is
Phase 2 and does not need to be configured yet.

## Server layout

The server has two accounts and keeps the deployment checkout apart from the coding
worker's clones. Both write to their own Git directories, so they must not share one.

| Account | Logs in? | Owns | Purpose |
| --- | --- | --- | --- |
| `<deploy-user>` | Yes, over SSH with your key; in the `docker` group | `/srv/personal-assistant/app`, `/srv/personal-assistant/deploy` | Runs Docker Compose, `redeploy-remote.sh`, and the host deployer |
| `assistant-coder` | No (`/usr/sbin/nologin`, no sudo, not in `docker`) | `/srv/personal-assistant/repos`, `/srv/personal-assistant/worktrees` | Identity the `coding-worker` container runs as |

| Path | Contents |
| --- | --- |
| `/srv/personal-assistant/app` | Deployment checkout: `.env.remote`, `backups/`, the scripts you run |
| `/srv/personal-assistant/deploy` | Host deployer spool: `requests/`, `processing/`, `done/`, `status/`, `logs/` |
| `/srv/personal-assistant/repos/<repo>` | Clones the coding agent fetches into and branches from |
| `/srv/personal-assistant/worktrees` | Per-task worktrees the coding agent edits |

`root` is only for the one-time setup below and can be locked out afterwards.

## Server and DNS

Use a small Linux server with a public IP, Docker Engine, Docker Compose v2, and at
least 2 GB RAM. Point a DNS `A` record (and `AAAA` if applicable) such as
`assistant.example.com` to the server.

Harden it before deploying:

- **Cloud Firewall.** Create a Hetzner Cloud Firewall for the server that allows TCP 22
  from your own IP only, plus TCP 80, TCP 443 and UDP 443. Do not expose ports 5432 or
  8000. Docker publishes ports around `ufw`, so the cloud firewall is the layer that
  actually holds.
- **Swap.** Image builds on a 4 GB server can run the stack out of memory. Add swap once:

  ```bash
  sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  ```

- **ARM64.** Hetzner `cax` servers are ARM64. `postgres` and `caddy` publish arm64
  images; confirm the coding worker builds (`docker compose ... --profile coding build
  coding-worker`) before relying on it.

## Accounts and SSH access

SSH uses key pairs. Your laptop keeps the private key; each server account has its own
`~/.ssh/authorized_keys` listing the public keys allowed to log in *as that account*.
Hetzner put your key into `root`'s list when the server was created. The same public
key can be added to several accounts, so one laptop key can log in as `root` and as the
deploy user. Give each additional device its own key pair and add its public key next to
the first, so one device can be revoked without the others.

### Create the deploy user

As `root`:

```bash
adduser <deploy-user>                      # interactive; gives it /bin/bash
usermod -aG docker <deploy-user>
install -d -m 700 -o <deploy-user> -g <deploy-user> /home/<deploy-user>/.ssh
install -m 600 -o <deploy-user> -g <deploy-user> \
  /root/.ssh/authorized_keys /home/<deploy-user>/.ssh/authorized_keys
```

### Verify the key

On the server, list the installed keys and their fingerprints. SSH ignores the file
unless `.ssh` is `700`, `authorized_keys` is `600`, and both are owned by the user:

```bash
sudo ls -la /home/<deploy-user>/.ssh/
sudo ssh-keygen -lf /home/<deploy-user>/.ssh/authorized_keys
```

On your laptop, compare with your key's fingerprint (your key may be `id_rsa` instead;
`ls ~/.ssh` shows which). The Hetzner console shows keys in MD5 format:

```bash
ssh-keygen -lf ~/.ssh/id_ed25519.pub              # SHA256:... format
ssh-keygen -E md5 -lf ~/.ssh/id_ed25519.pub       # c4:49:... format, as in Hetzner
```

Then test a direct login, keeping your `root` session open until it works:

```bash
ssh <deploy-user>@<server-ip> whoami      # prints <deploy-user>, no password prompt
ssh -v <deploy-user>@<server-ip>          # if refused: shows which key was offered and why
```

`Permission denied (publickey)` almost always means wrong permissions or a root-owned
`authorized_keys`.

Optionally add a shortcut to `~/.ssh/config` on your laptop, so `ssh assistant` works:

```
Host assistant
    HostName <server-ip>
    User <deploy-user>
```

### Switch between users on the server

```bash
su - <deploy-user>        # from root; `sudo -iu <deploy-user>` from a sudo user
whoami
exit                      # back to the previous user
```

`This account is currently not available` means the target account's shell is
`/usr/sbin/nologin`. That is intended for `assistant-coder`, which nobody logs into. Run
single commands as it instead, or override the shell for one session:

```bash
getent passwd assistant-coder                          # last field is the login shell
sudo -u assistant-coder git -C /srv/personal-assistant/repos/personal_assistant status
sudo su -s /bin/bash - assistant-coder                 # one-off interactive shell
```

If the deploy user shows that message, it was created without a shell; fix it with
`sudo usermod -s /bin/bash <deploy-user>`.

### Lock down root

Once logging in as the deploy user works, set these in `/etc/ssh/sshd_config` and run
`sudo systemctl restart ssh`, again keeping a session open until a fresh login succeeds:

```
PermitRootLogin no
PasswordAuthentication no
```

## Deployment checkout and environment

As `root` (or with sudo), create the checkout directory for the deploy user, then clone
and configure as that user:

```bash
sudo install -d -o <deploy-user> -g <deploy-user> /srv/personal-assistant/app
su - <deploy-user>
git clone https://github.com/yuyangtj/personal_assistant.git /srv/personal-assistant/app
cd /srv/personal-assistant/app
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

## Coding worker (optional)

The normal `worker` handles chat and non-coding tasks. The separate `coding-worker`
contains Git, uv, Kimi Code, and Claude Code and has no public port or Docker socket. It
runs with all Linux capabilities dropped, as the unprivileged `assistant-coder` host
account, and only receives the explicitly registered repository mounts plus its worktree
directory.

Create the account, its directories, and its clones as `root`:

```bash
useradd --system --create-home --home-dir /var/lib/assistant-coder \
  --shell /usr/sbin/nologin assistant-coder
install -d -o assistant-coder -g assistant-coder -m 750 \
  /srv/personal-assistant/repos /srv/personal-assistant/worktrees
sudo -u assistant-coder git clone https://github.com/yuyangtj/personal_assistant.git \
  /srv/personal-assistant/repos/personal_assistant
sudo -u assistant-coder git clone https://github.com/yuyangtj/analytics-agent-playground.git \
  /srv/personal-assistant/repos/analytics-agent-playground
id -u assistant-coder
id -g assistant-coder
```

Set these `.env.remote` fields to the printed UID/GID and the paths above.
`COMPOSE_PROFILES=coding` makes every Compose command, including `redeploy-remote.sh`
and the host deployer, include the coding worker:

```dotenv
COMPOSE_PROFILES=coding
ASSISTANT_CODING_WORKER_UID=<uid>
ASSISTANT_CODING_WORKER_GID=<gid>
ASSISTANT_HOST_REPOSITORY_PERSONAL_ASSISTANT_PATH=/srv/personal-assistant/repos/personal_assistant
ASSISTANT_HOST_REPOSITORY_ANALYTICS_AGENT_PLAYGROUND_PATH=/srv/personal-assistant/repos/analytics-agent-playground
ASSISTANT_HOST_CODE_WORKTREE_ROOT=/srv/personal-assistant/worktrees
KIMI_API_KEY=...
MINIMAX_API_KEY=...
ASSISTANT_GITHUB_TOKEN=...
```

Use clean HTTPS clones whose `origin` matches the repository manifests. The fine-grained
GitHub token must be restricted to the registered repositories with Contents and Pull
requests write and Checks read. It reaches the container only through the askpass
helper; the `assistant-coder` account itself holds no credentials. Keep
`ASSISTANT_APPROVAL_TOKEN` separate; it is supplied only to the API and is never
available to either coding CLI.

### Adding a coding repository

Two steps grant access and stay manual, so the assistant can never widen its own reach:

1. **GitHub token.** In GitHub → Settings → Developer settings → Fine-grained tokens,
   edit the token and add the repository under *Repository access*. Editing keeps the
   token value, so `.env.remote` is unchanged. Repositories owned by an organization may
   need an owner to approve the token.
2. **Clone as `assistant-coder`:**

   ```bash
   sudo -u assistant-coder git clone https://github.com/<owner>/<repo>.git \
     /srv/personal-assistant/repos/<repo>
   ```

The rest is a normal pull request to this repository:

3. `repositories/<id>.yaml`, modelled on `repositories/analytics-agent-playground.yaml`:
   `github_repository`, `path_env` (for example `ASSISTANT_REPOSITORY_<NAME>_PATH`),
   `base_branch`, `validation_profile`, `required_checks`, and `aliases` for naming it in
   chat.
4. `validation-profiles/<id>.yaml` with the commands that verify the agent's work. They
   run inside the coding-worker container.
5. In `compose.remote.yaml`, give `coding-worker` the environment variable
   `ASSISTANT_REPOSITORY_<NAME>_PATH: /workspace/repos/<repo>` and a bind mount from
   `${ASSISTANT_HOST_REPOSITORY_<NAME>_PATH}` to `/workspace/repos/<repo>`. Set
   `ASSISTANT_HOST_REPOSITORY_<NAME>_PATH` in `.env.remote`.
6. If the validation commands need a toolchain the image lacks (it has Python, uv,
   Node, and Git), add it to `Dockerfile.coding-worker`.
7. Optionally, a `deployment-targets/` entry if the assistant should also deploy it.

After merging and redeploying, the coding worker's startup log shows whether preflight
accepted the repository.

## First start

As the deploy user in `/srv/personal-assistant/app`. The host deployer must be installed
before the first start, because the API mounts its spool directories and Compose
refuses missing bind-mount sources:

```bash
docker compose --env-file .env.remote -f compose.remote.yaml config --quiet
sudo ./scripts/install-host-deployer.sh
./scripts/redeploy-remote.sh
systemctl status assistant-deployer.path
```

The installer creates `/srv/personal-assistant/deploy/{requests,processing,done,status,logs}`
owned by the deploy user, renders the units from `deploy/systemd/`, and enables them.
Use a different spool by setting `ASSISTANT_HOST_DEPLOY_SPOOL` for both the installer and
`.env.remote`.

Visit `https://YOUR_DOMAIN/` and enter the configured browser credentials. You should
see the conversation console, where you can create chats, send messages, confirm task
proposals, inspect task status/results, continue a task, and return to its chat.

## Deploying manually over SSH

`scripts/redeploy-remote.sh` updates the server to the latest `main` and restarts the
stack safely:

1. Requires `.env.remote` and `compose.remote.yaml`, and stops if tracked files have
   local changes.
2. Pulls `origin/main` (fast-forward only). When the host deployer calls it, it also
   refuses unless `main` is exactly the approved commit.
3. Keeps the primary conversation provider already set in `.env.remote`, or switches it
   when you pass `kimi` or `minimax`; automatic fallback stays on.
4. Validates the Compose configuration.
5. Backs up PostgreSQL to `backups/assistant-<time>.dump` if the database is running,
   keeping the newest 20 dumps.
6. Builds and starts the release with images tagged by commit
   (`personal-assistant:<sha>`); migrations run first.
7. Waits up to 60 seconds for `/health`.
8. If healthy, records the release as current and prunes images beyond the newest three
   releases. If not, restarts the previous release's images without rebuilding.

Exit codes: `0` deployed and healthy; `3` new release unhealthy and the previous one is
serving again; anything else failed (including "no previous release to roll back to").

From your laptop:

```bash
ssh -t <deploy-user>@<server-ip> 'cd /srv/personal-assistant/app && ./scripts/redeploy-remote.sh'
```

Or log in first and run `./scripts/redeploy-remote.sh` (or `... kimi` to switch the
primary provider). `-t` streams the build output and lets Ctrl-C stop it. If the
connection might drop during a build, run it inside `tmux new -s deploy` and reattach
with `tmux attach -t deploy`; a dropped plain session can kill the deploy halfway.
Afterwards, `echo $?` shows the exit code.

Run it as the deploy user, never as `root`: root-owned files in the checkout would stop
the host deployer from pulling.

## Approved post-merge deployments

The task UI can deploy a merged commit only after a second approval has authorized the
exact merge SHA. The server deploys itself: the API writes a request file into the spool,
and the systemd-managed **host deployer** outside Docker Compose runs
`scripts/redeploy-remote.sh` for it. The API never gets the Docker socket, and no SSH
key or GitHub Actions run is involved. The flow is drawn in
[`host-deployment-flow.svg`](host-deployment-flow.svg).

1. The API checks that the approved SHA is still the head of `main` (GitHub API) and
   that no coding run is executing, then writes `requests/<run>.json`.
2. `assistant-deployer.path` notices the file and starts `assistant-deployer.service`,
   which runs `scripts/host-deployer.sh` as the deploy user.
3. The deployer runs `redeploy-remote.sh` with the expected SHA, keeping the
   conversation provider already set in `.env.remote`.
4. Exit code `3` is reported as `rolled_back`, any other failure as `failed`.
5. The deployer writes `status/<run>.json`. The worker reads it every 10 seconds, so
   the run finishes even though the API restarted during its own rollout. Full logs
   stay in `logs/<run>.log` on the host.

A deployment refuses to start while a coding run is executing, because the rollout
recreates the coding worker. Pass `"force": true` in the deployment workflow input to
override this.

After the first host deployment succeeds, remove the old GitHub Actions route's
credentials: delete the `DEPLOY_*` secrets from the GitHub `production` environment and
drop **Actions: write** from the API's GitHub token. The disabled
`personal-assistant-production-actions` target and `.github/workflows/deploy.yml` stay
in the repository as a fallback; re-enabling them needs those secrets again.

To check the rollback path without shipping a broken commit:

```bash
ASSISTANT_DEPLOY_SIMULATE_HEALTH_FAILURE=1 ./scripts/redeploy-remote.sh
echo $?   # 3: the previous release is serving again
./scripts/redeploy-remote.sh             # put the current release back
```

A request that is still in `processing/` after a reboot or a killed deploy is reported
as `failed` the next time the deployer starts, including at boot.

The ordered SQL migrations are idempotent and run before each API/worker rollout.
Keep them backward compatible, because a rollback runs the previous release's code
against the new schema. PostgreSQL and Caddy certificate state live in named Docker
volumes.

## Command reference

Every Compose command takes the same prefix: `--env-file .env.remote` fills the `${...}`
values, and `-f compose.remote.yaml` selects the production stack. Define a shell alias
to save typing:

```bash
alias dc='docker compose --env-file .env.remote -f compose.remote.yaml'
```

| Command | What it does |
| --- | --- |
| `dc ps` | Lists the containers and their state. |
| `dc logs -f api worker coding-worker caddy` | Follows the merged logs of those services; Ctrl-C stops following. Add `--tail=100` to skip old history. Changes nothing. |
| `dc pull --ignore-buildable` | Downloads newer `postgres` and `caddy` images; running containers keep the old ones until the next deploy. Without `--ignore-buildable` it also tries to pull the locally built `personal-assistant` images from Docker Hub and fails. |
| `./scripts/redeploy-remote.sh` | Rebuilds and restarts; use this instead of `dc up -d --build`, which tags images `local` and skips backups, health checks, and rollback bookkeeping. |
| `journalctl -u assistant-deployer.service -n 50` | What the host deployer did. |
| `ls /srv/personal-assistant/deploy/status/` | Deployments the host deployer reported. |
| `less /srv/personal-assistant/deploy/logs/<run>.log` | Full redeploy output for one approved deployment. |

## Backup and restore

`redeploy-remote.sh` backs up before every deployment into the Git-ignored `backups/`
directory, readable only by the deploy user. To create one by hand:

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
- SSH accepts keys only, and only for the deploy user once `root` login is disabled.
- The general worker keeps code execution disabled. Coding runs only in the explicit
  `coding` Compose profile, as the no-login `assistant-coder` account, after startup
  preflight verifies clean repository identity, required executables, validation
  commands, and push access.
- Granting the coding agent a new repository requires the operator: the token's
  repository list and the `assistant-coder` clone are never changed by the assistant.
- No container has the Docker socket. Containers can only queue a deployment of the
  exact `origin/main` head through the spool; the host deployer re-verifies the SHA,
  accepts only its registered target, and runs as an unprivileged `docker`-group user.

For a public multi-user service, replace this single-operator login with real accounts,
per-user authorization, rate limiting, and audit/retention policies. The stack is
intended for one trusted operator.
