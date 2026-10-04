#!/usr/bin/env bash
# Processes deployment requests that the API drops into the deploy spool.
#
# Runs on the host, outside Docker Compose, from the systemd units installed by
# scripts/install-host-deployer.sh. The API only writes requests/<run>.json and reads
# status/<run>.json; this script is the only thing that runs redeploy-remote.sh.
set -Eeuo pipefail

SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIRECTORY}/.." && pwd)"
SPOOL="${ASSISTANT_HOST_DEPLOY_SPOOL:-/srv/personal-assistant/deploy}"
ENV_FILE="${ASSISTANT_DEPLOY_ENV_FILE:-${REPOSITORY_ROOT}/.env.remote}"
ALLOWED_TARGETS="${ASSISTANT_DEPLOYER_TARGETS:-personal-assistant-production}"
DRY_RUN="${ASSISTANT_DEPLOYER_DRY_RUN:-0}"
EXIT_ROLLED_BACK=3

timestamp() {
    date -u +%Y-%m-%dT%H:%M:%SZ
}

# write_status RUN_ID STATE STAGE COMMIT PREVIOUS STARTED FINISHED LOG_FILE
write_status() {
    python3 - "${SPOOL}/status" "$@" <<'PY'
import json
import os
import sys
import tempfile

directory, run_id, state, stage, commit, previous, started, finished, log_file = sys.argv[1:]
tail = ""
if log_file and os.path.exists(log_file):
    with open(log_file, errors="replace") as handle:
        tail = "".join(handle.readlines()[-80:])[-20000:]
document = {
    "workflow_run_id": run_id,
    "state": state,
    "stage": stage,
    "commit_sha": commit,
    "previous_sha": previous or None,
    "started_at": started,
    "finished_at": finished or None,
    "log_tail": tail,
}
descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".", suffix=".tmp")
with os.fdopen(descriptor, "w") as handle:
    json.dump(document, handle)
os.chmod(temporary, 0o644)
os.replace(temporary, os.path.join(directory, f"{run_id}.json"))
PY
}

# Prints run id, commit and target on three lines, or fails for a malformed document.
read_request() {
    python3 - "$1" <<'PY'
import json
import sys

with open(sys.argv[1]) as handle:
    document = json.load(handle)
for key in ("workflow_run_id", "commit_sha", "deployment_target_id"):
    value = document[key]
    if not isinstance(value, str) or "\n" in value:
        raise SystemExit(f"invalid {key}")
    print(value)
PY
}

target_allowed() {
    local allowed
    for allowed in ${ALLOWED_TARGETS//,/ }; do
        [[ "$1" == "${allowed}" ]] && return 0
    done
    return 1
}

run_redeploy() {
    local commit="$1" provider
    if [[ "${DRY_RUN}" == 1 ]]; then
        echo "Dry run: would deploy ${commit}"
        return "${ASSISTANT_DEPLOYER_DRY_RUN_EXIT:-0}"
    fi
    # Keep whichever conversation provider the operator last chose.
    provider="$(sed -n 's/^ASSISTANT_CONVERSATION_MODEL_PROVIDER=//p' "${ENV_FILE}" | tail -n 1)"
    case "${provider}" in
        minimax|kimi) ;;
        *) provider=minimax ;;
    esac
    ASSISTANT_DEPLOY_EXPECTED_SHA="${commit}" ASSISTANT_DEPLOY_ENV_FILE="${ENV_FILE}" \
        "${REPOSITORY_ROOT}/scripts/redeploy-remote.sh" "${provider}"
}

