#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${SCRIPT_DIR}/.state"
PID_FILE="${STATE_DIR}/playground.pid"
LOG_FILE="${STATE_DIR}/playground.log"

PLAYGROUND_CMD='tiup playground v8.5.4 --tiflash=0 --db.binpath=/Users/solotzg/Work/tidb/bin/tidb-server'

source "${SCRIPT_DIR}/lib.sh"

mkdir -p "${STATE_DIR}"

if [[ -f "${PID_FILE}" ]]; then
  old_pid="$(cat "${PID_FILE}")"
  if [[ -n "${old_pid}" ]] && kill -0 "${old_pid}" >/dev/null 2>&1; then
    echo "[mv-it] playground already running, pid=${old_pid}"
    wait_tidb_ready 120
    exit 0
  fi
fi

echo "[mv-it] starting playground"
echo "[mv-it] command: ${PLAYGROUND_CMD}"
setsid bash -lc "${PLAYGROUND_CMD}" >"${LOG_FILE}" 2>&1 < /dev/null &
pid=$!
echo "${pid}" > "${PID_FILE}"

if ! wait_tidb_ready 180; then
  echo "[mv-it] failed to start playground. log tail:" >&2
  tail -n 100 "${LOG_FILE}" >&2 || true
  exit 1
fi

echo "[mv-it] playground ready, pid=${pid}"
