#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

echo "[mv-it] test: MV create/refresh/drop flow"
run_sql "DROP DATABASE IF EXISTS mv_it_mv;"
run_sql "CREATE DATABASE mv_it_mv;"
run_sql "CREATE TABLE mv_it_mv.t (a INT NOT NULL, b INT NOT NULL);"
run_sql "INSERT INTO mv_it_mv.t VALUES (1, 10), (1, 5), (2, 7);"

expect_sql_error_contains \
  "CREATE MATERIALIZED VIEW mv_it_mv.mv_no_log (a, cnt) AS SELECT a, count(1) FROM mv_it_mv.t GROUP BY a;" \
  "materialized view log does not exist" \
  "create MV without MVLOG is rejected"

run_sql "CREATE MATERIALIZED VIEW LOG ON mv_it_mv.t (a, b) PURGE IMMEDIATE;"
run_sql "CREATE MATERIALIZED VIEW mv_it_mv.mv_sum (a, s, cnt) REFRESH FAST NEXT now() AS SELECT a, sum(b), count(1) FROM mv_it_mv.t GROUP BY a;"

assert_eq "1:15:2,2:7:1" "$(query_scalar "SELECT GROUP_CONCAT(CONCAT(a, ':', s, ':', cnt) ORDER BY a SEPARATOR ',') FROM mv_it_mv.mv_sum;")" "mv initial data snapshot"

expect_sql_error_contains \
  "BEGIN; REFRESH MATERIALIZED VIEW mv_it_mv.mv_sum COMPLETE;" \
  "cannot run REFRESH MATERIALIZED VIEW in explicit transaction" \
  "refresh is rejected in explicit transaction"

run_sql "INSERT INTO mv_it_mv.t VALUES (2, 3), (3, 4);"
assert_eq "1:15:2,2:7:1" "$(query_scalar "SELECT GROUP_CONCAT(CONCAT(a, ':', s, ':', cnt) ORDER BY a SEPARATOR ',') FROM mv_it_mv.mv_sum;")" "mv stays stale before refresh"

run_sql "REFRESH MATERIALIZED VIEW mv_it_mv.mv_sum COMPLETE;"
assert_eq "1:15:2,2:10:2,3:4:1" "$(query_scalar "SELECT GROUP_CONCAT(CONCAT(a, ':', s, ':', cnt) ORDER BY a SEPARATOR ',') FROM mv_it_mv.mv_sum;")" "complete refresh updates mv data"

run_sql "INSERT INTO mv_it_mv.t VALUES (3, 6);"
run_sql "REFRESH MATERIALIZED VIEW mv_it_mv.mv_sum WITH SYNC MODE COMPLETE;"
assert_eq "1:15:2,2:10:2,3:10:2" "$(query_scalar "SELECT GROUP_CONCAT(CONCAT(a, ':', s, ':', cnt) ORDER BY a SEPARATOR ',') FROM mv_it_mv.mv_sum;")" "with sync mode complete keeps synchronous complete semantics"

mv_id="$(query_scalar "SELECT tidb_table_id FROM information_schema.tables WHERE table_schema='mv_it_mv' AND table_name='mv_sum';")"
if [[ -z "${mv_id}" ]]; then
  echo "[mv-it][FAIL] cannot resolve mv table id for mv_it_mv.mv_sum" >&2
  exit 1
fi

last_success_tso_before_fast="$(query_scalar "SELECT LAST_SUCCESS_READ_TSO FROM mysql.tidb_mview_refresh_info WHERE MVIEW_ID = ${mv_id};")"

expect_sql_error_contains \
  "REFRESH MATERIALIZED VIEW mv_it_mv.mv_sum FAST;" \
  "FAST refresh is not yet supported" \
  "fast(incremental) refresh reports unsupported"

expect_sql_error_contains \
  "REFRESH MATERIALIZED VIEW mv_it_mv.mv_sum WITH SYNC MODE FAST;" \
  "FAST refresh is not yet supported" \
  "with sync mode fast(incremental) refresh reports unsupported"

assert_eq "${last_success_tso_before_fast}" "$(query_scalar "SELECT LAST_SUCCESS_READ_TSO FROM mysql.tidb_mview_refresh_info WHERE MVIEW_ID = ${mv_id};")" "failed fast refresh does not advance success read tso"

assert_eq "1" "$(query_scalar "SELECT LAST_SUCCESS_READ_TSO > 0 FROM mysql.tidb_mview_refresh_info WHERE MVIEW_ID = ${mv_id};")" "refresh info has non-zero read tso"
assert_eq "1" "$(query_scalar "SELECT COUNT(*) > 0 FROM mysql.tidb_mview_refresh_hist WHERE MVIEW_ID = ${mv_id} AND REFRESH_STATUS = 'success' AND REFRESH_METHOD = 'complete';")" "refresh history records complete success"

expect_sql_error_contains \
  "DROP MATERIALIZED VIEW LOG ON mv_it_mv.t;" \
  "dependent materialized views exist" \
  "drop MVLOG blocked while dependent MV exists"

run_sql "DROP MATERIALIZED VIEW mv_it_mv.mv_sum;"
run_sql "DROP MATERIALIZED VIEW LOG ON mv_it_mv.t;"
run_sql "DROP DATABASE IF EXISTS mv_it_mv;"

echo "[mv-it] mview integration tests passed"
