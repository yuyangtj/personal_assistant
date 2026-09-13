from __future__ import annotations

from pathlib import Path

import pytest

from app.capabilities import (
    CapabilityManifest,
    CapabilityNotFoundError,
    CapabilityRegistry,
    CapabilityRegistryError,
)


def manifest(
    capability_id: str,
    *,
    priority: int = 0,
    enabled: bool = True,
    provides: tuple[str, ...] = ("task_execution",),
) -> CapabilityManifest:
    return CapabilityManifest.model_validate(
        {
            "id": capability_id,
            "name": capability_id,
            "kind": "agent",
            "description": f"Test capability {capability_id}",
            "provides": list(provides),
            "availability": {"enabled": enabled, "concurrency": 1},
            "execution": {"adapter": capability_id, "timeout_seconds": 30},
            "routing": {"priority": priority},
        }
    )


def test_registry_selects_highest_priority_enabled_match() -> None:
    registry = CapabilityRegistry(
        [
            manifest("slow", priority=1),
            manifest("preferred", priority=20),
            manifest("disabled", priority=100, enabled=False),
        ]
    )

    assert registry.select(["task_execution"]).id == "preferred"


def test_registry_requires_all_capabilities() -> None:
    registry = CapabilityRegistry([manifest("coding", provides=("coding", "testing"))])

    assert registry.select(["coding", "testing"]).id == "coding"
    with pytest.raises(CapabilityNotFoundError):
        registry.select(["coding", "shell_execution"])


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(CapabilityRegistryError, match="Duplicate capability id"):
        CapabilityRegistry([manifest("same"), manifest("same")])


def test_registry_loads_yaml_manifests(tmp_path: Path) -> None:
    (tmp_path / "example.yaml").write_text(
        """
id: example
name: Example
kind: tool
description: Example test tool
provides: [lookup]
execution:
  adapter: example
""".strip(),
        encoding="utf-8",
    )

    registry = CapabilityRegistry.from_directory(tmp_path)

    assert registry.get("example").provides == ("lookup",)


def test_invalid_manifest_reports_its_path(tmp_path: Path) -> None:
    invalid_path = tmp_path / "invalid.yaml"
    invalid_path.write_text("id: INVALID", encoding="utf-8")

    with pytest.raises(CapabilityRegistryError, match="invalid.yaml"):
        CapabilityRegistry.from_directory(tmp_path)