process_request() {
    local request="$1" name claimed fields run_id commit target
    local log_file started previous exit_code=0 state stage
    name="$(basename -- "${request}")"
    claimed="${SPOOL}/processing/${name}"
    mv -- "${request}" "${claimed}" || return 0

    if ! fields="$(read_request "${claimed}" 2>/dev/null)"; then
        echo "Rejecting malformed request ${name}" >&2
        mv -- "${claimed}" "${SPOOL}/done/${name}.rejected"
        return 0
    fi
    { read -r run_id; read -r commit; read -r target; } <<< "${fields}"
    if [[ ! "${run_id}" =~ ^[0-9a-f-]{36}$ || "${name}" != "${run_id}.json" \
        || ! "${commit}" =~ ^[0-9a-f]{40}$ ]]; then
        echo "Rejecting request ${name} with an invalid run id or commit" >&2
        mv -- "${claimed}" "${SPOOL}/done/${name}.rejected"
        return 0
    fi

    log_file="${SPOOL}/logs/${run_id}.log"
    started="$(timestamp)"
    previous="$(git -C "${REPOSITORY_ROOT}" rev-parse HEAD)"

    if [[ -f "${SPOOL}/status/${run_id}.json" ]]; then
        echo "Request ${run_id} was already processed; ignoring the duplicate" >&2
        mv -- "${claimed}" "${SPOOL}/done/${name}.duplicate"
        return 0
    fi
    if ! target_allowed "${target}"; then
        echo "Deployment target ${target} is not served by this host" >> "${log_file}"
        write_status "${run_id}" failed deploy "${commit}" "${previous}" \
            "${started}" "$(timestamp)" "${log_file}"
        mv -- "${claimed}" "${SPOOL}/done/${name}"
        return 0
    fi

    echo "Deploying ${commit} for workflow run ${run_id} (serving ${previous})"
    write_status "${run_id}" running deploy "${commit}" "${previous}" "${started}" "" ""
    run_redeploy "${commit}" >> "${log_file}" 2>&1 || exit_code=$?
    case "${exit_code}" in
        0) state=succeeded stage=promote ;;
        "${EXIT_ROLLED_BACK}") state=rolled_back stage=health_check ;;
        *) state=failed stage=deploy ;;
    esac
    write_status "${run_id}" "${state}" "${stage}" "${commit}" "${previous}" \
        "${started}" "$(timestamp)" "${log_file}"
    mv -- "${claimed}" "${SPOOL}/done/${name}"
    echo "Workflow run ${run_id}: ${state} (exit ${exit_code})"
}

# A request still in processing/ means the host stopped mid-deploy (reboot, OOM kill).
recover_interrupted() {
    local claimed fields run_id commit target log_file
    for claimed in "${SPOOL}"/processing/*.json; do
        if fields="$(read_request "${claimed}" 2>/dev/null)"; then
            { read -r run_id; read -r commit; read -r target; } <<< "${fields}"
            if [[ "${run_id}" =~ ^[0-9a-f-]{36}$ && "${commit}" =~ ^[0-9a-f]{40}$ ]]; then
                log_file="${SPOOL}/logs/${run_id}.log"
                echo "Deployment was interrupted before it finished" >> "${log_file}"
                write_status "${run_id}" failed deploy "${commit}" "" \
                    "$(timestamp)" "$(timestamp)" "${log_file}"
            fi
        fi
        mv -- "${claimed}" "${SPOOL}/done/$(basename -- "${claimed}").interrupted"
    done
}

main() {
    local directory request
    for directory in requests processing "done" status logs; do
        if [[ ! -d "${SPOOL}/${directory}" ]]; then
            echo "Missing ${SPOOL}/${directory}; run scripts/install-host-deployer.sh" >&2
            exit 1
        fi
    done
    umask 022
    shopt -s nullglob

    exec 9> "${SPOOL}/.lock"
    flock 9

    recover_interrupted
    # Keep going until no request is left, including ones queued during a deploy.
    while true; do
        local requests=("${SPOOL}"/requests/*.json)
        [[ ${#requests[@]} -eq 0 ]] && break
        for request in "${requests[@]}"; do
            process_request "${request}"
        done
    done
}

# redeploy-remote.sh pulls a new version of this file; bash has already parsed main.
main "$@"; exit $?
