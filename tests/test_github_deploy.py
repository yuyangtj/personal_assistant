"""Deploys from GitHub Actions over HTTPS: the token check, the endpoints and the workflow."""

from __future__ import annotations

import json
import time
from pathlib import Path

import jwt
import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import Settings
from app.deployments import GitHubDeployVerifier, GitHubTokenError, HostDeployerSpool
from app.deployments.github_oidc import ISSUER
from app.main import github_deploy_verifier

ROOT = Path(__file__).resolve().parent.parent
AUDIENCE = "https://assistant.example"
REPOSITORY = "yuyangtj/personal_assistant"
SHA = "a" * 40
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeKeys:
    """Stands in for PyJWKClient: GitHub's public signing key."""

    class _Key:
        key = KEY.public_key()

    def get_signing_key_from_jwt(self, token: str):
        return self._Key()


def _claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "nbf": now,
        "exp": now + 300,
        "repository": REPOSITORY,
        "ref": "refs/heads/main",
        "environment": "production",
        "job_workflow_ref": f"{REPOSITORY}/.github/workflows/deploy.yml@refs/heads/main",
        "event_name": "workflow_run",
        "run_id": "123",
        "actor": "yuyangtj",
    }
    claims.update(overrides)
    return {name: value for name, value in claims.items() if value is not None}


def _token(key=KEY, algorithm="RS256", **overrides) -> str:
    return jwt.encode(_claims(**overrides), key, algorithm=algorithm)


def _verifier() -> GitHubDeployVerifier:
    return GitHubDeployVerifier(audience=AUDIENCE, repository=REPOSITORY, jwks_client=FakeKeys())


@pytest.mark.parametrize("event", ["workflow_run", "workflow_dispatch"])
def test_a_token_from_the_deploy_workflow_on_main_is_accepted(event: str) -> None:
    claims = _verifier().verify(_token(event_name=event))

    assert (claims.repository, claims.run_id, claims.event_name) == (REPOSITORY, "123", event)


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "https://someone-else.example"},
        {"iss": "https://evil.example"},
        {"repository": "someone/fork"},
        {"ref": "refs/heads/feature"},
        {"environment": "staging"},
        {"job_workflow_ref": f"{REPOSITORY}/.github/workflows/ci.yml@refs/heads/main"},
        {"job_workflow_ref": f"{REPOSITORY}/.github/workflows/deploy.yml@refs/heads/x"},
        {"event_name": "pull_request"},
        {"event_name": "pull_request_target"},
        {"exp": int(time.time()) - 600},
        {"run_id": None},
        {"environment": None},
    ],
)
def test_a_token_from_anywhere_else_is_refused(overrides: dict) -> None:
    with pytest.raises(GitHubTokenError):
        _verifier().verify(_token(**overrides))


def test_a_token_signed_by_another_key_is_refused() -> None:
    with pytest.raises(GitHubTokenError):
        _verifier().verify(_token(key=OTHER_KEY))


def test_an_unsigned_or_shared_secret_token_is_refused() -> None:
    unsigned = jwt.encode(_claims(), None, algorithm="none")
    shared = jwt.encode(_claims(), "secret", algorithm="HS256")

    for token in (unsigned, shared, "not-a-token"):
        with pytest.raises(GitHubTokenError):
            _verifier().verify(token)


def test_the_verifier_trusts_the_production_target_repository() -> None:
    from app.deployments import DeploymentRegistry
    from app.repositories import RepositoryRegistry

    verifier = github_deploy_verifier(
        Settings(github_deploy_audience=AUDIENCE),
        DeploymentRegistry.from_directory(ROOT / "deployment-targets"),
        RepositoryRegistry.from_directory(ROOT / "repositories"),
    )

    assert verifier.repository == REPOSITORY
    assert verifier.ref == "refs/heads/main"
    assert verifier.environment == "production"
    assert github_deploy_verifier(Settings(), None, None) is None


# The endpoints.


class FakeGitHub:
    def __init__(self, head: str = SHA):
        self.head = head

    def get_branch_head(self, **_kwargs) -> str:
        return self.head

    def dispatch_workflow(self, **_kwargs) -> None:
        raise AssertionError("the host deployer route never dispatches a workflow")


@pytest.fixture
def site(client, tmp_path: Path):
    spool = HostDeployerSpool(tmp_path)
    spool.requests_directory.mkdir()
    spool.status_directory.mkdir()
    state = client.app.state
    state.workflow_service.host_deployer = spool
    state.github_client = FakeGitHub()
    state.github_deploy_verifier = _verifier()
    return client, spool


def _auth(token: str | None = None) -> dict:
    return {"Authorization": f"Bearer {token or _token()}"}


def test_the_endpoint_is_off_without_an_audience(client) -> None:
    assert client.app.state.github_deploy_verifier is None
    assert client.post("/deploy/github", json={"commit_sha": SHA}).status_code == 404


