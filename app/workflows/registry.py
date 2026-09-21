from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.workflows.models import WorkflowManifest


class WorkflowRegistryError(ValueError):
    pass


class WorkflowNotFoundError(LookupError):
    pass


class WorkflowRegistry:
    def __init__(self, manifests: Iterable[WorkflowManifest]):
        self._manifests: dict[str, WorkflowManifest] = {}
        for manifest in manifests:
            if manifest.id in self._manifests:
                raise WorkflowRegistryError(f"Duplicate workflow id: {manifest.id}")
            self._manifests[manifest.id] = manifest

    @classmethod
    def from_directory(cls, directory: str | Path) -> WorkflowRegistry:
        root = Path(directory)
        if not root.is_dir():
            raise WorkflowRegistryError(f"Workflow directory does not exist: {root}")
        manifests: list[WorkflowManifest] = []
        for manifest_path in sorted(root.glob("*.yaml")):
            try:
                raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
                manifests.append(WorkflowManifest.model_validate(raw))
            except (OSError, yaml.YAMLError, ValidationError) as error:
                raise WorkflowRegistryError(
                    f"Invalid workflow manifest {manifest_path}: {error}"
                ) from error
        if not manifests:
            raise WorkflowRegistryError("Workflow directory contains no YAML manifests")
        return cls(manifests)

    def list(self, *, include_disabled: bool = False) -> list[WorkflowManifest]:
        manifests = self._manifests.values()
        if not include_disabled:
            manifests = (manifest for manifest in manifests if manifest.enabled)
        return sorted(manifests, key=lambda manifest: manifest.id)

    def get(self, workflow_id: str) -> WorkflowManifest:
        manifest = self._manifests.get(workflow_id.strip().lower())
        if manifest is None or not manifest.enabled:
            raise WorkflowNotFoundError(f"Unknown workflow: {workflow_id}")
        return manifest
