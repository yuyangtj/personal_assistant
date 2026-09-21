from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.repositories.models import RepositoryManifest


class RepositoryRegistryError(ValueError):
    pass


class RepositoryNotFoundError(LookupError):
    pass


class RepositoryRegistry:
    def __init__(self, manifests: Iterable[RepositoryManifest]):
        self._manifests: dict[str, RepositoryManifest] = {}
        aliases: dict[str, str] = {}
        defaults = 0
        for manifest in manifests:
            if manifest.id in self._manifests:
                raise RepositoryRegistryError(f"Duplicate repository id: {manifest.id}")
            self._manifests[manifest.id] = manifest
            defaults += int(manifest.default and manifest.enabled)
            for alias in (manifest.id, *manifest.aliases):
                if alias in aliases:
                    raise RepositoryRegistryError(f"Duplicate repository alias: {alias}")
                aliases[alias] = manifest.id
        if defaults > 1:
            raise RepositoryRegistryError("Only one enabled repository can be the default")
        self._aliases = aliases

    @classmethod
    def from_directory(cls, directory: str | Path) -> RepositoryRegistry:
        root = Path(directory)
        if not root.is_dir():
            raise RepositoryRegistryError(f"Repository directory does not exist: {root}")
        manifests: list[RepositoryManifest] = []
        for manifest_path in sorted(root.glob("*.yaml")):
            try:
                raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
                manifests.append(RepositoryManifest.model_validate(raw))
            except (OSError, yaml.YAMLError, ValidationError) as error:
                raise RepositoryRegistryError(
                    f"Invalid repository manifest {manifest_path}: {error}"
                ) from error
        if not manifests:
            raise RepositoryRegistryError("Repository directory contains no YAML manifests")
        return cls(manifests)

    def list(self, *, include_disabled: bool = False) -> list[RepositoryManifest]:
        manifests = self._manifests.values()
        if not include_disabled:
            manifests = (manifest for manifest in manifests if manifest.enabled)
        return sorted(manifests, key=lambda manifest: manifest.id)

    def get(self, repository_id: str) -> RepositoryManifest:
        normalized = repository_id.strip().lower()
        resolved = self._aliases.get(normalized, normalized)
        manifest = self._manifests.get(resolved)
        if manifest is None or not manifest.enabled:
            raise RepositoryNotFoundError(f"Unknown repository: {repository_id}")
        return manifest

    def default(self) -> RepositoryManifest | None:
        return next((manifest for manifest in self.list() if manifest.default), None)
