#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd -P)
cd "$REPO_ROOT"

MODE=${ANZMA_BACKUP_MODE:-compose}
COMPOSE_FILE=${ANZMA_COMPOSE_FILE:-deploy/compose/docker-compose.yml}
COMPOSE_PROJECT=${ANZMA_COMPOSE_PROJECT:-}
DB_SERVICE=${ANZMA_BACKUP_DB_SERVICE:-db}
BACKUP_DIR=${ANZMA_BACKUP_DIR:-${HOME:?HOME must be set}/anzma_backups}
SCRATCH_DB=${ANZMA_RESTORE_SCRATCH_DB:-anzma_restore_check_$(date -u +%Y%m%d_%H%M%S)}
START_EPOCH=$(date -u +%s)
KEEP=0
DRY_RUN=0
BACKUP_INPUT=""
BACKUP_PATH=""
ROWS_PATH=""
TEMP_DIR=""
CONTAINER_ARCHIVE=""
SCRATCH_CREATED=0
FAILED=0
MISMATCHES=0
TABLES_COMPARED=0
EXIT_CODE=0
SOURCE_DB=${PGDATABASE:-}

json_summary() {
    local event=$1 timestamp duration file scratch_value
    timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    duration=$(($(date -u +%s) - START_EPOCH))
    file=${BACKUP_PATH##*/}
    [[ -n "$file" ]] || file=${BACKUP_INPUT##*/}
    [[ "$file" =~ ^anzma_[0-9]{8}_[0-9]{6}\.dump$ ]] || file=invalid
    scratch_value=$SCRATCH_DB
    [[ "$scratch_value" =~ ^[A-Za-z0-9_]{1,63}$ ]] || scratch_value=invalid
    printf '{"event":"%s","backup_file":"%s","scratch_db":"%s","tables_compared":%s,"mismatches":%s,"duration_s":%s,"timestamp":"%s","dry_run":%s}\n' \
        "$event" "$file" "$scratch_value" "$TABLES_COMPARED" "$MISMATCHES" "$duration" "$timestamp" "$DRY_RUN"
}

compose() {
    local args=(-f "$COMPOSE_FILE")
    [[ -z "$COMPOSE_PROJECT" ]] || args+=(-p "$COMPOSE_PROJECT")
    docker compose "${args[@]}" "$@"
}

fail() {
    local message=$1 code=${2:-1}
    printf '%s\n' "$message" >&2
    FAILED=1
    EXIT_CODE=$code
    exit "$code"
}

db_psql() {
    local database=$1 sql=$2
    if [[ "$MODE" == compose ]]; then
        compose exec -T "$DB_SERVICE" sh -c \
            'psql -X -A -t -F "|" -v ON_ERROR_STOP=1 -U "${PGUSER:-${POSTGRES_USER:-postgres}}" -d "$1" -c "$2"' \
            sh "$database" "$sql"
    else
        psql -X -A -t -F '|' -v ON_ERROR_STOP=1 -U "$PGUSER" -d "$database" -c "$sql"
    fi
}

drop_scratch() {
    local terminate_sql="SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$SCRATCH_DB' AND pid <> pg_backend_pid();"
    if ! db_psql postgres "$terminate_sql" >/dev/null 2>>"$TEMP_DIR/errors.log"; then
        printf 'Could not terminate remaining connections to scratch database %s.\n' "$SCRATCH_DB" >&2
        return 1
    fi
    if ! db_psql postgres "DROP DATABASE \"$SCRATCH_DB\";" >/dev/null 2>>"$TEMP_DIR/errors.log"; then
        printf 'Could not drop scratch database %s.\n' "$SCRATCH_DB" >&2
        return 1
    fi
    SCRATCH_CREATED=0
    return 0
}

finish() {
    local status=$? event
    trap - EXIT INT TERM HUP
    if [[ -n "$CONTAINER_ARCHIVE" && "$MODE" == compose ]]; then
        compose exec -T "$DB_SERVICE" rm -f "$CONTAINER_ARCHIVE" >/dev/null 2>&1 || true
    fi
    if (( SCRATCH_CREATED && KEEP == 0 )); then
        drop_scratch || { FAILED=1; EXIT_CODE=1; }
    fi
    if [[ -n "$TEMP_DIR" ]]; then rm -rf "$TEMP_DIR"; fi
    if (( FAILED )) || (( status != 0 )); then event=RESTORE_VERIFY_FAILED; else event=RESTORE_VERIFY_OK; fi
    json_summary "$event"
    if (( FAILED )); then exit "$EXIT_CODE"; fi
    exit "$status"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

usage_error() {
    printf '%s\n' 'Usage: scripts/restore_verify.sh [--backup PATH] [--keep] [--dry-run]' >&2
    fail 'Invalid command-line arguments.' 2
}

while (($#)); do
    case "$1" in
        --backup) (($# >= 2)) || usage_error; BACKUP_INPUT=$2; shift 2 ;;
        --keep) KEEP=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        *) usage_error ;;
    esac
done

[[ "$MODE" == compose || "$MODE" == host ]] || fail 'ANZMA_BACKUP_MODE must be compose or host.' 2
[[ "$DB_SERVICE" =~ ^[A-Za-z0-9_-]+$ ]] || fail 'ANZMA_BACKUP_DB_SERVICE must be a Compose service name.' 2
[[ "$SCRATCH_DB" =~ ^anzma_restore_check[A-Za-z0-9_]*$ && ${#SCRATCH_DB} -le 63 ]] \
    || fail 'Scratch database name must start with anzma_restore_check and contain only letters, digits, and underscores (maximum 63 characters).' 2
[[ -n "$SOURCE_DB" ]] || [[ "$MODE" == compose ]] \
    || fail 'PGDATABASE is required in host mode.' 2
if [[ -n "$SOURCE_DB" ]]; then
    SCRATCH_DB_FOLDED=$(printf '%s' "$SCRATCH_DB" | tr '[:upper:]' '[:lower:]')
    SOURCE_DB_FOLDED=$(printf '%s' "$SOURCE_DB" | tr '[:upper:]' '[:lower:]')
    [[ "$SCRATCH_DB_FOLDED" != "$SOURCE_DB_FOLDED" ]] \
        || fail 'Scratch database name must differ from PGDATABASE.' 2
fi
if [[ "$MODE" == compose ]]; then
    [[ -f "$COMPOSE_FILE" ]] || fail 'ANZMA_COMPOSE_FILE does not name a file.' 2
    command -v docker >/dev/null 2>&1 || fail 'docker is required in compose mode.' 2
else
    [[ -n ${PGUSER:-} ]] || fail 'PGUSER is required in host mode.' 2
    for tool in psql pg_restore; do command -v "$tool" >/dev/null 2>&1 || fail "$tool is required in host mode." 2; done
fi

if [[ "$MODE" == compose ]]; then
    CONTAINER_SOURCE_DB=$(compose exec -T "$DB_SERVICE" sh -c 'printf "%s" "${PGDATABASE:-${POSTGRES_DB:-anzma}}"' 2>/dev/null) \
        || fail 'Could not determine the configured database name from the Compose database service.' 2
    if [[ -n "$SOURCE_DB" && "$SOURCE_DB" != "$CONTAINER_SOURCE_DB" ]]; then
        fail 'PGDATABASE does not match the database configured in the Compose database service.' 2
    fi
    SOURCE_DB=$CONTAINER_SOURCE_DB
fi
SCRATCH_DB_FOLDED=$(printf '%s' "$SCRATCH_DB" | tr '[:upper:]' '[:lower:]')
SOURCE_DB_FOLDED=$(printf '%s' "$SOURCE_DB" | tr '[:upper:]' '[:lower:]')
[[ "$SCRATCH_DB_FOLDED" != "$SOURCE_DB_FOLDED" ]] \
    || fail 'Scratch database name must differ from PGDATABASE.' 2

if [[ -z "$BACKUP_INPUT" ]]; then
    if [[ "$BACKUP_DIR" != /* ]]; then BACKUP_DIR="$REPO_ROOT/$BACKUP_DIR"; fi
    [[ -d "$BACKUP_DIR" ]] || fail 'Backup directory does not exist.' 1
    BACKUP_DIR=$(cd -- "$BACKUP_DIR" && pwd -P)
    shopt -s nullglob
    local_candidates=("$BACKUP_DIR"/anzma_????????_??????.dump)
    shopt -u nullglob
    if ((${#local_candidates[@]} == 0)); then fail 'No named backup archives were found.' 1; fi
    BACKUP_PATH=$(printf '%s\n' "${local_candidates[@]}" | sort -r | while IFS= read -r candidate; do
        [[ -s "$candidate" && -s "$candidate.sha256" && -s "$candidate.rowcounts.txt" ]] || continue
        checksum=$(awk 'NR==1 {print $1}' "$candidate.sha256")
        [[ "$checksum" =~ ^[0-9a-fA-F]{64}$ ]] || continue
        if command -v sha256sum >/dev/null 2>&1; then actual=$(sha256sum "$candidate" | awk '{print $1}')
        elif command -v shasum >/dev/null 2>&1; then actual=$(shasum -a 256 "$candidate" | awk '{print $1}')
        else break; fi
        [[ "$actual" == "$checksum" ]] || continue
        [[ "${candidate##*/}" =~ ^anzma_[0-9]{8}_[0-9]{6}\.dump$ ]] || continue
        printf '%s\n' "$candidate"
        break
    done)
    [[ -n "$BACKUP_PATH" ]] || fail 'No backup with a matching SHA-256 sidecar and row-count file was found.' 1
else
    if [[ "$BACKUP_INPUT" != /* ]]; then BACKUP_INPUT="$REPO_ROOT/$BACKUP_INPUT"; fi
    BACKUP_PATH=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$BACKUP_INPUT") \
        || fail 'Could not resolve backup path.' 1
fi
[[ "${BACKUP_PATH##*/}" =~ ^anzma_[0-9]{8}_[0-9]{6}\.dump$ ]] \
    || fail 'Backup filename does not match the scripts/backup_db.sh naming pattern.' 1
ROWS_PATH="${BACKUP_PATH}.rowcounts.txt"

[[ -f "$BACKUP_PATH" && -s "$BACKUP_PATH" ]] || fail 'Backup archive is missing or empty.' 1
[[ -f "${BACKUP_PATH}.sha256" && -s "${BACKUP_PATH}.sha256" ]] || fail 'SHA-256 sidecar is missing or empty.' 1
[[ -f "$ROWS_PATH" && -s "$ROWS_PATH" ]] || fail 'Row-count sidecar is missing or empty.' 1
EXPECTED_HASH=$(awk 'NR==1 {print $1}' "${BACKUP_PATH}.sha256")
[[ "$EXPECTED_HASH" =~ ^[0-9a-fA-F]{64}$ ]] || fail 'SHA-256 sidecar does not contain a valid digest.' 1
if command -v sha256sum >/dev/null 2>&1; then ACTUAL_HASH=$(sha256sum "$BACKUP_PATH" | awk '{print $1}')
elif command -v shasum >/dev/null 2>&1; then ACTUAL_HASH=$(shasum -a 256 "$BACKUP_PATH" | awk '{print $1}')
else fail 'sha256sum or shasum is required to verify the archive.' 2; fi
[[ "$ACTUAL_HASH" == "$EXPECTED_HASH" ]] || fail 'Backup SHA-256 does not match its sidecar.' 1

TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/anzma-restore-verify.XXXXXX") || fail 'Could not create a private temporary directory.' 1
chmod 700 "$TEMP_DIR"
touch "$TEMP_DIR/errors.log"
chmod 600 "$TEMP_DIR/errors.log"

if [[ "$MODE" == compose ]]; then
    CONTAINER_ARCHIVE="/tmp/anzma-restore-verify-${$}-$(date -u +%Y%m%d%H%M%S).dump"
    compose exec -T "$DB_SERVICE" sh -c 'umask 077; cat > "$1"' sh "$CONTAINER_ARCHIVE" \
        < "$BACKUP_PATH" 2>>"$TEMP_DIR/errors.log" || fail 'Could not stage the archive in the configured Compose database service.' 1
    compose exec -T "$DB_SERVICE" pg_restore --list "$CONTAINER_ARCHIVE" \
        >/dev/null 2>>"$TEMP_DIR/errors.log" || fail 'pg_restore --list rejected the backup archive.' 1
else
    pg_restore --list "$BACKUP_PATH" >/dev/null 2>>"$TEMP_DIR/errors.log" \
        || fail 'pg_restore --list rejected the backup archive.' 1
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
    printf 'DRY RUN: would restore %s into %s, verify schema/migrations/row counts, then drop the scratch database.\n' \
        "$BACKUP_PATH" "$SCRATCH_DB"
    exit 0
fi

DB_EXISTS=$(db_psql postgres "SELECT 1 FROM pg_database WHERE datname = '$SCRATCH_DB';" 2>>"$TEMP_DIR/errors.log") \
    || fail 'Could not check whether the scratch database already exists.' 1
[[ -z "$DB_EXISTS" ]] || fail "Scratch database $SCRATCH_DB already exists." 2
db_psql postgres "CREATE DATABASE \"$SCRATCH_DB\";" >/dev/null 2>>"$TEMP_DIR/errors.log" \
    || fail "Could not create scratch database $SCRATCH_DB." 1
SCRATCH_CREATED=1

if [[ "$MODE" == compose ]]; then
    if ! compose exec -T "$DB_SERVICE" sh -c \
        'pg_restore --no-owner --no-privileges -U "${PGUSER:-${POSTGRES_USER:-postgres}}" -d "$1" "$2"' \
        sh "$SCRATCH_DB" "$CONTAINER_ARCHIVE" \
        >/dev/null 2>>"$TEMP_DIR/errors.log"; then
        grep -E '^pg_restore: error:' "$TEMP_DIR/errors.log" | head -1 >&2 || true
        fail 'pg_restore failed while loading the scratch database.' 1
    fi
    compose exec -T "$DB_SERVICE" sh -c \
        'psql -X -A -t -F "|" -v ON_ERROR_STOP=1 -U "${PGUSER:-${POSTGRES_USER:-postgres}}" -d "$1" -f -' \
        sh "$SCRATCH_DB" < "$REPO_ROOT/scripts/verify_schema.sql" > "$TEMP_DIR/schema.out" 2>>"$TEMP_DIR/errors.log" \
        || fail 'scripts/verify_schema.sql could not complete against the scratch database.' 1
else
    pg_restore --no-owner --no-privileges -U "$PGUSER" -d "$SCRATCH_DB" "$BACKUP_PATH" \
        >/dev/null 2>>"$TEMP_DIR/errors.log" || fail 'pg_restore failed while loading the scratch database.' 1
    psql -X -A -t -F '|' -v ON_ERROR_STOP=1 -U "$PGUSER" -d "$SCRATCH_DB" \
        -f "$REPO_ROOT/scripts/verify_schema.sql" > "$TEMP_DIR/schema.out" 2>>"$TEMP_DIR/errors.log" \
        || fail 'scripts/verify_schema.sql could not complete against the scratch database.' 1
fi
if ! awk -F '|' '$5 != "OK" { print $2; failed=1 } END { exit !(failed == 0) }' "$TEMP_DIR/schema.out"; then
    FAILED_CHECKS=$(awk -F '|' '$5 != "OK" { print $2 }' "$TEMP_DIR/schema.out" | paste -sd ', ' -)
    printf 'Schema verification failed: %s\n' "${FAILED_CHECKS:-OVERALL result missing or not OK}" >&2
    MISMATCHES=$((MISMATCHES + 1))
    fail 'Schema verification did not report all checks OK.' 1
fi
grep -Eq '^99\|OVERALL\|all OK\|\|OK$' "$TEMP_DIR/schema.out" \
    || { MISMATCHES=$((MISMATCHES + 1)); fail 'Schema verification did not contain OVERALL OK.' 1; }

expected_versions=()
for migration in "$REPO_ROOT"/db/migrations/[0-9][0-9][0-9]_*.sql; do
    if grep -Eiq 'INSERT[[:space:]]+INTO[[:space:]]+schema_migrations' "$migration"; then
        expected_versions+=("$(basename "$migration" .sql)")
    fi
done
if ((${#expected_versions[@]} == 0)); then fail 'No migration files write schema_migrations; cannot derive the expected ledger.' 1; fi
EXPECTED_VERSIONS=$(printf '%s\n' "${expected_versions[@]}" | sort)
if [[ "$MODE" == compose ]]; then
    ACTUAL_VERSIONS=$(compose exec -T "$DB_SERVICE" sh -c \
        'psql -X -A -t -v ON_ERROR_STOP=1 -U "${PGUSER:-${POSTGRES_USER:-postgres}}" -d "$1" -c "SELECT version FROM schema_migrations ORDER BY version"' \
        sh "$SCRATCH_DB" 2>>"$TEMP_DIR/errors.log") || fail 'Could not read schema_migrations from scratch database.' 1
else
    ACTUAL_VERSIONS=$(psql -X -A -t -v ON_ERROR_STOP=1 -U "$PGUSER" -d "$SCRATCH_DB" \
        -c 'SELECT version FROM schema_migrations ORDER BY version' 2>>"$TEMP_DIR/errors.log") \
        || fail 'Could not read schema_migrations from scratch database.' 1
fi
ACTUAL_VERSIONS=$(printf '%s\n' "$ACTUAL_VERSIONS" | sed '/^[[:space:]]*$/d' | sort)
if [[ "$ACTUAL_VERSIONS" != "$EXPECTED_VERSIONS" ]]; then
    printf 'Migration ledger mismatch: expected [%s], got [%s].\n' \
        "$(printf '%s' "$EXPECTED_VERSIONS" | tr '\n' ' ')" "$(printf '%s' "$ACTUAL_VERSIONS" | tr '\n' ' ')" >&2
    MISMATCHES=$((MISMATCHES + 1))
    fail 'schema_migrations does not match the versions recorded by migration files.' 1
fi

seen_tables=""
COUNT_LINES=0
while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^([a-z_][a-z0-9_]*)=([0-9]+)$ ]] \
        || { MISMATCHES=$((MISMATCHES + 1)); fail "Malformed row-count line: $line" 1; }
    table=${BASH_REMATCH[1]}
    expected_count=${BASH_REMATCH[2]}
    if printf '%s\n' "$seen_tables" | grep -Fxq "$table"; then
        MISMATCHES=$((MISMATCHES + 1))
        fail "Duplicate row-count entry for $table." 1
    fi
    seen_tables="${seen_tables}${table}"$'\n'
    COUNT_LINES=$((COUNT_LINES + 1))
    sql="SELECT count(*) FROM public.\"$table\";"
    if [[ "$MODE" == compose ]]; then
        actual_count=$(compose exec -T "$DB_SERVICE" sh -c \
            'psql -X -A -t -v ON_ERROR_STOP=1 -U "${PGUSER:-${POSTGRES_USER:-postgres}}" -d "$1" -c "$2"' \
            sh "$SCRATCH_DB" "$sql" 2>>"$TEMP_DIR/errors.log" </dev/null) \
            || { printf 'Could not count table %s in scratch database.\n' "$table" >&2; MISMATCHES=$((MISMATCHES + 1)); continue; }
    else
        actual_count=$(psql -X -A -t -v ON_ERROR_STOP=1 -U "$PGUSER" -d "$SCRATCH_DB" \
            -c "$sql" 2>>"$TEMP_DIR/errors.log") \
            || { printf 'Could not count table %s in scratch database.\n' "$table" >&2; MISMATCHES=$((MISMATCHES + 1)); continue; }
    fi
    actual_count=$(printf '%s' "$actual_count" | tr -d '[:space:]')
    [[ "$actual_count" =~ ^[0-9]+$ ]] \
        || { printf 'Invalid row count returned for table %s.\n' "$table" >&2; MISMATCHES=$((MISMATCHES + 1)); continue; }
    TABLES_COMPARED=$((TABLES_COMPARED + 1))
    if [[ "$actual_count" != "$expected_count" ]]; then
        printf 'Row count mismatch for table %s: backup=%s scratch=%s.\n' \
            "$table" "$expected_count" "$actual_count" >&2
        MISMATCHES=$((MISMATCHES + 1))
    fi
done < "$ROWS_PATH"
(( COUNT_LINES > 0 )) || { MISMATCHES=$((MISMATCHES + 1)); fail 'Row-count sidecar contains no table counts.' 1; }
if (( MISMATCHES != 0 )); then fail 'One or more restore verification checks failed.' 1; fi
exit 0
