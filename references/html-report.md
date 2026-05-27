# HTML report guide

The HTML report is a single self-contained file: matplotlib PNGs are base64-embedded, no CDN, opens offline. Generated alongside the CSV at `sglang_report_<basename>_<timestamp>.html`.

Sections render top-to-bottom in this order. Mode-conditional sections are silently dropped when the mode isn't active.

## 1. Header

Source filename, time range covered, bucket count, total events parsed, parse error count, and one badge per detected mode (`normal`, `pd`, `mtp`, `radix`, `log_requests`). The "single-window mode" badge appears when `--hour` or `--range` was used.

## 2. Anomaly summary

A table of every (bucket, flag) pair where a flag fired, with severity and the threshold expression. Empty state: "No anomalies detected. All buckets clean."

This is the section to scan first — if it's empty, the rest of the report is a routine sanity check.

## 3. Throughput over time

Three lines on the same axes:
- `input_tps_mean` solid (prefill rate)
- `gen_tps_mean` solid in a second color (decode rate — the headline metric)
- `gen_tps_p95` dashed (tail behavior)

What it answers: "Is decode keeping up?" — gen_tps trending down while input_tps holds up means decode is slowing. Both falling together usually means request volume dropped.

## 4. Queue depth over time

`queue_req_mean` solid + `queue_req_p95` dashed. Red horizontal line at 2000 (the `flag_queue_backlog` threshold).

What it answers: "Are requests piling up?" — queue rising while gen_tps holds means the cluster is saturated. Queue rising *and* gen_tps falling usually correlates with `flag_token_pressure` or `flag_retraction_spike`.

## 5. KV cache token usage over time

`token_usage_mean` solid + `token_usage_p95` dashed. Red line at 0.95 (`flag_token_pressure` threshold).

What it answers: "Is the KV cache near OOM?" — `token_usage_p95` consistently > 0.95 means the scheduler will start retracting requests soon (in PD mode) or rejecting new ones.

## 6. Radix cache (radix mode only)

`cache_hit_rate_mean` line on the left axis with `prefill_events` as a faint bar overlay on the right axis.

The bar overlay matters: a "collapse" in cache_hit_rate during a low-volume bucket may just be sampling noise, not a real regression. The `flag_cache_collapse` rule guards on `prefill_events > 5` for the same reason.

What it answers: "Is the cache helping?" — sustained > 0.30 during repetitive workloads (system prompts, RAG with repeated retrieval) is healthy. Near-zero is fine for diverse first-time prompts.

## 7. PD disaggregation (PD mode only)

Two charts:
- `transfer_req_p95` + `prealloc_req_p95` + `inflight_req_p95` lines — the three sides of the PD pipeline
- `retracted_req_delta_sum` bars — per-bucket retraction count

What they answer: "Is the prefill->decode KV transfer healthy?" — `transfer_req_p95` rising = network bottleneck, `prealloc_req_p95` rising while `transfer_req_p95` is flat = decode lacks KV memory, retraction bars > 0 = clients disconnecting or KV transfer timing out.

## 8. Speculative decoding (MTP mode only)

Two charts:
- `accept_rate_mean` + `accept_rate_p50` lines, red line at 0.5 (`flag_low_accept_rate` threshold)
- `accept_len_mean` line

What they answer: "Is speculation paying off?" — accept_rate < 0.5 sustained means the draft model is misaligned, weights are corrupted, or DP attention is interfering. accept_len in 2.0-2.7 with `--speculative-num-steps 2` is excellent. accept_len = 1.0 means no draft tokens are being accepted, just baseline decoding (and you're paying speculation overhead for nothing).

## 9. Request-level latency (--log-requests only)

Side-by-side:
- TTFT p50/p95/p99 lines, red threshold at 5000 ms (`flag_ttft_regression`)
- e2e_latency p50/p95/p99 lines

What they answer: "How is the user-facing experience?" — TTFT regressions usually correlate to prefill bottlenecks (queue backlog or cache collapse). e2e regressions with stable TTFT mean decode is slowing.

## 10. Per-bucket narrative

For every bucket where ≥1 flag fired, a card listing:
- Bucket time
- All flags that fired (severity-colored)
- Top contextual metrics (`token_usage_p95`, `queue_req_p95`, `gen_tps_mean`, `cache_hit_rate_mean`, `accept_rate_mean`, `retracted_req_delta_sum`, `ttft_ms_p95`)

When PD errors fired anywhere in the analyzed window, a "PD error / retraction log samples" section follows the narratives with up to 5 verbatim error lines. Match the `bootstrap_room=` id to specific failed requests.

## 11. Appendix

The full CSV inlined as a scrollable HTML table — useful for screenshots or copy-pasting into incident reports.

Footer carries skill version + generated-at timestamp.

## Single-window mode (`--hour N` / `--range`)

When you scope to one hour or one custom range, time-series charts are dropped (only one bucket — a line of one point isn't useful) and replaced by per-event scatter charts within the window:
- `gen_tps` scatter at decode-line granularity (one dot per decode log line)
- `token_usage` scatter same
- `ttft_ms` and `e2e_latency_ms` histograms (when request-level data is present)

The narrative section becomes the main content — this layout is optimized for "explain what happened during this one hour" investigations.

## Severity colors

- **warn** (amber): something to watch; the threshold has been crossed but the system may still be operating acceptably.
- **critical** (red): customer-impacting or system-stability issue; investigate immediately.

`flag_retraction_spike` and `flag_throughput_drop` are critical. Everything else is warn.

## What charts can't show you

- Per-request traces — for that, scope to `--log-requests` and look at the appendix CSV's request-level columns, or grep the raw log for a specific `rid`.
- GPU utilization — sglang only logs `fwd occupancy` when device timer is enabled. If your `prefill_tflops`/`decode_tflops` columns are NaN, the upstream server wasn't started with `--enable-mfu-metrics`.
- Per-tensor-parallel-rank breakdown — `tp_rank` is captured per event but the report aggregates across ranks. If you need per-rank, filter the JSON output (`--json` flag) by `tp_rank`.
