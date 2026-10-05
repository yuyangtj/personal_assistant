#!/usr/bin/env bash
# Rebuilds and restarts the remote stack at origin/main.
#
# Exit codes: 0 deployed and healthy; 3 new release unhealthy and the previous release
# was restored; any other non-zero value means the deployment failed.
set -Eeuo pipefail

SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIRECTORY}/.." && pwd)"
ENV_FILE="${ASSISTANT_DEPLOY_ENV_FILE:-${REPOSITORY_ROOT}/.env.remote}"
COMPOSE_FILE="${REPOSITORY_ROOT}/compose.remote.yaml"
DEPLOY_BRANCH="${ASSISTANT_DEPLOY_BRANCH:-main}"
EXPECTED_SHA="${ASSISTANT_DEPLOY_EXPECTED_SHA:-}"
SIMULATE_HEALTH_FAILURE="${ASSISTANT_DEPLOY_SIMULATE_HEALTH_FAILURE:-0}"
CURRENT_TAG_FILE="${REPOSITORY_ROOT}/.deploy-current-tag"
RELEASES_TO_KEEP=3
EXIT_ROLLED_BACK=3
BACKUPS_TO_KEEP=20
PROVIDER="${1:-}"

usage() {
    echo "Usage: $0 [minimax|kimi]  (default: keep the provider set in .env.remote)" >&2
}

wait_for_health() {
    local _
    for _ in $(seq 1 30); do
        if "${compose[@]}" exec -T api python -c \
            'import json, urllib.request; assert json.load(urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2))["status"] == "ok"' \
            >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    return 1
}

prune_release_images() {
    # `docker image ls` lists newest first; keep the serving release plus the newest
    # older ones so a rollback never needs a rebuild.
    local repository tag
    for repository in personal-assistant personal-assistant-coding personal-assistant-mcp-tools; do
        docker image ls "${repository}" --format '{{.Tag}}' \
            | grep -E '^[0-9a-f]{40}$' \
            | grep -vx "${release_tag}" \
            | tail -n +"${RELEASES_TO_KEEP}" \
            | while read -r tag; do
                docker image rm "${repository}:${tag}" >/dev/null 2>&1 || true
            done || true  # grep finds nothing on the first releases
    done
}

set_env_value() {
    local key="$1"
    local value="$2"
    if grep -q "^${key}=" "${ENV_FILE}"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "${ENV_FILE}"
    else
        printf '\n%s=%s\n' "${key}" "${value}" >> "${ENV_FILE}"
    fi
}

case "${PROVIDER}" in
    ""|minimax|kimi) ;;
    *)
        usage
        exit 2
        ;;
esac

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Missing ${ENV_FILE}. Copy .env.remote.example and configure it first." >&2
    exit 1
fi

if [[ -z "${PROVIDER}" ]]; then
    # Keep the operator's current choice; only an explicit argument switches it.
    PROVIDER="$(sed -n 's/^ASSISTANT_CONVERSATION_MODEL_PROVIDER=//p' "${ENV_FILE}" | tail -n 1)"
    case "${PROVIDER}" in
        minimax|kimi) ;;
        *) PROVIDER=minimax ;;
    esac
fi

if [[ ! -f "${COMPOSE_FILE}" ]]; then
    echo "Missing ${COMPOSE_FILE}. Run this script from a complete repository clone." >&2
    exit 1
fi

cd "${REPOSITORY_ROOT}"

if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Tracked files have local changes. Commit or restore them before redeploying." >&2
    exit 1
fi

echo "Pulling origin/${DEPLOY_BRANCH}..."
git pull --ff-only origin "${DEPLOY_BRANCH}"
# The private MCP servers live next to this checkout (built by the "mcp" profile).
MCP_SERVERS_DIRECTORY="${REPOSITORY_ROOT}/../mcp-servers"
if [[ -d "${MCP_SERVERS_DIRECTORY}/.git" ]]; then
    git -C "${MCP_SERVERS_DIRECTORY}" pull --ff-only --quiet
fi

