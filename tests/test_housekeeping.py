from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "housekeeping.sh"


def _bash(command: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f"set -Eeuo pipefail; source '{SCRIPT}'; {command}"],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        check=False,
    )


def _dumps(directory: Path, *stamps: str) -> None:
    for stamp in stamps:
        (directory / f"assistant-{stamp}Z.dump").write_text("dump")


def test_backups_keep_the_newest_and_the_last_one_of_each_recent_day(tmp_path: Path) -> None:
    _dumps(
        tmp_path,
        # A day with many deploys...
        "20261024T210000",
        "20261024T220000",
        "20261024T230000",
        "20261024T100000",
        "20261024T110000",
        "20261024T120000",
        "20261024T130000",
        # ...and the days before it.
        "20261023T090000",
        "20261023T180000",
        "20261020T120000",
        "20261010T080000",
        "20261010T090000",
        "20261010T100000",
        # Older than the cutoff: only the newest-N rule could keep these.
        "20260920T120000",
        "20260920T130000",
    )
    (tmp_path / "notes.txt").write_text("not a dump")

    result = _bash(f"prune_backups '{tmp_path}' 3 20260925")

    assert result.returncode == 0, result.stderr
    kept = sorted(path.name for path in tmp_path.iterdir())
    assert kept == [
        "assistant-20261010T100000Z.dump",  # the last dump of each recent day
        "assistant-20261020T120000Z.dump",
        "assistant-20261023T180000Z.dump",
        "assistant-20261024T210000Z.dump",  # the 3 newest
        "assistant-20261024T220000Z.dump",
        "assistant-20261024T230000Z.dump",
        "notes.txt",  # anything else is left alone
    ]


def test_an_empty_or_missing_backup_directory_is_fine(tmp_path: Path) -> None:
    assert _bash(f"prune_backups '{tmp_path}' 20 20260925").returncode == 0
    assert _bash(f"prune_backups '{tmp_path / 'missing'}' 20 20260925").returncode == 0


def _stub_tools(
    directory: Path, *, disk_percent: int, docker_fails: bool = False
) -> dict[str, str]:
    """Fake `df` and `docker` first on the PATH; docker records how it was called."""
    log = directory / "docker.log"
    (directory / "df").write_text(f'#!/bin/sh\nprintf "Use%%\\n %s%%\\n" {disk_percent}\n')
    (directory / "docker").write_text(
        f'#!/bin/sh\necho "$@" >> "{log}"\n' + ("exit 1\n" if docker_fails else "echo pruned\n")
    )
    for name in ("df", "docker"):
        (directory / name).chmod(0o755)
    return {"PATH": f"{directory}:{os.environ['PATH']}"}


def test_the_build_cache_is_trimmed_gently_and_harder_when_the_disk_is_full(
    tmp_path: Path,
) -> None:
    for percent, expected in ((40, "until=72h"), (91, "until=6h")):
        directory = tmp_path / str(percent)
        directory.mkdir()
        env = _stub_tools(directory, disk_percent=percent)

        result = _bash("prune_build_cache", env=env)

        assert result.returncode == 0, result.stderr
        assert (directory / "docker.log").read_text().strip() == (
            f"builder prune --force --filter {expected}"
        )


def test_a_failing_cache_prune_never_fails_the_deploy(tmp_path: Path) -> None:
    env = _stub_tools(tmp_path, disk_percent=50, docker_fails=True)

    assert _bash("prune_build_cache", env=env).returncode == 0


def test_deploys_keep_every_chat_provider_the_app_supports() -> None:
    """A deploy once rewrote ASSISTANT_CONVERSATION_MODEL_PROVIDER=deepseek to minimax:
    its scripts only knew kimi and minimax."""
    import re
    from pathlib import Path

    from app.integrations.factory import CHAT_PROVIDERS

    for script in ("scripts/redeploy-remote.sh", "scripts/host-deployer.sh"):
        text = Path(script).read_text(encoding="utf-8")
        for allowed in re.findall(r"^\s*(?:\"\"\|)?([a-z|]+)\) ;;$", text, re.MULTILINE):
            assert set(allowed.split("|")) == set(CHAT_PROVIDERS), (script, allowed)
    redeploy = Path("scripts/redeploy-remote.sh").read_text(encoding="utf-8")
    assert '"${FALLBACK:-auto}"' in redeploy  # a chosen fallback is kept
