#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-4000}"
USER="${USER:-root}"
PASSWORD="${PASSWORD:-}"
DATABASE="${DATABASE:-test}"
STATUS="${STATUS:-http://127.0.0.1:10080}"
ROWS="${ROWS:-50000}"
BATCH_SIZE="${BATCH_SIZE:-1000}"
CONCURRENCY="${CONCURRENCY:-100}"
DURATION="${DURATION:-180}"
WARMUP="${WARMUP:-30}"
IN_LIST="${IN_LIST:-256}"
HIT_RATIO="${HIT_RATIO:-0.45}"
HIT_PATTERN="${HIT_PATTERN:-contiguous}"
SPLIT_TABLE_REGIONS="${SPLIT_TABLE_REGIONS:-0}"
SPLIT_INDEX_REGIONS="${SPLIT_INDEX_REGIONS:-0}"
TARGET_QPS="${TARGET_QPS:-0}"
MODE="${MODE:-select}"
USERNAME_STATE="${USERNAME_STATE:-stale}"
TABLES="${TABLES:-dh_yuebao_info,dh_active_promote_details,dh_active_details}"
OUT="${OUT:-result.json}"
HEAP_PREFIX="${HEAP_PREFIX:-}"
PREPARE="${PREPARE:-1}"
RESET_ONLY="${RESET_ONLY:-0}"

args=(
  --host "$HOST"
  --port "$PORT"
  --user "$USER"
  --password "$PASSWORD"
  --database "$DATABASE"
  --status "$STATUS"
  --rows "$ROWS"
  --batch-size "$BATCH_SIZE"
  --concurrency "$CONCURRENCY"
  --duration "$DURATION"
  --warmup "$WARMUP"
  --in-list "$IN_LIST"
  --hit-ratio "$HIT_RATIO"
  --hit-pattern "$HIT_PATTERN"
  --split-table-regions "$SPLIT_TABLE_REGIONS"
  --split-index-regions "$SPLIT_INDEX_REGIONS"
  --target-qps "$TARGET_QPS"
  --mode "$MODE"
  --username-state "$USERNAME_STATE"
  --tables "$TABLES"
  --out "$OUT"
)

if [[ "$PREPARE" == "1" ]]; then
  args+=(--prepare)
fi

if [[ "$RESET_ONLY" == "1" ]]; then
  args+=(--reset-only)
fi

if [[ -n "$HEAP_PREFIX" ]]; then
  args+=(--heap-prefix "$HEAP_PREFIX")
fi

python3 "$SCRIPT_DIR/bench.py" "${args[@]}"
