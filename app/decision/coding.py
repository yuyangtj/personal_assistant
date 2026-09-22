from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.capabilities.models import IDENTIFIER_PATTERN
from app.providers import ProviderStateStore


class TaskComplexity(StrEnum):
    SIMPLE = "simple"
    STANDARD = "standard"
    COMPLEX = "complex"


class CodingTaskProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    complexity: TaskComplexity
    traits: tuple[str, ...]
    reasons: tuple[str, ...]


class CodingRunnerManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str = Field(min_length=1, max_length=120)
    priority: int = Field(ge=-100, le=100)
    cost_tier: int = Field(ge=1, le=5)
    reasoning_tier: int = Field(ge=1, le=5)
    strengths: tuple[str, ...] = ()
    enabled: bool = True

    @field_validator("id")
    @classmethod
    def id_is_identifier(cls, value: str) -> str:
        if not IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError("runner id must be a lowercase identifier")
        return value

    @field_validator("strengths")
    @classmethod
    def strengths_are_identifiers(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("runner strengths must be unique")
        if any(not IDENTIFIER_PATTERN.fullmatch(value) for value in values):
            raise ValueError("runner strengths must be lowercase identifiers")
        return values


class CodingRunnerRegistry:
    def __init__(self, manifests: Iterable[CodingRunnerManifest]):
        self._manifests: dict[str, CodingRunnerManifest] = {}
        for manifest in manifests:
            if manifest.id in self._manifests:
                raise ValueError(f"Duplicate coding runner id: {manifest.id}")
            self._manifests[manifest.id] = manifest

    @classmethod
    def from_directory(cls, directory: str | Path) -> CodingRunnerRegistry:
        root = Path(directory)
        if not root.is_dir():
            raise ValueError(f"Coding runner directory does not exist: {root}")
        manifests: list[CodingRunnerManifest] = []
        for path in sorted(root.glob("*.yaml")):
            try:
                manifests.append(
                    CodingRunnerManifest.model_validate(
                        yaml.safe_load(path.read_text(encoding="utf-8"))
                    )
                )
            except (OSError, yaml.YAMLError, ValidationError) as error:
                raise ValueError(f"Invalid coding runner manifest {path}: {error}") from error
        if not manifests:
            raise ValueError("Coding runner directory contains no YAML manifests")
        return cls(manifests)

    def get(self, runner_id: str) -> CodingRunnerManifest:
        manifest = self._manifests.get(runner_id)
        if manifest is None or not manifest.enabled:
            raise ValueError(f"Unknown or disabled coding runner: {runner_id}")
        return manifest

    def list(self) -> list[CodingRunnerManifest]:
        return sorted(
            (manifest for manifest in self._manifests.values() if manifest.enabled),
            key=lambda manifest: (-manifest.priority, manifest.id),
        )


class ProviderRunner(Protocol):
    provider: str


class CodingCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    score: int
    available: bool
    reasons: tuple[str, ...]


class CodingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: CodingTaskProfile
    candidates: tuple[CodingCandidate, ...]
    ordered_providers: tuple[str, ...]


_TRAIT_TERMS: dict[str, frozenset[str]] = {
    "architecture": frozenset({"architecture", "architect", "design", "system"}),
    "data": frozenset({"analytics", "data", "database", "query", "sql", "metric"}),
    "debugging": frozenset({"bug", "debug", "error", "fail", "fix", "issue"}),
    "documentation": frozenset({"docs", "documentation", "readme", "comment"}),
    "migration": frozenset({"migration", "schema", "upgrade"}),
    "refactor": frozenset({"refactor", "restructure", "rewrite"}),
    "security": frozenset({"auth", "permission", "secret", "security", "token"}),
    "testing": frozenset({"test", "tests", "coverage"}),
}
_COMPLEX_TERMS = frozenset(
    {"architecture", "concurrency", "deploy", "migration", "production", "security", "system"}
)
_SIMPLE_TERMS = frozenset({"comment", "docs", "documentation", "readme", "rename", "typo"})


def profile_coding_task(request: str) -> CodingTaskProfile:
    words = set(re.findall(r"[a-z0-9_]+", request.lower()))
    traits = tuple(trait for trait, terms in _TRAIT_TERMS.items() if words.intersection(terms))
    complex_matches = sorted(words.intersection(_COMPLEX_TERMS))
    simple_matches = sorted(words.intersection(_SIMPLE_TERMS))
    if complex_matches or len(request) > 800:
        complexity = TaskComplexity.COMPLEX
        reasons = tuple(f"complex:{term}" for term in complex_matches) or ("complex:long_request",)
    elif simple_matches and len(words) < 80:
        complexity = TaskComplexity.SIMPLE
        reasons = tuple(f"simple:{term}" for term in simple_matches)
    else:
        complexity = TaskComplexity.STANDARD
        reasons = ("standard:default",)
    return CodingTaskProfile(complexity=complexity, traits=traits, reasons=reasons)


class CodingDecisionEngine:
    def __init__(
        self,
        registry: CodingRunnerRegistry,
        state_store: ProviderStateStore,
    ):
        self.registry = registry
        self.state_store = state_store

    def decide(
        self,
        runners: Sequence[ProviderRunner],
        request: str,
    ) -> CodingDecision:
        profile = profile_coding_task(request)
        candidates: list[CodingCandidate] = []
        configured_order = {runner.provider: index for index, runner in enumerate(runners)}
        for runner in runners:
            manifest = self.registry.get(runner.provider)
            state = self.state_store.get(f"coding:{runner.provider}")
            available = self.state_store.is_available(f"coding:{runner.provider}")
            score = manifest.priority
            reasons = [f"base_priority:{manifest.priority}"]
            matched = sorted(set(profile.traits).intersection(manifest.strengths))
            if matched:
                score += len(matched) * 12
                reasons.append("strength_match:" + ",".join(matched))
            if profile.complexity == TaskComplexity.SIMPLE:
                adjustment = (6 - manifest.cost_tier) * 4
                reasons.append(f"simple_cost_fit:{adjustment}")
            elif profile.complexity == TaskComplexity.COMPLEX:
                adjustment = manifest.reasoning_tier * 8
                reasons.append(f"complex_reasoning_fit:{adjustment}")
            else:
                adjustment = manifest.reasoning_tier * 3 + (6 - manifest.cost_tier) * 2
                reasons.append(f"standard_fit:{adjustment}")
            score += adjustment
            if state.consecutive_failures:
                penalty = min(state.consecutive_failures * 5, 25)
                score -= penalty
                reasons.append(f"failure_penalty:-{penalty}")
            attempts = state.total_successes + state.total_failures
            if attempts >= 3:
                # Historical evidence is deliberately bounded so it cannot overpower
                # capability, cost, or live availability signals.
                historical = round(((state.total_successes / attempts) - 0.5) * 20)
                historical = max(-10, min(10, historical))
                score += historical
                reasons.append(
                    f"historical_success:{state.total_successes}/{attempts}:{historical:+d}"
                )
            if not available:
                reasons.append("unavailable:cooldown")
            candidates.append(
                CodingCandidate(
                    provider=runner.provider,
                    score=score,
                    available=available,
                    reasons=tuple(reasons),
                )
            )
        candidates.sort(
            key=lambda candidate: (
                not candidate.available,
                -candidate.score,
                configured_order[candidate.provider],
            )
        )
        return CodingDecision(
            profile=profile,
            candidates=tuple(candidates),
            ordered_providers=tuple(candidate.provider for candidate in candidates),
        )
