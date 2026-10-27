from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def compose(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text())


def test_coding_worker_is_on_only_database_and_dedicated_egress_networks() -> None:
    for filename in ("compose.yaml", "compose.remote.yaml"):
        config = compose(filename)
        services = config["services"]
        networks = config["networks"]

        assert networks["private"]["internal"] is True
        assert networks["database"]["internal"] is True
        assert not (networks["egress"] or {}).get("internal", False)
        assert not (networks["coding-egress"] or {}).get("internal", False)
        assert services["postgres"]["networks"] == ["database"]
        assert services["migrate"]["networks"] == ["database"]
        assert services["coding-worker"]["networks"] == ["database", "coding-egress"]
        for name in ("api", "worker"):
            assert set(services[name]["networks"]) == {"private", "database", "egress"}
            assert "coding-egress" not in services[name]["networks"]
        assert set(services["mcp-tools"]["networks"]) == {"private", "egress"}
        if "caddy" in services:
            assert set(services["caddy"]["networks"]) == {"private", "egress"}


def test_coding_database_identity_and_secret_are_separate() -> None:
    local = compose("compose.yaml")["services"]
    remote = compose("compose.remote.yaml")["services"]

    for services in (local, remote):
        database_url = services["coding-worker"]["environment"]["ASSISTANT_DATABASE_URL"]
        assert "assistant_coding:" in database_url
        for name in ("api", "worker"):
            owner_url = services[name]["environment"]["ASSISTANT_DATABASE_URL"]
            assert "assistant_coding" not in owner_url

    assert (
        "${ASSISTANT_CODING_POSTGRES_PASSWORD:-assistant-coding}"
        in (local["coding-worker"]["environment"]["ASSISTANT_DATABASE_URL"])
    )
    required_secret = (
        "${ASSISTANT_CODING_POSTGRES_PASSWORD:?set ASSISTANT_CODING_POSTGRES_PASSWORD}"
    )
    assert required_secret in remote["coding-worker"]["environment"]["ASSISTANT_DATABASE_URL"]
    assert (
        "ASSISTANT_CODING_POSTGRES_PASSWORD=assistant-coding" in (ROOT / ".env.example").read_text()
    )
    assert (
        "ASSISTANT_CODING_POSTGRES_PASSWORD=replace_with_a_third_random_hex_value"
        in (ROOT / ".env.remote.example").read_text()
    )


def test_migration_runner_is_transactional_and_password_is_a_psql_variable() -> None:
    runner = (ROOT / "scripts/migrate.sh").read_text()
    password_sql = (ROOT / "deploy/postgres/coding-worker-password.sql").read_text()

    assert runner.count("-v ON_ERROR_STOP=1") == 2
    assert runner.count("--single-transaction") == 2
    assert runner.count("pg_advisory_xact_lock") == 2
    assert '-v coding_password="${ASSISTANT_CODING_POSTGRES_PASSWORD}"' in runner
    assert "ALTER ROLE assistant_coding LOGIN" in password_sql
    assert ":'coding_password'" in password_sql
    assert "ASSISTANT_CODING_POSTGRES_PASSWORD" not in password_sql


def test_coding_role_grants_and_security_definer_boundary() -> None:
    migration = (ROOT / "migrations/035_coding_worker_role.sql").read_text()

    assert "CREATE ROLE assistant_coding NOLOGIN" in migration
    assert "ALTER ROLE assistant_coding NOLOGIN" in migration
    assert "GRANT UPDATE (status, claimed_by, claim_id, lease_expires_at" in migration
    for capability in ("coding", "pull_request_creation", "coding-pull-request"):
        assert f"'{capability}'" in migration
    assert "source_context ? 'repository_id'" in migration
    rls_tables = (
        "tasks",
        "task_events",
        "executions",
        "coding_runs",
        "workflow_runs",
        "workflow_run_events",
    )
    for table in rls_tables:
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in migration
    denied_tables = (
        "notes",
        "memories",
        "chat_messages",
        "chat_sessions",
        "schedules",
        "work_items",
        "digests",
        "purchases",
        "passkeys",
    )
    for table in denied_tables:
        assert f"GRANT SELECT ON {table}" not in migration
        assert f"GRANT USAGE ON {table}" not in migration
    assert "CREATE OR REPLACE FUNCTION public.post_coding_assistant_reply" in migration
    assert "p_message_id VARCHAR(36)" in migration
    assert "p_blocks JSONB" in migration
    assert "p_emotion VARCHAR(16)" in migration
    assert "SECURITY DEFINER" in migration
    assert "SET search_path = ''" in migration
    assert "FROM PUBLIC" in migration
    assert "GRANT EXECUTE ON FUNCTION public.post_coding_assistant_reply" in migration
