#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

echo "[mv-it] test: MV service metrics on /metrics"
wait_tidb_ready 120
wait_metrics_ready 120

mv_fetch_before="$(metric_sum_or_zero "tidb_mv_service_run_event_total" 'type="fetch_mviews_ok"')"
mvlog_fetch_before="$(metric_sum_or_zero "tidb_mv_service_run_event_total" 'type="fetch_mlog_ok"')"
submit_before="$(metric_sum_or_zero "tidb_mv_service_run_event_total" 'type="task_executor_submitted"')"
mv_refresh_dur_before="$(metric_sum_or_zero "tidb_mv_service_operation_duration_seconds_count" 'type="mv_refresh"')"
mvlog_purge_dur_before="$(metric_sum_or_zero "tidb_mv_service_operation_duration_seconds_count" 'type="mvlog_purge"')"

run_sql "DROP DATABASE IF EXISTS mv_it_metrics;"
run_sql "CREATE DATABASE mv_it_metrics;"
run_sql "CREATE TABLE mv_it_metrics.t (id INT NOT NULL, v INT NOT NULL, PRIMARY KEY(id));"
run_sql "INSERT INTO mv_it_metrics.t VALUES (1, 10), (2, 20);"
run_sql "CREATE MATERIALIZED VIEW LOG ON mv_it_metrics.t (id, v) PURGE START WITH now() NEXT now();"
run_sql "CREATE MATERIALIZED VIEW mv_it_metrics.mv (id, s, cnt) REFRESH FAST NEXT now() AS SELECT id, sum(v), count(1) FROM mv_it_metrics.t GROUP BY id;"

wait_metric_ge "tidb_mv_service_task_status" 'type="mv_total"' "1" 120 1 "mv service discovers pending mv task"
wait_metric_ge "tidb_mv_service_task_status" 'type="mvlog_total"' "1" 120 1 "mv service discovers pending mvlog task"

wait_metric_increase "tidb_mv_service_run_event_total" 'type="fetch_mviews_ok"' "${mv_fetch_before}" "1" 120 1 "mv service fetches mview metadata"
wait_metric_increase "tidb_mv_service_run_event_total" 'type="fetch_mlog_ok"' "${mvlog_fetch_before}" "1" 120 1 "mv service fetches mlog metadata"
wait_metric_increase "tidb_mv_service_run_event_total" 'type="task_executor_submitted"' "${submit_before}" "1" 120 1 "mv service submits tasks to executor"
wait_metric_increase "tidb_mv_service_operation_duration_seconds_count" 'type="mv_refresh"' "${mv_refresh_dur_before}" "1" 120 1 "mv refresh task duration metric recorded"
wait_metric_increase "tidb_mv_service_operation_duration_seconds_count" 'type="mvlog_purge"' "${mvlog_purge_dur_before}" "1" 120 1 "mvlog purge task duration metric recorded"

run_sql "DROP DATABASE IF EXISTS mv_it_metrics;"
echo "[mv-it] mv service metrics test passed"
