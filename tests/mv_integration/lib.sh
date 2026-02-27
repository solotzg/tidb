#!/usr/bin/env bash

set -euo pipefail

MYSQL_HOST="${MYSQL_HOST:-127.0.0.1}"
MYSQL_PORT="${MYSQL_PORT:-4000}"
MYSQL_USER="${MYSQL_USER:-root}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:-}"
METRICS_HOST="${METRICS_HOST:-127.0.0.1}"
METRICS_PORT="${METRICS_PORT:-10080}"

mysql_cmd() {
  local args=(
    --host "${MYSQL_HOST}"
    --port "${MYSQL_PORT}"
    --user "${MYSQL_USER}"
    --batch
    --raw
    --skip-column-names
  )
  if [[ -n "${MYSQL_PASSWORD}" ]]; then
    args+=(--password="${MYSQL_PASSWORD}")
  fi
  command mysql "${args[@]}" "$@"
}

wait_tidb_ready() {
  local retries="${1:-120}"
  local i=0
  while (( i < retries )); do
    if mysql_cmd -e "SELECT 1;" >/dev/null 2>&1; then
      return 0
    fi
    ((i++))
    sleep 1
  done
  echo "[mv-it] TiDB is not ready after ${retries}s" >&2
  return 1
}

run_sql() {
  mysql_cmd -e "$1"
}

query_scalar() {
  mysql_cmd -e "$1" | tr -d '\r'
}

metrics_url() {
  echo "http://${METRICS_HOST}:${METRICS_PORT}/metrics"
}

metrics_cmd() {
  command curl --silent --show-error --fail "$(metrics_url)"
}

wait_metrics_ready() {
  local retries="${1:-120}"
  local i=0
  while (( i < retries )); do
    if metrics_cmd >/dev/null 2>&1; then
      return 0
    fi
    ((i++))
    sleep 1
  done
  echo "[mv-it] metrics endpoint is not ready after ${retries}s: $(metrics_url)" >&2
  return 1
}

# query_metric_sum returns the sum of metric samples that match both metric name and label substring.
# - metric example: tidb_mv_service_run_event_total
# - label_filter example: type="fetch_mviews_ok"
query_metric_sum() {
  local metric="$1"
  local label_filter="${2:-}"
  metrics_cmd | awk -v metric="${metric}" -v label_filter="${label_filter}" '
    {
      # Match exact metric series lines, not comments and not *_created derivatives.
      is_metric_line = (index($0, metric "{") == 1 || index($0, metric " ") == 1)
      if (!is_metric_line) {
        next
      }
      if (label_filter != "" && index($0, label_filter) == 0) {
        next
      }
      sum += ($NF + 0)
      found = 1
    }
    END {
      if (found) {
        printf "%.6f", sum
      }
    }
  '
}

metric_sum_or_zero() {
  local metric="$1"
  local label_filter="${2:-}"
  local value
  value="$(query_metric_sum "${metric}" "${label_filter}")"
  if [[ -z "${value}" ]]; then
    echo "0"
  else
    echo "${value}"
  fi
}

wait_metric_ge() {
  local metric="$1"
  local label_filter="$2"
  local expected="$3"
  local retries="$4"
  local sleep_sec="$5"
  local message="$6"

  local i=0
  local actual="0"
  while (( i < retries )); do
    actual="$(metric_sum_or_zero "${metric}" "${label_filter}")"
    if awk -v a="${actual}" -v b="${expected}" 'BEGIN{exit !(a+0 >= b+0)}'; then
      echo "[mv-it][PASS] ${message}: metric=${metric}, labels=${label_filter}, actual=${actual}, expected>=${expected}"
      return 0
    fi
    ((i++))
    sleep "${sleep_sec}"
  done

  echo "[mv-it][FAIL] ${message}: metric=${metric}, labels=${label_filter}, actual=${actual}, expected>=${expected}" >&2
  return 1
}

wait_metric_increase() {
  local metric="$1"
  local label_filter="$2"
  local baseline="$3"
  local delta="$4"
  local retries="$5"
  local sleep_sec="$6"
  local message="$7"

  local target
  target="$(awk -v base="${baseline}" -v inc="${delta}" 'BEGIN{printf "%.6f", base+inc}')"
  wait_metric_ge "${metric}" "${label_filter}" "${target}" "${retries}" "${sleep_sec}" "${message}"
}

assert_eq() {
  local expected="$1"
  local actual="$2"
  local message="$3"
  if [[ "${expected}" != "${actual}" ]]; then
    echo "[mv-it][FAIL] ${message}: expected=${expected}, actual=${actual}" >&2
    exit 1
  fi
  echo "[mv-it][PASS] ${message}: ${actual}"
}

expect_sql_error() {
  local sql="$1"
  local message="$2"
  if run_sql "${sql}" >/dev/null 2>&1; then
    echo "[mv-it][FAIL] ${message}: statement unexpectedly succeeded" >&2
    exit 1
  fi
  echo "[mv-it][PASS] ${message}"
}

expect_sql_error_contains() {
  local sql="$1"
  local expected="$2"
  local message="$3"
  local output=""
  if output="$(mysql_cmd -e "${sql}" 2>&1)"; then
    echo "[mv-it][FAIL] ${message}: statement unexpectedly succeeded" >&2
    exit 1
  fi
  if [[ "${output}" != *"${expected}"* ]]; then
    echo "[mv-it][FAIL] ${message}: expected error to contain '${expected}', got '${output}'" >&2
    exit 1
  fi
  echo "[mv-it][PASS] ${message}"
}
