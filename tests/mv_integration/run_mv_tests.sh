#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

echo "[mv-it] waiting tidb readiness on ${MYSQL_HOST}:${MYSQL_PORT}"
wait_tidb_ready 120

echo "[mv-it] test: MV system tables bootstrap"
assert_eq "4" "$(query_scalar "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='mysql' AND table_name IN ('tidb_mview_refresh_info','tidb_mlog_purge_info','tidb_mview_refresh_hist','tidb_mlog_purge_hist');")" "bootstrap has 4 MV system tables"
"${SCRIPT_DIR}/run_mview_tests.sh"
"${SCRIPT_DIR}/run_mvlog_tests.sh"
"${SCRIPT_DIR}/run_mvservice_metrics_tests.sh"

echo "[mv-it] all MV, MVLOG and MV service metrics integration tests passed"
