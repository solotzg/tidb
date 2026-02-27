# MV Integration Scripts

This directory contains runnable scripts for MV/MVLOG integration tests.

## Deployment Command

The deployment command is fixed to:

```bash
tiup playground v8.5.4 --tiflash=0 --db.binpath=/Users/solotzg/Work/tidb/bin/tidb-server
```

## External Access

All test scripts connect by default with:

```bash
mysql --host 127.0.0.1 --port 4000 -u root
```

You can override connection parameters with environment variables:

- `MYSQL_HOST` (default `127.0.0.1`)
- `MYSQL_PORT` (default `4000`)
- `MYSQL_USER` (default `root`)
- `MYSQL_PASSWORD` (default empty)

Metrics endpoint used by MV service assertions:

- `METRICS_HOST` (default `127.0.0.1`)
- `METRICS_PORT` (default `10080`)

## Scripts

- `playground_up.sh`: starts TiUP playground in background and waits for TiDB readiness.
- `playground_down.sh`: stops playground process group started by `playground_up.sh`.
- `run_mview_tests.sh`: covers MV create/refresh/drop behavior, explicit-transaction rejection, and incremental refresh (`FAST` / `WITH SYNC MODE FAST`) path checks.
- `run_mvlog_tests.sh`: covers MVLOG create, DML/DDL tracking behavior, explicit-transaction rejection for purge, and `PURGE MATERIALIZED VIEW LOG` execution/schedule/safe-boundary metadata.
- `run_mvservice_metrics_tests.sh`: validates MV service behavior via `http://127.0.0.1:10080/metrics`.
- `run_mv_tests.sh`: aggregate entry that runs MV, MVLOG, and MV service metrics suites.
- `run_all.sh`: one-command entry. Start playground, run tests, then cleanup.

## Usage

Managed mode (start/stop playground automatically):

```bash
tests/mv_integration/run_all.sh
```

External mode (you started playground manually):

```bash
MV_IT_DEPLOY=external tests/mv_integration/run_all.sh
```
