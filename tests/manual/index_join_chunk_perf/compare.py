#!/usr/bin/env python3

import argparse
import json
import sys


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def nested(data, keys, default=0.0):
    cur = data
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def delta(before, after):
    if before == 0:
        return 0.0
    return (after - before) / before * 100.0


def print_row(name, before, after, unit=""):
    print(f"{name:32s} before={before:12.3f}{unit} after={after:12.3f}{unit} delta={delta(before, after):8.2f}%")


def metric_range(data, name):
    samples = data.get("metric_samples", [])
    vals = [sample[name] for sample in samples if name in sample]
    if not vals:
        return 0.0
    return max(vals) - min(vals)


def elapsed(data):
    samples = data.get("metric_samples", [])
    ts = [sample["ts"] for sample in samples if "ts" in sample]
    if len(ts) >= 2:
        return max(ts) - min(ts)
    return nested(data, ("workload", "elapsed_sec"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("before")
    parser.add_argument("after")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--heap-ratio", type=float, default=0.75)
    parser.add_argument("--rss-ratio", type=float, default=0.85)
    parser.add_argument("--cpu-ratio", type=float, default=1.05)
    parser.add_argument("--p95-ratio", type=float, default=1.05)
    args = parser.parse_args()

    before = load(args.before)
    after = load(args.after)

    before_heap = nested(before, ("metrics", "go_memstats_heap_inuse_bytes", "max"))
    after_heap = nested(after, ("metrics", "go_memstats_heap_inuse_bytes", "max"))
    before_heap_range = metric_range(before, "go_memstats_heap_inuse_bytes")
    after_heap_range = metric_range(after, "go_memstats_heap_inuse_bytes")
    before_rss = nested(before, ("metrics", "process_resident_memory_bytes", "max"))
    after_rss = nested(after, ("metrics", "process_resident_memory_bytes", "max"))
    before_rss_range = metric_range(before, "process_resident_memory_bytes")
    after_rss_range = metric_range(after, "process_resident_memory_bytes")
    before_cpu_seconds = metric_range(before, "process_cpu_seconds_total")
    after_cpu_seconds = metric_range(after, "process_cpu_seconds_total")
    before_elapsed = elapsed(before)
    after_elapsed = elapsed(after)
    before_cpu_cores = before_cpu_seconds / before_elapsed if before_elapsed else 0.0
    after_cpu_cores = after_cpu_seconds / after_elapsed if after_elapsed else 0.0
    before_p95 = nested(before, ("workload", "latency_ms", "p95"))
    after_p95 = nested(after, ("workload", "latency_ms", "p95"))
    before_p99 = nested(before, ("workload", "latency_ms", "p99"))
    after_p99 = nested(after, ("workload", "latency_ms", "p99"))
    before_qps = nested(before, ("workload", "qps"))
    after_qps = nested(after, ("workload", "qps"))

    print_row("peak_heap_inuse", before_heap / 1024 / 1024, after_heap / 1024 / 1024, " MiB")
    print_row("heap_inuse_range", before_heap_range / 1024 / 1024, after_heap_range / 1024 / 1024, " MiB")
    print_row("peak_rss", before_rss / 1024 / 1024, after_rss / 1024 / 1024, " MiB")
    print_row("rss_range", before_rss_range / 1024 / 1024, after_rss_range / 1024 / 1024, " MiB")
    print_row("cpu_seconds", before_cpu_seconds, after_cpu_seconds, " s")
    print_row("avg_cpu_cores", before_cpu_cores, after_cpu_cores)
    print_row("p95_latency", before_p95, after_p95, " ms")
    print_row("p99_latency", before_p99, after_p99, " ms")
    print_row("qps", before_qps, after_qps)

    after_errors = nested(after, ("workload", "error_count"))
    failures = []
    if after_errors != 0:
        failures.append(f"after error_count={after_errors}")
    if before_heap and after_heap > before_heap * args.heap_ratio:
        failures.append(f"heap ratio {after_heap / before_heap:.3f} > {args.heap_ratio}")
    if before_rss and after_rss > before_rss * args.rss_ratio:
        failures.append(f"rss ratio {after_rss / before_rss:.3f} > {args.rss_ratio}")
    if before_cpu_cores and after_cpu_cores > before_cpu_cores * args.cpu_ratio:
        failures.append(f"cpu cores ratio {after_cpu_cores / before_cpu_cores:.3f} > {args.cpu_ratio}")
    if before_p95 and after_p95 > before_p95 * args.p95_ratio:
        failures.append(f"p95 ratio {after_p95 / before_p95:.3f} > {args.p95_ratio}")

    if failures:
        print("\nThreshold warnings:")
        for failure in failures:
            print(f"  - {failure}")
        if args.strict:
            sys.exit(1)


if __name__ == "__main__":
    main()
