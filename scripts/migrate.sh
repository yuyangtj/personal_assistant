#!/bin/sh
set -eu

: "${ASSISTANT_CODING_POSTGRES_PASSWORD:?set ASSISTANT_CODING_POSTGRES_PASSWORD}"
: "${PGHOST:=postgres}"
: "${PGUSER:=assistant}"
: "${PGDATABASE:=assistant}"
: "${MIGRATIONS_DIRECTORY:=/migrations}"
: "${CODING_PASSWORD_SQL:=/database/coding-worker-password.sql}"

for migration in "${MIGRATIONS_DIRECTORY}"/*.sql; do
    psql -h "${PGHOST}" -U "${PGUSER}" -d "${PGDATABASE}" \
        -v ON_ERROR_STOP=1 \
        --single-transaction \
        -c "SELECT pg_advisory_xact_lock(824615901)" \
        -f "${migration}"
done

psql -h "${PGHOST}" -U "${PGUSER}" -d "${PGDATABASE}" \
    -v ON_ERROR_STOP=1 \
    --single-transaction \
    -c "SELECT pg_advisory_xact_lock(824615901)" \
    -v coding_password="${ASSISTANT_CODING_POSTGRES_PASSWORD}" \
    -f "${CODING_PASSWORD_SQL}"
