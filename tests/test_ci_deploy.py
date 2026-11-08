"""GitHub Actions deploys: the workflow's trigger, and the server script its key runs."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ci-deploy.sh"
SHA = "a" * 40
OTHER = "b" * 40


@pytest.fixture
def spool(tmp_path: Path) -> Path:
    for name in ("requests", "processing", "done", "status", "logs"):
        (tmp_path / "spool" / name).mkdir(parents=True)
    return tmp_path / "spool"


def _run(
    spool: Path,
    *args: str,
    coding_runs: Path | None = None,
    main: str = SHA,
    ssh=None,
    coding_wait: int = 1,
):
    environment = {
        **os.environ,
        "ASSISTANT_HOST_DEPLOY_SPOOL": str(spool),
        "ASSISTANT_CI_DEPLOY_MAIN_SHA": main,
        "ASSISTANT_CI_DEPLOY_POLL_SECONDS": "0.05",
        # Whole seconds: bash measures the wait with $SECONDS.
        "ASSISTANT_CI_DEPLOY_CODING_WAIT_SECONDS": str(coding_wait),
        "ASSISTANT_CI_DEPLOY_TIMEOUT_SECONDS": "5",
    }
    environment.pop("SSH_ORIGINAL_COMMAND", None)
    if ssh is not None:
        environment["SSH_ORIGINAL_COMMAND"] = ssh
    if coding_runs is not None:
        environment["ASSISTANT_CI_DEPLOY_CODING_RUNS_FILE"] = str(coding_runs)
    else:
        no_runs = spool.parent / "no-runs"
        no_runs.write_text("0\n")
        environment["ASSISTANT_CI_DEPLOY_CODING_RUNS_FILE"] = str(no_runs)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _fake_deployer(spool: Path, outcome: str, seen: list[dict]) -> threading.Thread:
    """Answers the first request the way scripts/host-deployer.sh does."""

    def serve() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            requests = list((spool / "requests").glob("*.json"))
            if requests:
                document = json.loads(requests[0].read_text())
                seen.append(document)
                run_id = document["workflow_run_id"]
                requests[0].rename(spool / "done" / requests[0].name)
                (spool / "logs" / f"{run_id}.log").write_text("Building...\nHealth ok\n")
                (spool / "status" / f"{run_id}.json").write_text(
                    json.dumps({"workflow_run_id": run_id, "state": outcome})
                )
                return
            time.sleep(0.02)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return thread


@pytest.mark.parametrize(
    "command",
    [
        "",
        "deploy",
        f"deploy {SHA[:39]}",
        f"deploy {SHA.upper()}",
        f"deploy {SHA} now",
        f"deploy {SHA} force extra",
        f"bash -c id {SHA}",
        f"deploy {SHA};id",
    ],
)
def test_anything_but_deploy_and_a_full_sha_is_refused(spool: Path, command: str) -> None:
    result = _run(spool, ssh=command)

    assert result.returncode == 2
    assert "Usage: deploy" in result.stderr
    assert not list((spool / "requests").iterdir())


@pytest.mark.parametrize(("outcome", "code"), [("succeeded", 0), ("rolled_back", 3), ("failed", 1)])
def test_it_queues_the_deploy_and_reports_how_it_ended(spool: Path, outcome, code) -> None:
    seen: list[dict] = []
    deployer = _fake_deployer(spool, outcome, seen)

    result = _run(spool, ssh=f"deploy {SHA}")
    deployer.join()

    assert result.returncode == code, result.stderr
    (request,) = seen
    assert request["commit_sha"] == SHA
    assert request["deployment_target_id"] == "personal-assistant-production"
    assert request["requested_by"] == "github-actions"
    assert len(request["workflow_run_id"]) == 36
    assert "Building...\nHealth ok\n" in result.stdout  # the deploy log is streamed


def test_the_ssh_command_wins_over_arguments(spool: Path) -> None:
    result = _run(spool, "deploy", SHA, ssh="whoami")

    assert result.returncode == 2


def test_a_commit_main_has_moved_past_is_skipped(spool: Path) -> None:
    result = _run(spool, ssh=f"deploy {SHA}", main=OTHER)

    assert result.returncode == 0
    assert "moved past" in result.stdout
    assert not list((spool / "requests").iterdir())


def test_it_waits_for_an_executing_coding_run(spool: Path, tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.write_text("1\n")
    seen: list[dict] = []
    deployer = _fake_deployer(spool, "succeeded", seen)
    threading.Timer(0.3, lambda: runs.write_text("0\n")).start()

    result = _run(spool, ssh=f"deploy {SHA}", coding_runs=runs, coding_wait=10)
    deployer.join()

    assert result.returncode == 0, result.stderr
    assert "waiting for it to finish" in result.stdout
    assert len(seen) == 1


def test_it_gives_up_when_the_coding_run_outlasts_the_wait(spool: Path, tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.write_text("2\n")

    result = _run(spool, ssh=f"deploy {SHA}", coding_runs=runs)

    assert result.returncode == 1
    assert "still executing" in result.stderr
    assert not list((spool / "requests").iterdir())


def test_force_does_not_wait_for_coding_runs(spool: Path, tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.write_text("1\n")
    seen: list[dict] = []
    deployer = _fake_deployer(spool, "succeeded", seen)

    result = _run(spool, ssh=f"deploy {SHA} force", coding_runs=runs)
    deployer.join()

    assert result.returncode == 0, result.stderr
    assert "waiting" not in result.stdout


def test_an_unreadable_database_does_not_block_a_deploy(spool: Path, tmp_path: Path) -> None:
    seen: list[dict] = []
    deployer = _fake_deployer(spool, "succeeded", seen)

    result = _run(spool, ssh=f"deploy {SHA}", coding_runs=tmp_path / "missing")
    deployer.join()

    assert result.returncode == 0, result.stderr
    assert "deploying anyway" in result.stdout


def test_no_outcome_in_time_fails(spool: Path) -> None:
    result = _run(spool, ssh=f"deploy {SHA}")  # nothing answers the request

    assert result.returncode == 1
    assert "may still be running" in result.stderr


def _workflow() -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / "deploy.yml").read_text())


def test_the_workflow_deploys_main_after_ci_passes() -> None:
    workflow = _workflow()
    triggers = workflow[True]  # YAML reads the key "on" as True
    job = workflow["jobs"]["deploy"]

    assert triggers["workflow_run"] == {
        "workflows": ["CI"],
        "types": ["completed"],
        "branches": ["main"],
    }
    assert "conclusion == 'success'" in job["if"]
    assert "event == 'push'" in job["if"]  # not a pull request's CI run
    assert job["environment"] == "production"
    assert workflow["concurrency"]["cancel-in-progress"] is False
    ssh_step = next(step for step in job["steps"] if "ssh -i" in step.get("run", ""))
    assert '"deploy ${SHA} ${FORCE}"' in ssh_step["run"]


def test_the_consoles_fallback_inputs_still_exist() -> None:
    inputs = _workflow()[True]["workflow_dispatch"]["inputs"]

    assert {"commit_sha", "workflow_run_id"} <= set(inputs)
    assert not any(value.get("required") for value in inputs.values())
