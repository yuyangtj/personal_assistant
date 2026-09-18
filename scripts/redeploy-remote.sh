#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIRECTORY}/.." && pwd)"
ENV_FILE="${ASSISTANT_DEPLOY_ENV_FILE:-${REPOSITORY_ROOT}/.env.remote}"
COMPOSE_FILE="${REPOSITORY_ROOT}/compose.remote.yaml"
DEPLOY_BRANCH="${ASSISTANT_DEPLOY_BRANCH:-main}"
PROVIDER="${1:-minimax}"

usage() {
    echo "Usage: $0 [minimax|kimi]" >&2
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
    minimax|kimi) ;;
    *)
        usage
        exit 2
        ;;
esac

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Missing ${ENV_FILE}. Copy .env.remote.example and configure it first." >&2
    exit 1
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
fi

echo "Building and starting the remote stack..."
"${compose[@]}" up -d --build --remove-orphans

echo "Waiting for the API health check..."
healthy=false
for _ in $(seq 1 30); do
    if "${compose[@]}" exec -T api python -c \
        'import json, urllib.request; assert json.load(urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2))["status"] == "ok"' \
        >/dev/null 2>&1; then
        healthy=true
        break
    fi
    sleep 2
done

if [[ "${healthy}" != true ]]; then
    echo "Deployment started, but the API did not become healthy within 60 seconds." >&2
    "${compose[@]}" ps >&2
    "${compose[@]}" logs --tail=80 api migrate postgres >&2
    exit 1
fi

"${compose[@]}" ps
echo "Deployment complete: https://$(sed -n 's/^ASSISTANT_DOMAIN=//p' "${ENV_FILE}" | tail -n 1)"
