#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

echo "[mv-it] test: MVLOG DML flow"
run_sql "DROP DATABASE IF EXISTS mv_it_mvlog;"
run_sql "CREATE DATABASE mv_it_mvlog;"
run_sql "CREATE TABLE mv_it_mvlog.t (id INT PRIMARY KEY, uk INT UNIQUE, v INT, extra INT);"
run_sql "CREATE MATERIALIZED VIEW LOG ON mv_it_mvlog.t (id, uk, v);"

assert_eq "1" "$(query_scalar "SELECT COUNT(*) FROM mysql.tidb_mlog_purge_info p WHERE p.MLOG_ID = (SELECT tidb_table_id FROM information_schema.tables WHERE table_schema='mv_it_mvlog' AND table_name='\$mlog\$t');")" "mlog purge metadata exists"

run_sql "INSERT INTO mv_it_mvlog.t VALUES (1,10,100,1000);"
assert_eq "1" "$(query_scalar "SELECT COUNT(*) FROM mv_it_mvlog.\`\$mlog\$t\`;")" "insert writes one mlog row"

run_sql "DELETE FROM mv_it_mvlog.\`\$mlog\$t\`;"
run_sql "UPDATE mv_it_mvlog.t SET v=101 WHERE id=1;"
assert_eq "2" "$(query_scalar "SELECT COUNT(*) FROM mv_it_mvlog.\`\$mlog\$t\`;")" "update tracked column writes old/new rows"

run_sql "DELETE FROM mv_it_mvlog.\`\$mlog\$t\`;"
run_sql "UPDATE mv_it_mvlog.t SET extra=2000 WHERE id=1;"
assert_eq "0" "$(query_scalar "SELECT COUNT(*) FROM mv_it_mvlog.\`\$mlog\$t\`;")" "update untracked column does not write mlog"

run_sql "DELETE FROM mv_it_mvlog.\`\$mlog\$t\`;"
run_sql "DELETE FROM mv_it_mvlog.t WHERE id=1;"
assert_eq "1" "$(query_scalar "SELECT COUNT(*) FROM mv_it_mvlog.\`\$mlog\$t\`;")" "delete writes one mlog row"

echo "[mv-it] test: MVLOG purge command and state with dependent MV"
run_sql "CREATE TABLE mv_it_mvlog.purge_t (id INT NOT NULL, v INT NOT NULL, PRIMARY KEY(id));"
run_sql "CREATE MATERIALIZED VIEW LOG ON mv_it_mvlog.purge_t (id, v) PURGE IMMEDIATE;"
run_sql "CREATE MATERIALIZED VIEW mv_it_mvlog.mv_purge (id, s, cnt) REFRESH FAST NEXT now() AS SELECT id, sum(v), count(1) FROM mv_it_mvlog.purge_t GROUP BY id;"
run_sql "INSERT INTO mv_it_mvlog.purge_t VALUES (1, 10), (2, 20), (3, 30);"

expect_sql_error_contains \
  "BEGIN; PURGE MATERIALIZED VIEW LOG ON mv_it_mvlog.purge_t;" \
  "cannot run PURGE MATERIALIZED VIEW LOG in explicit transaction" \
  "purge is rejected in explicit transaction"

assert_eq "3" "$(query_scalar "SELECT COUNT(*) FROM mv_it_mvlog.\`\$mlog\$purge_t\`;")" "mlog has rows before purge"
run_sql "PURGE MATERIALIZED VIEW LOG ON mv_it_mvlog.purge_t;"
assert_eq "3" "$(query_scalar "SELECT COUNT(*) FROM mv_it_mvlog.\`\$mlog\$purge_t\`;")" "before mv refresh, purge keeps newer mlog rows"

run_sql "REFRESH MATERIALIZED VIEW mv_it_mvlog.mv_purge COMPLETE;"
run_sql "PURGE MATERIALIZED VIEW LOG ON mv_it_mvlog.purge_t;"
assert_eq "0" "$(query_scalar "SELECT COUNT(*) FROM mv_it_mvlog.\`\$mlog\$purge_t\`;")" "after mv refresh, purge removes stale mlog rows"

mlog_id="$(query_scalar "SELECT tidb_table_id FROM information_schema.tables WHERE table_schema='mv_it_mvlog' AND table_name='\$mlog\$purge_t';")"
if [[ -z "${mlog_id}" ]]; then
  echo "[mv-it][FAIL] cannot resolve mlog id for mv_it_mvlog.\`\$mlog\$purge_t\`" >&2
  exit 1
fi

assert_eq "1" "$(query_scalar "SELECT PURGE_STATUS='success' AND PURGE_ROWS>=1 FROM mysql.tidb_mlog_purge_hist WHERE MLOG_ID=${mlog_id} ORDER BY PURGE_JOB_ID DESC LIMIT 1;")" "purge history records successful row deletion"
assert_eq "1" "$(query_scalar "SELECT LAST_PURGED_TSO IS NOT NULL FROM mysql.tidb_mlog_purge_info WHERE MLOG_ID=${mlog_id};")" "purge info stores last purged tso"

purge_hist_before="$(query_scalar "SELECT COUNT(*) FROM mysql.tidb_mlog_purge_hist WHERE MLOG_ID=${mlog_id};")"
run_sql "PURGE MATERIALIZED VIEW LOG ON mv_it_mvlog.purge_t;"
purge_hist_after="$(query_scalar "SELECT COUNT(*) FROM mysql.tidb_mlog_purge_hist WHERE MLOG_ID=${mlog_id};")"
assert_eq "$((purge_hist_before + 1))" "${purge_hist_after}" "second purge appends one purge history row"
assert_eq "1" "$(query_scalar "SELECT PURGE_STATUS='success' AND PURGE_ROWS=0 FROM mysql.tidb_mlog_purge_hist WHERE MLOG_ID=${mlog_id} ORDER BY PURGE_JOB_ID DESC LIMIT 1;")" "second purge short-circuits with zero rows"

echo "[mv-it] test: MVLOG purge schedule metadata"
run_sql "CREATE TABLE mv_it_mvlog.purge_sched_t (id INT PRIMARY KEY, v INT);"
run_sql "CREATE MATERIALIZED VIEW LOG ON mv_it_mvlog.purge_sched_t (id, v) PURGE START WITH date_add(now(), interval 30 minute) NEXT date_add(now(), interval 1 hour);"
assert_eq "1" "$(query_scalar "SELECT NEXT_TIME IS NOT NULL FROM mysql.tidb_mlog_purge_info WHERE MLOG_ID = (SELECT tidb_table_id FROM information_schema.tables WHERE table_schema='mv_it_mvlog' AND table_name='\$mlog\$purge_sched_t');")" "purge schedule sets NEXT_TIME"

echo "[mv-it] test: MVLOG schema-change behavior"
run_sql "DROP TABLE IF EXISTS mv_it_mvlog.t;"
run_sql "DROP TABLE IF EXISTS mv_it_mvlog.\`\$mlog\$t\`;"
run_sql "CREATE TABLE mv_it_mvlog.t (id INT PRIMARY KEY, tracked INT, untracked INT);"
run_sql "CREATE MATERIALIZED VIEW LOG ON mv_it_mvlog.t (id, tracked);"
run_sql "INSERT INTO mv_it_mvlog.t VALUES (1,10,100);"
run_sql "DELETE FROM mv_it_mvlog.\`\$mlog\$t\`;"

run_sql "ALTER TABLE mv_it_mvlog.t ADD COLUMN c_new INT DEFAULT 0;"
run_sql "UPDATE mv_it_mvlog.t SET untracked=101 WHERE id=1;"
assert_eq "0" "$(query_scalar "SELECT COUNT(*) FROM mv_it_mvlog.\`\$mlog\$t\`;")" "DDL + untracked update keeps mlog stable"

run_sql "INSERT INTO mv_it_mvlog.t (id, tracked, untracked) VALUES (2,20,200);"
assert_eq "1" "$(query_scalar "SELECT COUNT(*) FROM mv_it_mvlog.\`\$mlog\$t\`;")" "insert still writes mlog after DDL"

run_sql "DROP TABLE IF EXISTS mv_it_mvlog.t2;"
run_sql "DROP TABLE IF EXISTS mv_it_mvlog.\`\$mlog\$t2\`;"
run_sql "CREATE TABLE mv_it_mvlog.t2 (id INT PRIMARY KEY, tracked INT, untracked INT);"
run_sql "CREATE MATERIALIZED VIEW LOG ON mv_it_mvlog.t2 (id, tracked);"
run_sql "ALTER TABLE mv_it_mvlog.t2 DROP COLUMN tracked;"
expect_sql_error "INSERT INTO mv_it_mvlog.t2 (id, untracked) VALUES (1, 100);" "tracked-column drop leads to DML failure"

run_sql "DROP DATABASE IF EXISTS mv_it_mvlog;"
echo "[mv-it] mvlog integration tests passed"
