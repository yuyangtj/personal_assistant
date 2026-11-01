from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "redeploy-remote.sh"


def _functions() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    return text[text.index("api_is_healthy() {") : text.index("prune_release_images() {")]


def _run(tmp_path: Path, command: str, overrides: dict[str, str] | None = None):
    log = tmp_path / "docker.log"
    docker = f"""#!/bin/sh
echo "$@" >> {shlex.quote(str(log))}
case "$*" in
  *"config --services"*)
    if [ "${{STUB_CODING:-0}}" = 1 ]; then echo coding-worker; fi
    ;;
  *"exec -T api"*) exit "${{STUB_API_FAIL:-0}}" ;;
  *"exec -T coding-worker"*) exit "${{STUB_CODING_FAIL:-0}}" ;;
  *"exec -T worker"*) exit "${{STUB_WORKER_FAIL:-0}}" ;;
  *"exec -T caddy cat"*)
    if [ "${{STUB_CADDY_CAT_FAIL:-0}}" = 1 ]; then exit 1; fi
    cat {shlex.quote(str(ROOT / "deploy" / "Caddyfile"))}
    ;;
  *"force-recreate caddy"*) exit "${{STUB_CADDY_RECREATE_FAIL:-0}}" ;;
  *"up -d"*)
    # Fail the first STUB_UP_FAILURES starts.
    count_file={shlex.quote(str(tmp_path / "up.count"))}
    count=$(cat "$count_file" 2>/dev/null || echo 0)
    echo $((count + 1)) > "$count_file"
    if [ "$count" -lt "${{STUB_UP_FAILURES:-0}}" ]; then exit 1; fi
    ;;
esac
"""
    (tmp_path / "docker").write_text(docker)
    (tmp_path / "curl").write_text('#!/bin/sh\nexit "${STUB_CURL_FAIL:-0}"\n')
    (tmp_path / "sleep").write_text("#!/bin/sh\nexit 0\n")
    for name in ("docker", "curl", "sleep"):
        (tmp_path / name).chmod(0o755)
    shell = (
        "set -Eeuo pipefail; "
        "compose=(docker compose); ASSISTANT_DOMAIN=assistant.example; "
        f"{_functions()}\n{command}"
    )
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        **(overrides or {}),
    }
    return subprocess.run(
        ["bash", "-c", shell], capture_output=True, text=True, env=env, check=False
    )


def test_full_health_requires_api_public_https_and_all_enabled_workers(tmp_path: Path) -> None:
    assert _run(tmp_path, "wait_for_health", {"STUB_CODING": "1"}).returncode == 0
    assert _run(tmp_path, "wait_for_health", {"STUB_WORKER_FAIL": "1"}).returncode == 1
    assert _run(tmp_path, "wait_for_health", {"STUB_CURL_FAIL": "1"}).returncode == 1
    assert (
        _run(
            tmp_path,
            "wait_for_health",
            {"STUB_CODING": "1", "STUB_CODING_FAIL": "1"},
        ).returncode
        == 1
    )


def test_legacy_rollback_health_does_not_require_new_worker_module(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "wait_for_health legacy",
        {"STUB_WORKER_FAIL": "1", "STUB_CODING": "1", "STUB_CODING_FAIL": "1"},
    )

    assert result.returncode == 0


def test_caddy_recreation_failure_is_visible_to_rollout_logic(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "refresh_caddy",
        {"STUB_CADDY_CAT_FAIL": "1", "STUB_CADDY_RECREATE_FAIL": "1"},
    )

    assert result.returncode == 1


def test_caddy_is_refreshed_before_release_tag_is_recorded() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    rollout = text[text.index("if ! start_release; then") :]

    assert rollout.index("refresh_caddy") < rollout.index('> "${CURRENT_TAG_FILE}"')
    assert "wait_for_health legacy" in text


def _compose_calls(tmp_path: Path) -> list[str]:
    log = (tmp_path / "docker.log").read_text().splitlines()
    return [line.removeprefix("compose ") for line in log]


def test_start_release_does_not_clear_containers_when_up_succeeds(tmp_path: Path) -> None:
    assert _run(tmp_path, "start_release").returncode == 0
    assert _compose_calls(tmp_path) == ["up -d --no-build --remove-orphans"]


def test_start_release_clears_old_containers_and_networks_then_retries(tmp_path: Path) -> None:
    result = _run(tmp_path, "start_release", {"STUB_UP_FAILURES": "1"})

    assert result.returncode == 0
    assert _compose_calls(tmp_path) == [
        "up -d --no-build --remove-orphans",
        "down --remove-orphans",
        "up -d --no-build --remove-orphans",
    ]


def test_start_release_failure_reaches_rollout_logic(tmp_path: Path) -> None:
    assert _run(tmp_path, "start_release", {"STUB_UP_FAILURES": "2"}).returncode == 1


def test_images_build_before_anything_is_stopped_and_failed_start_rolls_back() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    rollout = text[text.index('"${compose[@]}" build\n') :]

    assert rollout.index('"${compose[@]}" build') < rollout.index("if ! start_release; then")
    assert "down --volumes" not in text and " down -v" not in text
    rollback = rollout[rollout.index('if [[ "${healthy}" != true ]]; then') :]
    assert "if start_release; then" in rollback
