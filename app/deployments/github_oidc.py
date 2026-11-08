"""Proves a deploy request comes from this repository's deploy workflow on GitHub Actions.

A workflow run with ``permissions: id-token: write`` can ask GitHub for a short-lived
OpenID Connect token, signed by GitHub, that names the repository, branch, workflow file
and environment the run belongs to. Checking those claims proves where a request came
from without any secret stored in GitHub or on the server.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import jwt

logger = logging.getLogger(__name__)

ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = f"{ISSUER}/.well-known/jwks"
DEPLOY_WORKFLOW = ".github/workflows/deploy.yml"
#: After CI passes on a merge, or "Run workflow" in the Actions tab.
ALLOWED_EVENTS = frozenset({"workflow_run", "workflow_dispatch"})
REQUIRED_CLAIMS = [
    "exp",
    "iat",
    "iss",
    "aud",
    "repository",
    "ref",
    "environment",
    "job_workflow_ref",
    "event_name",
    "run_id",
]


class GitHubTokenError(ValueError):
    """The token isn't a valid one from the deploy workflow; the message says why."""


@dataclass(frozen=True, slots=True)
class GitHubDeployClaims:
    repository: str
    run_id: str
    event_name: str
    actor: str


class GitHubDeployVerifier:
    """Accepts only tokens from ``deploy.yml`` on the base branch, in its environment."""

    def __init__(
        self,
        *,
        audience: str,
        repository: str,
        branch: str = "main",
        environment: str = "production",
        jwks_client: Any | None = None,
        leeway_seconds: int = 60,
    ):
        if not audience or not repository:
            raise ValueError("An audience and a repository are required")
        self.audience = audience
        self.repository = repository
        self.ref = f"refs/heads/{branch}"
        self.environment = environment
        self.leeway_seconds = leeway_seconds
        # GitHub's signing keys, fetched once and cached for an hour.
        self._jwks = jwks_client or jwt.PyJWKClient(
            JWKS_URL, cache_keys=True, lifespan=3600, timeout=10
        )

    def verify(self, token: str) -> GitHubDeployClaims:
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=ISSUER,
                leeway=self.leeway_seconds,
                options={"require": REQUIRED_CLAIMS},
            )
        except jwt.PyJWTError as error:
            raise GitHubTokenError(f"not a valid GitHub token ({type(error).__name__})") from error
        expected = {
            "repository": self.repository,
            "ref": self.ref,
            "environment": self.environment,
            "job_workflow_ref": f"{self.repository}/{DEPLOY_WORKFLOW}@{self.ref}",
        }
        for name, value in expected.items():
            if claims.get(name) != value:
                raise GitHubTokenError(f"the token's {name} is not {value}")
        if claims.get("event_name") not in ALLOWED_EVENTS:
            raise GitHubTokenError(f"a {claims.get('event_name')} run can't deploy")
        return GitHubDeployClaims(
            repository=str(claims["repository"]),
            run_id=str(claims["run_id"]),
            event_name=str(claims["event_name"]),
            actor=str(claims.get("actor") or ""),
        )