if [[ -n "${EXPECTED_SHA}" ]]; then
    if [[ ! "${EXPECTED_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
        echo "ASSISTANT_DEPLOY_EXPECTED_SHA must be a full lowercase Git SHA." >&2
        exit 1
    fi
    deployed_sha="$(git rev-parse HEAD)"
    if [[ "${deployed_sha}" != "${EXPECTED_SHA}" ]]; then
        echo "Refusing deployment: origin/${DEPLOY_BRANCH} is ${deployed_sha}, expected ${EXPECTED_SHA}." >&2
        exit 1
    fi
fi

set_env_value "ASSISTANT_CONVERSATION_MODEL_PROVIDER" "${PROVIDER}"
set_env_value "ASSISTANT_CONVERSATION_MODEL_FALLBACK_PROVIDER" "auto"
echo "Conversation provider: ${PROVIDER} (automatic fallback enabled)"

# This script treats .env.remote as authoritative. Exported shell values otherwise
# take precedence over --env-file and can silently retain an older provider or raw hash.
unset ASSISTANT_CONVERSATION_MODEL_PROVIDER
unset ASSISTANT_CONVERSATION_MODEL_FALLBACK_PROVIDER
unset ASSISTANT_WEB_PASSWORD_HASH

compose=(docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}")
"${compose[@]}" config --quiet

if "${compose[@]}" ps --status running --services 2>/dev/null | grep -qx postgres; then
    backup_directory="${REPOSITORY_ROOT}/backups"
    backup_path="${backup_directory}/assistant-$(date -u +%Y%m%dT%H%M%SZ).dump"
    mkdir -p "${backup_directory}"
    umask 077
    echo "Backing up PostgreSQL to ${backup_path}..."
    "${compose[@]}" exec -T postgres pg_dump -U assistant -d assistant -Fc > "${backup_path}"
    # Timestamped names sort chronologically; keep only the newest dumps.
    find "${backup_directory}" -maxdepth 1 -type f -name 'assistant-*.dump' \
        | sort -r | tail -n +"$((BACKUPS_TO_KEEP + 1))" | xargs -r rm -f --
fi

release_tag="$(git rev-parse HEAD)"
previous_tag=""
if [[ -f "${CURRENT_TAG_FILE}" ]]; then
    previous_tag="$(tr -d '[:space:]' < "${CURRENT_TAG_FILE}")"
fi

echo "Building and starting release ${release_tag}..."
export ASSISTANT_IMAGE_TAG="${release_tag}"
"${compose[@]}" up -d --build --remove-orphans

echo "Waiting for the API health check..."
healthy=false
if [[ "${SIMULATE_HEALTH_FAILURE}" == 1 ]]; then
    echo "ASSISTANT_DEPLOY_SIMULATE_HEALTH_FAILURE=1: treating the health check as failed." >&2
elif wait_for_health; then
    healthy=true
fi

if [[ "${healthy}" != true ]]; then
    echo "Release ${release_tag} did not become healthy within 60 seconds." >&2
    "${compose[@]}" ps >&2
    "${compose[@]}" logs --tail=80 api migrate postgres >&2
    if [[ ! "${previous_tag}" =~ ^[0-9a-f]{40}$ || "${previous_tag}" == "${release_tag}" ]]; then
        echo "No earlier release image is recorded, so there is nothing to roll back to." >&2
        exit 1
    fi
    echo "Rolling back to release ${previous_tag}..." >&2
    export ASSISTANT_IMAGE_TAG="${previous_tag}"
    # Migrations are backward compatible, so the previous images run on the new schema.
    if "${compose[@]}" up -d --no-build --remove-orphans && wait_for_health; then
        echo "Rolled back: release ${previous_tag} is serving." >&2
        exit "${EXIT_ROLLED_BACK}"
    fi
    echo "Rollback to ${previous_tag} also failed to become healthy." >&2
    exit 1
fi

printf '%s\n' "${release_tag}" > "${CURRENT_TAG_FILE}"
prune_release_images

# `up -d` leaves Caddy running for a config-only change, and its single-file mount keeps
# the old Caddyfile after a checkout replaces it: recreate it when the two differ.
if ! "${compose[@]}" exec -T caddy cat /etc/caddy/Caddyfile 2>/dev/null \
    | cmp -s - "${REPOSITORY_ROOT}/deploy/Caddyfile"; then
    echo "The Caddyfile changed; restarting the web proxy..."
    "${compose[@]}" up -d --no-deps --force-recreate caddy
fi

"${compose[@]}" ps
echo "Deployment complete: https://$(sed -n 's/^ASSISTANT_DOMAIN=//p' "${ENV_FILE}" | tail -n 1)"
