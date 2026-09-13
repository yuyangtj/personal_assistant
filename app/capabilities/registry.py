from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.capabilities.models import CapabilityKind, CapabilityManifest


class CapabilityRegistryError(ValueError):
    pass


class CapabilityNotFoundError(LookupError):
    def __init__(self, requirements: Iterable[str]):
        self.requirements = tuple(requirements)
        super().__init__(f"No enabled capability provides: {', '.join(self.requirements)}")


class CapabilityRegistry:
    def __init__(self, manifests: Iterable[CapabilityManifest]):
        self._manifests: dict[str, CapabilityManifest] = {}
        for manifest in manifests:
            if manifest.id in self._manifests:
                raise CapabilityRegistryError(f"Duplicate capability id: {manifest.id}")
            self._manifests[manifest.id] = manifest

    @classmethod
    def from_directory(cls, directory: str | Path) -> CapabilityRegistry:
        capability_directory = Path(directory)
        if not capability_directory.is_dir():
            raise CapabilityRegistryError(
                f"Capability directory does not exist: {capability_directory}"
            )

        manifests: list[CapabilityManifest] = []
        for path in sorted(capability_directory.glob("*.yaml")):
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise CapabilityRegistryError(f"Manifest must be a mapping: {path}")
                manifests.append(CapabilityManifest.model_validate(raw))
            except (OSError, yaml.YAMLError, ValidationError) as error:
                raise CapabilityRegistryError(
                    f"Invalid capability manifest {path}: {error}"
                ) from error

        if not manifests:
            raise CapabilityRegistryError(
                f"Capability directory contains no YAML manifests: {capability_directory}"
            )
        return cls(manifests)

    def get(self, capability_id: str) -> CapabilityManifest:
        try:
            return self._manifests[capability_id]
        except KeyError as error:
            raise CapabilityNotFoundError([capability_id]) from error

    def list(
        self,
        *,
        kind: CapabilityKind | None = None,
        include_disabled: bool = True,
    ) -> list[CapabilityManifest]:
        manifests = self._manifests.values()
        if kind is not None:
            manifests = (manifest for manifest in manifests if manifest.kind == kind)
        if not include_disabled:
            manifests = (manifest for manifest in manifests if manifest.availability.enabled)
        return sorted(manifests, key=lambda manifest: manifest.id)

    def candidates(self, requirements: Iterable[str]) -> list[CapabilityManifest]:
        required = set(requirements)
        if not required:
            raise CapabilityRegistryError("At least one required capability is needed")
        candidates = [
            manifest
            for manifest in self._manifests.values()
            if manifest.availability.enabled and manifest.provides_all(required)
        ]
        return sorted(
            candidates,
            key=lambda manifest: (-manifest.routing.priority, manifest.id),
        )

    def select(self, requirements: Iterable[str]) -> CapabilityManifest:
        normalized = tuple(dict.fromkeys(requirements))
        candidates = self.candidates(normalized)
        if not candidates:
            raise CapabilityNotFoundError(normalized)
        return candidates[0]
