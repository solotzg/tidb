#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${SCRIPT_DIR}/.state"
PID_FILE="${STATE_DIR}/playground.pid"

if [[ ! -f "${PID_FILE}" ]]; then
  echo "[mv-it] no playground pid file, skip stop"
  exit 0
fi

pid="$(cat "${PID_FILE}")"
if [[ -z "${pid}" ]]; then
  echo "[mv-it] empty pid file, cleanup only"
  rm -f "${PID_FILE}"
  exit 0
fi

if kill -0 "${pid}" >/dev/null 2>&1; then
  echo "[mv-it] stopping playground pid=${pid}"
  kill -TERM "-${pid}" >/dev/null 2>&1 || kill -TERM "${pid}" >/dev/null 2>&1 || true
  sleep 2
  if kill -0 "${pid}" >/dev/null 2>&1; then
    kill -KILL "-${pid}" >/dev/null 2>&1 || kill -KILL "${pid}" >/dev/null 2>&1 || true
  fi
fi

rm -f "${PID_FILE}"
echo "[mv-it] playground stopped"
