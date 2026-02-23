#!/usr/bin/env bash
set -euo pipefail

SCHEMA="${SCHEMA:-news}"
INCOMING_DIR="${INCOMING_DIR:-/opt/mcp-news/incoming}"
ARCHIVE_DIR="${ARCHIVE_DIR:-/opt/mcp-news/archive}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-3306}"
DB_USER="${DB_USER:-root}"
DB_PASSWORD="${DB_PASSWORD:-}"

if [[ -z "${DB_PASSWORD}" ]]; then
  echo "DB_PASSWORD is required." >&2
  exit 1
fi

mkdir -p "${INCOMING_DIR}" "${ARCHIVE_DIR}"

latest_dump="$(ls -1t "${INCOMING_DIR}/${SCHEMA}"_*.sql.gz 2>/dev/null | head -n 1 || true)"

if [[ -z "${latest_dump}" ]]; then
  echo "No dump files found in ${INCOMING_DIR}." >&2
  exit 1
fi

tmp_sql="$(mktemp "/tmp/${SCHEMA}_import_XXXXXX.sql")"
trap 'rm -f "${tmp_sql}"' EXIT

echo "Decompressing ${latest_dump}..."
gunzip -c "${latest_dump}" > "${tmp_sql}"

echo "Importing schema ${SCHEMA}..."
export MYSQL_PWD="${DB_PASSWORD}"
mysql \
  --host="${DB_HOST}" \
  --port="${DB_PORT}" \
  --user="${DB_USER}" \
  < "${tmp_sql}"
unset MYSQL_PWD

echo "Running sanity check..."
export MYSQL_PWD="${DB_PASSWORD}"
mysql \
  --host="${DB_HOST}" \
  --port="${DB_PORT}" \
  --user="${DB_USER}" \
  --batch \
  --skip-column-names \
  -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='${SCHEMA}';"
unset MYSQL_PWD

timestamp="$(date '+%Y%m%d_%H%M%S')"
mv "${latest_dump}" "${ARCHIVE_DIR}/$(basename "${latest_dump}" .gz)_imported_${timestamp}.gz"

find "${ARCHIVE_DIR}" -type f -name "${SCHEMA}_*.sql*_imported_*.gz" -mtime "+${RETENTION_DAYS}" -delete
echo "Import complete."
