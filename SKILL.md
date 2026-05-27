---
name: sglang-log-analyzer
description: Use when the user asks to analyze sglang server logs, do hourly capacity/performance review, find throughput/queue/cache anomalies, or root-cause an sglang incident. Handles default, PD-disaggregation, MTP/speculative, and radix-cache modes; also parses --log-requests output. Produces hourly CSV plus a self-contained HTML report with charts.
---

# sglang log analyzer

Parses sglang server logs (default / PD-disaggregation / MTP-speculative / radix-cache; with or without `--log-requests`) and produces an **hourly CSV** plus a **self-contained HTML report** with charts and anomaly flags.

## When to use

Trigger phrases / signals:
- "analyze sglang log", "review the sglang server log", "做小时级报表"
- "why is throughput dropping at 20:00", "8-9点为什么慢"
- "compare 14:00 vs 20:00", "look at queue depth yesterday"
- A path ending in `.log` whose contents include `Prefill batch.` or `Decode batch.` lines.

Don't use for: live tailing (this is offline analysis), multi-day rollups (run per day, then diff), profiling kernel-level perf (sglang logs are scheduler-level only).

## One-shot invocation

```bash
# whole-file analysis (most common)
uv run scripts/analyze.py --log /path/to/sglang.log --out reports/

# focus on a single hour (the user's "8-9点报表" pattern)
uv run scripts/analyze.py --log /path/to/sglang.log --hour 20 --out reports/

# custom range
uv run scripts/analyze.py --log /path/to/sglang.log --range 14:30-21:15 --out reports/
```

