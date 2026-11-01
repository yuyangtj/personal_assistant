"""The server's configuration files agree with each other.

These catch the mistakes that pass every other test and only show on a deploy: a setting
that never reaches a container, or an image that copies a folder that no longer exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = (ROOT / "compose.remote.yaml").read_text(encoding="utf-8")

#: Keys in .env.remote.example that no service receives today, so setting them on the
#: server does nothing. Listed so no new ones appear; remove a key here once it is either
#: passed through in compose.remote.yaml or dropped from the example.
NOT_PASSED_THROUGH = {
    "ASSISTANT_GITHUB_REPOSITORY",
    "ASSISTANT_GITHUB_BASE_BRANCH",
    "ASSISTANT_GITHUB_REMOTE",
    "ASSISTANT_GITHUB_DRAFT_PULL_REQUESTS",
    "ASSISTANT_KIMI_CODE_EXECUTABLE",
    "ASSISTANT_CLAUDE_CODE_EXECUTABLE",
    "ASSISTANT_MINIMAX_CODE_MODEL",
    "ASSISTANT_CODE_AGENT_RATE_LIMIT_COOLDOWN_SECONDS",
    "ASSISTANT_CODE_AGENT_QUOTA_COOLDOWN_SECONDS",
    "ASSISTANT_CODE_REPOSITORY_PATH",
}


def _remote_example_keys() -> list[str]:
    text = (ROOT / ".env.remote.example").read_text(encoding="utf-8")
    return re.findall(r"^([A-Z][A-Z0-9_]*)=", text, re.MULTILINE)


def _reaches_a_service(key: str) -> bool:
    """Interpolated somewhere (``${KEY``), or set to a fixed value for a service."""
    return bool(
        re.search(rf"\$\{{{key}[:}}?-]", COMPOSE)
        or re.search(rf"^\s+{key}:", COMPOSE, re.MULTILINE)
    )


def test_every_server_setting_reaches_a_service() -> None:
    missing = [
        key
        for key in _remote_example_keys()
        if key not in NOT_PASSED_THROUGH and not _reaches_a_service(key)
    ]

    assert not missing, (
        f"{missing} are in .env.remote.example but no service in compose.remote.yaml "
        "receives them; add each to the environment of the service that reads it"
    )


def test_the_known_exceptions_are_still_exceptions() -> None:
    keys = set(_remote_example_keys())
    fixed = sorted(key for key in NOT_PASSED_THROUGH if key in keys and _reaches_a_service(key))
    gone = sorted(NOT_PASSED_THROUGH - keys)

    assert not fixed, f"{fixed} now reach a service; remove them from NOT_PASSED_THROUGH"
    assert not gone, f"{gone} left .env.remote.example; remove them from NOT_PASSED_THROUGH"


@pytest.mark.parametrize("dockerfile", ["Dockerfile", "Dockerfile.coding-worker"])
def test_every_copied_path_exists(dockerfile: str) -> None:
    missing = []
    for line in (ROOT / dockerfile).read_text(encoding="utf-8").splitlines():
        words = line.split()
        if not words or words[0] != "COPY" or any(w.startswith("--from") for w in words):
            continue
        sources = [word for word in words[1:-1] if not word.startswith("--")]
        missing += [source for source in sources if not (ROOT / source).exists()]

    assert not missing, f"{dockerfile} copies {missing}, which don't exist"
