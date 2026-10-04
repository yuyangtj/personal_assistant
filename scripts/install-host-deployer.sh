#!/usr/bin/env bash
# Installs the host deployer: spool directories plus the systemd service and path units.
#
# Run once on the server with sudo, from the trusted checkout, as the user that owns
# that checkout and runs Docker Compose:
#   sudo ./scripts/install-host-deployer.sh
set -Eeuo pipefail

SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIRECTORY}/.." && pwd)"
SPOOL="${ASSISTANT_HOST_DEPLOY_SPOOL:-/srv/personal-assistant/deploy}"
DEPLOY_USER="${ASSISTANT_DEPLOY_USER:-${SUDO_USER:-}}"
UNIT_DIRECTORY=/etc/systemd/system

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run this script with sudo." >&2
    exit 1
fi
if [[ -z "${DEPLOY_USER}" || "${DEPLOY_USER}" == root ]]; then
    echo "Set ASSISTANT_DEPLOY_USER to the non-root user that owns ${REPOSITORY_ROOT}." >&2
    exit 1
fi
if ! id -nG "${DEPLOY_USER}" | tr ' ' '\n' | grep -qx docker; then
    echo "${DEPLOY_USER} must be in the docker group to run Docker Compose." >&2
    exit 1
fi
if [[ "$(stat -c %U "${REPOSITORY_ROOT}")" != "${DEPLOY_USER}" ]]; then
    echo "${REPOSITORY_ROOT} must be owned by ${DEPLOY_USER}." >&2
    exit 1
fi
for executable in flock git python3 docker; do
    if ! command -v "${executable}" >/dev/null; then
        echo "Missing required executable: ${executable}" >&2
        exit 1
    fi
done

# The API container (root) writes requests/; only the deployer writes everything else.
# Compose may already have created these as root-owned bind-mount sources.
install -d -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" -m 755 "${SPOOL}"
for directory in requests processing "done" status logs; do
    install -d -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" -m 755 "${SPOOL}/${directory}"
done

render() {
    sed \
        -e "s|@DEPLOY_USER@|${DEPLOY_USER}|g" \
        -e "s|@REPOSITORY_ROOT@|${REPOSITORY_ROOT}|g" \
        -e "s|@SPOOL@|${SPOOL}|g" \
        "${REPOSITORY_ROOT}/deploy/systemd/$1.in" > "${UNIT_DIRECTORY}/$1"
    chmod 644 "${UNIT_DIRECTORY}/$1"
}
render assistant-deployer.service
render assistant-deployer.path

systemctl daemon-reload
systemctl enable assistant-deployer.service
systemctl enable --now assistant-deployer.path
echo "Host deployer installed. Spool: ${SPOOL}"
systemctl --no-pager status assistant-deployer.path || true