def test_a_request_without_a_valid_token_deploys_nothing(site) -> None:
    client, spool = site

    missing = client.post("/deploy/github", json={"commit_sha": SHA})
    forged = client.post(
        "/deploy/github", json={"commit_sha": SHA}, headers=_auth(_token(key=OTHER_KEY))
    )
    from_a_branch = client.post(
        "/deploy/github", json={"commit_sha": SHA}, headers=_auth(_token(ref="refs/heads/x"))
    )

    assert [r.status_code for r in (missing, forged, from_a_branch)] == [401, 401, 401]
    assert list(spool.requests_directory.iterdir()) == []


def test_a_merge_is_approved_and_handed_to_the_host_deployer(site) -> None:
    client, spool = site

    reply = client.post("/deploy/github", json={"commit_sha": SHA}, headers=_auth())

    assert reply.status_code == 202
    run_id = reply.json()["workflow_run_id"]
    (request,) = spool.requests_directory.iterdir()
    assert json.loads(request.read_text())["commit_sha"] == SHA
    run = client.app.state.workflow_service.get_run(run_id)
    assert run.status == "running"
    events = client.app.state.workflow_service.list_events(run_id)
    approved = next(e for e in events if e.event_type == "WORKFLOW_APPROVED")
    assert "github-actions run 123" in approved.payload["by"]


def test_a_commit_main_has_moved_past_is_skipped(site) -> None:
    client, spool = site
    client.app.state.github_client = FakeGitHub(head="b" * 40)

    reply = client.post("/deploy/github", json={"commit_sha": SHA}, headers=_auth())

    assert reply.status_code == 200
    assert reply.json()["outcome"] == "skipped"
    assert list(spool.requests_directory.iterdir()) == []


def test_a_coding_run_asks_the_workflow_to_retry_unless_forced(site, monkeypatch) -> None:
    client, spool = site
    service = client.app.state.workflow_service
    monkeypatch.setattr(service, "coding_run_in_progress", lambda: True)
    monkeypatch.setattr(type(service), "_coding_run_in_progress", staticmethod(lambda s: True))

    waiting = client.post("/deploy/github", json={"commit_sha": SHA}, headers=_auth())
    forced = client.post("/deploy/github", json={"commit_sha": SHA, "force": True}, headers=_auth())

    assert waiting.status_code == 409
    assert waiting.json()["retry_after_seconds"] == 30
    assert forced.status_code == 202
    assert len(list(spool.requests_directory.iterdir())) == 1


@pytest.mark.parametrize("body", [{"commit_sha": "abc"}, {"commit_sha": SHA, "extra": 1}])
def test_a_malformed_request_is_refused(site, body) -> None:
    client, _ = site

    assert client.post("/deploy/github", json=body, headers=_auth()).status_code == 422


@pytest.mark.parametrize(
    ("state", "status"),
    [("succeeded", "completed"), ("rolled_back", "failed"), ("failed", "failed")],
)
def test_the_status_reports_how_the_deploy_ended(site, state: str, status: str) -> None:
    client, spool = site
    run_id = client.post("/deploy/github", json={"commit_sha": SHA}, headers=_auth()).json()[
        "workflow_run_id"
    ]
    running = client.get(f"/deploy/github/{run_id}", headers=_auth())
    (spool.status_directory / f"{run_id}.json").write_text(
        json.dumps(
            {
                "workflow_run_id": run_id,
                "state": state,
                "stage": "promote",
                "commit_sha": SHA,
                "previous_sha": "b" * 40,
                "started_at": "2026-11-08T12:00:00+00:00",
                "finished_at": "2026-11-08T12:03:00+00:00",
                "log_tail": "Deployment complete",
            }
        )
    )

    finished = client.get(f"/deploy/github/{run_id}", headers=_auth())

    assert running.json()["status"] == "running"
    assert finished.json()["status"] == status
    assert finished.json()["deployer_state"] == state
    assert finished.json()["log_tail"] == "Deployment complete"


def test_the_status_needs_a_token_and_a_deployment(site) -> None:
    client, _ = site

    assert client.get("/deploy/github/nope").status_code == 401
    assert client.get("/deploy/github/nope", headers=_auth()).status_code == 404


# The workflow and the proxy.


def _workflow() -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / "deploy.yml").read_text())


def test_the_workflow_deploys_main_after_ci_passes_with_a_github_token() -> None:
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
    assert job["environment"] == "production"  # the token's environment claim
    assert workflow["permissions"]["id-token"] == "write"
    assert workflow["concurrency"]["cancel-in-progress"] is False
    script = "\n".join(step.get("run", "") for step in job["steps"])
    assert "/deploy/github" in script
    assert "secrets." not in json.dumps(workflow)  # no stored credential


def test_the_consoles_fallback_inputs_are_still_accepted() -> None:
    inputs = _workflow()[True]["workflow_dispatch"]["inputs"]

    assert {"commit_sha", "workflow_run_id"} <= set(inputs)
    assert not any(value.get("required") for value in inputs.values())


def test_caddy_lets_github_reach_only_the_deploy_paths() -> None:
    caddyfile = (ROOT / "deploy" / "Caddyfile").read_text()
    (private,) = [line for line in caddyfile.splitlines() if "@private not path" in line]

    assert {"/deploy/github", "/deploy/github/*"} <= set(private.split())
    assert "/deploy/*" not in private.split()
