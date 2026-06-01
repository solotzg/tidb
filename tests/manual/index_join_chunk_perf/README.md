# Index Join Chunk Capacity Perf Harness

This is a manual before/after perf harness for the index lookup join chunk
initial capacity issue. It must run against a real TiDB server, not mockstore.

The workload is based on the slow SQL shapes from `debug`, `debug2`, and
`debug3`: `dh_yuebao_info`, `dh_active_promote_details`, and
`dh_active_details` joined with `dh_account_basic`.

`dh_account_basic` is modeled from
`tidb_schema_by_table_1779953802.json`, including the captured business columns
and lookup indexes used by the index join. The `select` workload still reads
only `a.username` from `dh_account_basic` so that the projected inner side stays
aligned with the debug SQL shape.
`dh_yuebao_info` uses a schema modeled after the table metadata captured in
`debug`, including the original business column names, decimal/datetime/string
columns, and the `(useridx, site_code)` lookup index.
`dh_active_promote_details` is modeled from
`tidb_schema_by_table_1779955007.json`, including its original business columns
and lookup indexes. `dh_active_details` is modeled from
`tidb_schema_by_table_1779955064.json`, including its original business columns
and lookup indexes.

## Prerequisites

```bash
pip3 install pymysql
```

Start a real TiDB cluster, for example 1 PD + 1 TiKV + 1 TiDB. Run the before
and after tidb-server binaries against the same PD/TiKV data.

## Prepare And Run

```bash
cd /Users/solotzg/Work/tidb

ROWS=200000 \
CONCURRENCY=200 \
DURATION=180 \
WARMUP=30 \
IN_LIST=256 \
HIT_RATIO=0.45 \
HIT_PATTERN=contiguous \
SPLIT_TABLE_REGIONS=0 \
SPLIT_INDEX_REGIONS=0 \
TARGET_QPS=0 \
OUT=before.json \
HEAP_PREFIX=before \
tests/manual/index_join_chunk_perf/run.sh
```

Restart only TiDB with the patched binary, then run without reloading data:

```bash
PREPARE=0 \
ROWS=200000 \
CONCURRENCY=200 \
DURATION=180 \
WARMUP=30 \
IN_LIST=256 \
HIT_RATIO=0.45 \
HIT_PATTERN=contiguous \
SPLIT_TABLE_REGIONS=0 \
SPLIT_INDEX_REGIONS=0 \
TARGET_QPS=0 \
OUT=after.json \
HEAP_PREFIX=after \
tests/manual/index_join_chunk_perf/run.sh
```

Compare:

```bash
python3 tests/manual/index_join_chunk_perf/compare.py before.json after.json
```

Use `--strict` if the script should exit non-zero when thresholds are not met.

Use `TARGET_QPS` to compare before/after under the same offered load. `0` means
unlimited closed-loop load. For fixed-load update testing, keep `CONCURRENCY`
high enough to reach the target and set the same `TARGET_QPS` in both runs:

```bash
MODE=update TABLES=dh_yuebao_info PREPARE=0 ROWS=200000 CONCURRENCY=50 \
  DURATION=120 WARMUP=20 IN_LIST=200 HIT_RATIO=0.45 TARGET_QPS=100 OUT=update.json \
  tests/manual/index_join_chunk_perf/run.sh
```

`HIT_RATIO` controls how many values in each `IN (...)` list refer to existing
rows. The default `0.45` makes `IN_LIST=200` produce about 90 matching rows,
which is close to the `debug` plan's `actRows` of 79 to 97.

Use `HIT_PATTERN=spread` with region splitting when the goal is to make the
storage-side cop task distribution closer to a production slow log. `spread`
distributes matching and missing `useridx` values across the whole generated key
range instead of using one contiguous range. `SPLIT_TABLE_REGIONS=N` splits the
primary table data, while `SPLIT_INDEX_REGIONS=N` splits the relevant
`(useridx, site_code)` lookup indexes after loading data:

```bash
MODE=update TABLES=dh_yuebao_info PREPARE=1 USERNAME_STATE=matched \
  ROWS=200000 CONCURRENCY=50 DURATION=120 WARMUP=20 IN_LIST=200 \
  HIT_RATIO=0.45 HIT_PATTERN=spread SPLIT_TABLE_REGIONS=24 \
  SPLIT_INDEX_REGIONS=3 TARGET_QPS=100 \
  OUT=join-only-before.json tests/manual/index_join_chunk_perf/run.sh
```

After the data and regions are prepared, keep `PREPARE=0` for before/after TiDB
binary runs. Region split is only performed during `PREPARE=1`.

Use `USERNAME_STATE=matched` when the goal is to exercise the join lookup path
without persistent row changes. It sets fact-table usernames to the matching
`dh_account_basic` value, so `b.username != a.username` is false and the update
affects 0 rows. Prepare the data in matched state first:

```bash
MODE=update TABLES=dh_yuebao_info PREPARE=1 USERNAME_STATE=matched \
  ROWS=200000 CONCURRENCY=50 DURATION=120 WARMUP=20 IN_LIST=200 \
  HIT_RATIO=0.45 TARGET_QPS=100 OUT=update-join-only-before.json \
  tests/manual/index_join_chunk_perf/run.sh
```

If the data already exists, reset it to matched state outside the measured run:

```bash
PREPARE=0 RESET_ONLY=1 TABLES=dh_yuebao_info USERNAME_STATE=matched \
  tests/manual/index_join_chunk_perf/run.sh
```

Then run without per-iteration reset writes:

```bash
MODE=update TABLES=dh_yuebao_info PREPARE=0 USERNAME_STATE=matched \
  ROWS=200000 CONCURRENCY=50 DURATION=120 WARMUP=20 IN_LIST=200 \
  HIT_RATIO=0.45 TARGET_QPS=100 OUT=update-join-only.json \
  tests/manual/index_join_chunk_perf/run.sh
```

Reset all fact-table usernames without rebuilding schema or reloading rows:

```bash
PREPARE=0 RESET_ONLY=1 tests/manual/index_join_chunk_perf/run.sh
```

Run only one captured SQL shape:

```bash
MODE=update TABLES=dh_yuebao_info PREPARE=0 tests/manual/index_join_chunk_perf/run.sh
MODE=update TABLES=dh_active_promote_details PREPARE=0 tests/manual/index_join_chunk_perf/run.sh
MODE=update TABLES=dh_active_details PREPARE=0 tests/manual/index_join_chunk_perf/run.sh
```

## Smaller Local Run

```bash
ROWS=50000 CONCURRENCY=50 DURATION=60 WARMUP=10 OUT=result.json \
  tests/manual/index_join_chunk_perf/run.sh
```

## Metrics

The script samples these metrics from TiDB status port `/metrics` once per
second:

- `process_cpu_seconds_total`
- `process_resident_memory_bytes`
- `go_memstats_heap_inuse_bytes`
- `go_memstats_heap_alloc_bytes`
- `go_memstats_heap_objects`

The primary validation is lower TiDB heap/RSS under the same concurrency, while
CPU usage and p95 latency do not regress materially, and the plan remains
`IndexHashJoin`. `compare.py` reports CPU seconds and estimated average CPU
cores from the `process_cpu_seconds_total` delta across the measured sample
window.
The join-table and fact-table schemas are now modeled after the captured
production table shapes.

The default workload mode is `select` because the original update slow logs were
dominated by lock wait and retry. `MODE=update` uses the captured business shape:
`a.useridx IN (...)`, `a.site_code = ?`, and `LIMIT 2000`. It is useful for SQL
shape reproduction, while `select` remains the cleaner primary memory perf
signal.
