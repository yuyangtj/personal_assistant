from __future__ import annotations

import pytest

from app.repositories import RepositoryNotFoundError, RepositoryRegistry


def test_repository_registry_loads_both_trusted_repositories() -> None:
    registry = RepositoryRegistry.from_directory("repositories")

    assert [repository.id for repository in registry.list()] == [
        "analytics-agent-playground",
        "personal-assistant",
    ]
    assert registry.get("analytics").id == "analytics-agent-playground"
    assert registry.default().id == "personal-assistant"  # type: ignore[union-attr]


def test_repository_registry_rejects_unknown_repository() -> None:
    registry = RepositoryRegistry.from_directory("repositories")

    with pytest.raises(RepositoryNotFoundError, match="Unknown repository"):
        registry.get("untrusted-repository")