`uv` (already on the user's PATH) reads the PEP 723 header at the top of `analyze.py` and creates an isolated venv on first run — pandas/matplotlib/jinja2 install automatically. Subsequent runs reuse the cached env.

You don't need to write parsing code. The script handles every line shape in every mode.

## What the script does

1. **Detects modes** by scanning the first ~5000 lines (PD if `#prealloc-req`/`#transfer-req` present; MTP if `accept rate` present; radix if `cache hit rate > 0` ever; log_requests if `Receive:`/`Finish:` lines present). Modes combine.
2. **Parses** prefill, decode, receive, finish, and KVTransferError lines into per-event records. Field-order changes between sglang versions don't break parsing — it tokenizes key-value pairs after the line-shape anchor regex.
3. **Buckets** by hour (or scopes to `--hour`/`--range`/`--date`), computes mean/p50/p95/p99/max for each numeric field, plus `retracted_req_delta_sum` from the monotonic counter.
4. **Renders** CSV (source of truth) and HTML (matplotlib PNGs base64-embedded — opens offline).

## Output files

```
sglang_report_<basename>_<YYYYMMDD-HHMMSS>.csv
sglang_report_<basename>_<YYYYMMDD-HHMMSS>.html
```

CSV is the source of truth — every metric, every percentile, every flag. HTML is for humans — charts, anomaly summary, per-bucket narrative for flagged buckets, and the CSV inlined as an appendix.

## CLI flags

| flag | purpose |
|------|---------|
| `--log PATH` | required; or `-` for stdin |
| `--out DIR` | output directory (default: log's parent dir) |
| `--basename NAME` | override basename for output files |
| `--hour N` | scope to hour `HH:00` of the latest date in log |
| `--range HH:MM-HH:MM` | custom window |
| `--date YYYY-MM-DD` | restrict to specific date when log spans multiple days |
| `--mode auto\|normal\|pd\|mtp\|radix` | force mode if auto-detect misfires (comma-separable) |
| `--assume-interval SECS` | synthesize timestamps if log lacks `[YYYY-MM-DD HH:MM:SS]` prefix |
| `--no-html` / `--no-csv` / `--json` | output toggles |
| `--quiet` | suppress stderr progress |

Exit codes: `0` ok, `2` no parseable lines, `3` bad args, `4` no events in requested window.

## Anomaly flags surfaced in CSV + HTML

Quick reference (full thresholds in [references/csv-schema.md](references/csv-schema.md)):

| flag | severity | rule |
|------|----------|------|
| `flag_token_pressure` | warn | `token_usage_p95 > 0.95` |
| `flag_queue_backlog` | warn | `queue_req_p95 > 2000` |
| `flag_low_accept_rate` | warn | `accept_rate_mean < 0.5` (MTP) |
| `flag_retraction_spike` | critical | `retracted_req_delta_sum >= 1` (PD) |
| `flag_cache_collapse` | warn | `cache_hit_rate_mean < 0.10` and `prefill_events > 5` |
| `flag_ttft_regression` | warn | `ttft_ms_p95 > 5000` (--log-requests) |
| `flag_throughput_drop` | critical | `gen_tps_mean < 0.5 * rolling_max(4 buckets)` |

Stored as `0`/`1` in CSV for grep-ability. HTML highlights critical in red, warn in amber.

## Decision tree for common questions

| Question | What to do |
|----------|-----------|
| "Why was throughput low at 20:00?" | `--hour 20`, open HTML, read the per-bucket narrative + check `queue_req_p95`, `token_usage_p95`, `retracted_req_delta_sum` |
| "Compare 14:00 vs 20:00" | full-file run, open CSV, diff the two rows |
| "Is the cache helping?" | check `cache_hit_rate_mean` column trend; >0.30 sustained = effective; near-zero = no prefix reuse |
| "Are PD KV transfers healthy?" | `pd` section in HTML — `transfer_req_p95` flat, `retracted_req_delta_sum` zero = healthy |
| "Is speculative decoding working?" | MTP section — `accept_rate_mean` 0.8-0.94 = healthy; pinned at 0.33 = broken draft |
| "What requests timed out?" | grep raw log for `KVTransferError`, then cross-reference `bootstrap_room` ids; HTML samples up to 5 |

## Mode-specific notes

- **Default mode**: every metric in CSV is populated.
- **PD-disaggregation**: extra columns `prealloc_req_*`, `transfer_req_*`, `inflight_req_*` (the "inflight queue"), `retracted_req_delta_sum`, `prealloc_usage_*`. KVTransferError lines surfaced in HTML narrative.
- **MTP / speculative (EAGLE / NEXTN / MTP)**: extra columns `accept_len_*`, `accept_rate_*`. A pinned 0.33 with `--speculative-num-steps 2` typically means the draft model weights are corrupted or DP attention is interfering.
- **Radix cache**: `cache_hit_rate_*` columns populated from prefill lines. With MTP enabled upstream, sglang has a known bug ([#20451](https://github.com/sgl-project/sglang/issues/20451)) that reports `cached_tokens=0` per request even when cache is hitting — `cache_hit_rate` from prefill lines is still accurate.
- **--log-requests**: per-request `ttft_ms_*`, `e2e_latency_ms_*`, `cached_tokens_per_req_mean`. Modes are not mutually exclusive — a PD+MTP+log-requests log gets all three groups of columns.

## Troubleshooting

- **Empty CSV / "no parseable lines"**: log doesn't contain `Prefill batch`/`Decode batch` lines. sglang prints these at INFO; check `--log-level` upstream.
- **Mode mis-detected**: pass `--mode pd` (or comma-separated) to force.
- **Timestamps absent**: pass `--assume-interval 1` to synthesize monotonic timestamps. Hour bucketing won't match wall-clock but relative order is preserved.
- **Some columns NaN**: expected — mode-specific columns are NaN when the mode isn't active.
- **Re-test the parser after sglang upgrade**: `bash tests/fixtures/smoke.sh` runs the full suite against the bundled fixtures and asserts each anomaly flag fires where expected.

## References

- [references/log-format.md](references/log-format.md) — every parsed line shape, every field, what each means
- [references/csv-schema.md](references/csv-schema.md) — every CSV column with units and interpretation
- [references/html-report.md](references/html-report.md) — chart catalogue, severity colors, single-window mode behavior
