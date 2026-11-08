#!/usr/bin/env bash
# The only command GitHub Actions' deploy key can run on the server.
#
# The deploy user's authorized_keys pins that key to this script (a forced command), so
# the key can't open a shell. It accepts exactly "deploy <commit sha> [force]", hands the
# deployment to the host deployer through its spool (the same route as the console:
# one lock, logs, rollback, status), prints the deploy log as it grows, and exits with
# the outcome: 0 deployed or skipped, 3 rolled back, 1 failed, 2 a malformed command.
#
# A commit that is no longer the head of main is skipped: the newer commit deploys in
# its own run, and redeploy-remote.sh refuses anything but main's head anyway. Without
# "force" it waits for an executing coding run to finish, since a rollout restarts the
# coding worker.
#
# The ASSISTANT_CI_DEPLOY_* variables exist for tests; sshd doesn't pass a client's
# environment to a forced command, so a caller can't set them.
set -Eeuo pipefail

SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIRECTORY}/.." && pwd)"
SPOOL="${ASSISTANT_HOST_DEPLOY_SPOOL:-/srv/personal-assistant/deploy}"
ENV_FILE="${ASSISTANT_DEPLOY_ENV_FILE:-${REPOSITORY_ROOT}/.env.remote}"
TARGET=personal-assistant-production
CODING_WAIT_SECONDS="${ASSISTANT_CI_DEPLOY_CODING_WAIT_SECONDS:-3600}"
DEPLOY_TIMEOUT_SECONDS="${ASSISTANT_CI_DEPLOY_TIMEOUT_SECONDS:-3000}"
POLL_SECONDS="${ASSISTANT_CI_DEPLOY_POLL_SECONDS:-10}"

usage() {
    echo "Usage: deploy <40-character commit sha> [force]" >&2
    exit 2
}

# The head of main on GitHub, or nothing when it can't be read.
main_head() {
    if [[ -n "${ASSISTANT_CI_DEPLOY_MAIN_SHA:-}" ]]; then
        echo "${ASSISTANT_CI_DEPLOY_MAIN_SHA}"
        return
    fi
    git -C "${REPOSITORY_ROOT}" ls-remote origin refs/heads/main 2>/dev/null | cut -f1
}

superseded() {
    local head
    head="$(main_head)"
    [[ -n "${head}" && "${head}" != "$1" ]]
}

# How many coding runs are executing; fails when the database can't be asked.
executing_coding_runs() {
    if [[ -n "${ASSISTANT_CI_DEPLOY_CODING_RUNS_FILE:-}" ]]; then
        cat "${ASSISTANT_CI_DEPLOY_CODING_RUNS_FILE}"
        return
    fi
    docker compose --env-file "${ENV_FILE}" -f "${REPOSITORY_ROOT}/compose.remote.yaml" \
        exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "
            select count(*) from tasks t join coding_runs c on c.task_id = t.id
            where t.status = '"'"'executing'"'"'"'
}

wait_for_coding_runs() {
    local commit="$1" started="${SECONDS}" count announced=""
    while true; do
        if ! count="$(executing_coding_runs 2>/dev/null)" || [[ ! "${count}" =~ ^[0-9]+$ ]]; then
            echo "Couldn't check for coding runs (is the database down?); deploying anyway."
            return 0
        fi
        (( count == 0 )) && return 0
        if (( SECONDS - started >= CODING_WAIT_SECONDS )); then
            echo "A coding run is still executing after ${CODING_WAIT_SECONDS}s; not deploying." >&2
            echo "Run the deploy workflow again with force to interrupt it." >&2
            return 1
        fi
        [[ -n "${announced:-}" ]] || echo "A coding run is executing; waiting for it to finish..."
        announced=1
        sleep "${POLL_SECONDS}"
        if superseded "${commit}"; then
            echo "main has moved past ${commit}; the newer commit deploys in its own run."
            exit 0
        fi
    done
}

# Writes requests/<run>.json the way the API does, atomically.
submit_request() {
    python3 - "${SPOOL}/requests" "$1" "$2" "${TARGET}" <<'PY'
import datetime
import json
import os
import sys
import tempfile

directory, run_id, commit, target = sys.argv[1:]
document = {
    "workflow_run_id": run_id,
    "commit_sha": commit,
    "deployment_target_id": target,
    "requested_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "requested_by": "github-actions",
}
descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".", suffix=".tmp")
with os.fdopen(descriptor, "w") as handle:
    json.dump(document, handle)
os.chmod(temporary, 0o644)
os.replace(temporary, os.path.join(directory, f"{run_id}.json"))
PY
}

status_state() {
    python3 - "$1" <<'PY' 2>/dev/null || true
import json
import sys

with open(sys.argv[1]) as handle:
    print(json.load(handle).get("state", ""))
PY
}

# Prints the deploy log as it grows until the deployer reports an outcome.
follow_deployment() {
    local run_id="$1" log_file="${SPOOL}/logs/$1.log" status_file="${SPOOL}/status/$1.json"
    local printed=0 size state="" started="${SECONDS}"
    while true; do
        if [[ -f "${log_file}" ]]; then
            size="$(wc -c < "${log_file}")"
            if (( size > printed )); then
                tail -c "+$(( printed + 1 ))" "${log_file}" | head -c "$(( size - printed ))"
                printed="${size}"
            fi
        fi
        [[ -f "${status_file}" ]] && state="$(status_state "${status_file}")"
        case "${state}" in
            succeeded) echo "Deployed."; return 0 ;;
            rolled_back) echo "The release failed its health checks and was rolled back." >&2; return 3 ;;
            failed) echo "The deployment failed; the log above says why." >&2; return 1 ;;
        esac
        if (( SECONDS - started >= DEPLOY_TIMEOUT_SECONDS )); then
            echo "No outcome after ${DEPLOY_TIMEOUT_SECONDS}s; deployment ${run_id} may still be" \
                "running. Its log is ${log_file} on the server." >&2
            return 1
        fi
        sleep "${POLL_SECONDS}"
    done
}

main() {
    local words commit force=0 run_id
    read -r -a words <<< "${SSH_ORIGINAL_COMMAND-$*}"
    (( ${#words[@]} == 2 || ${#words[@]} == 3 )) || usage
    [[ "${words[0]}" == deploy && "${words[1]}" =~ ^[0-9a-f]{40}$ ]] || usage
    commit="${words[1]}"
    if (( ${#words[@]} == 3 )); then
        [[ "${words[2]}" == force ]] || usage
        force=1
    fi
    if [[ ! -d "${SPOOL}/requests" ]]; then
        echo "The host deployer isn't installed (no ${SPOOL}/requests)." >&2
        exit 1
    fi

    if superseded "${commit}"; then
        echo "main has moved past ${commit}; the newer commit deploys in its own run."
        exit 0
    fi
    if (( force == 0 )); then
        wait_for_coding_runs "${commit}" || exit 1
    fi

    run_id="$(python3 -c 'import uuid; print(uuid.uuid4())')"
    submit_request "${run_id}" "${commit}"
    echo "Queued deployment ${run_id} of ${commit}."
    follow_deployment "${run_id}"
}

# The deploy pulls a new version of this file; bash has already parsed main.
main "$@"; exit $?
