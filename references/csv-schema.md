# CSV column dictionary

The hourly CSV (`sglang_report_<basename>_<timestamp>.csv`) is one row per hour bucket. Mode-specific columns are written for every row; absent values are blank/`NaN`. Boolean flags are stored as `0`/`1` for grep-ability.

## Bucket bookkeeping

| column | unit | meaning |
|--------|------|---------|
| `bucket_start` | ISO 8601 | bucket lower bound (inclusive), e.g. `2025-05-27T20:00:00` |
| `bucket_end` | ISO 8601 | bucket upper bound (exclusive) |
| `prefill_events` | count | prefill log lines in bucket |
| `decode_events` | count | decode log lines in bucket |
| `requests_received` | count | `Receive:` lines (only with `--log-requests` upstream) |
| `requests_finished` | count | `Finish:` lines |
| `pd_error_count` | count | KVTransferError / retraction error lines |

## Throughput

| column | unit | source field |
|--------|------|--------------|
| `input_tps_mean` / `_p50` / `_p95` / `_max` | tokens/s | prefill `input throughput (token/s)` |
| `gen_tps_mean` / `_p50` / `_p95` / `_max` | tokens/s | decode `gen throughput (token/s)` |

`gen_tps_mean` is the primary inference-speed metric. A drop relative to the rolling 4-bucket max drives `flag_throughput_drop`.

## Queue + concurrency

| column | unit | source |
|--------|------|--------|
| `queue_req_mean` / `_p50` / `_p95` / `_max` | requests | `#queue-req` (decode line is authoritative — sampled every decode interval) |
| `running_req_mean` / `_p95` / `_max` | requests | `#running-req` |
| `pending_token_mean` / `_p95` | tokens | prefill `#pending-token` |

`queue_req_p95 > 2000` drives `flag_queue_backlog`.

## Token usage (KV cache)

| column | unit | source |
|--------|------|--------|
| `token_usage_mean` / `_p50` / `_p95` / `_max` | fraction (0-1) | `token usage` |

`token_usage_p95 > 0.95` drives `flag_token_pressure`.

## Cache (radix)

| column | unit | source |
|--------|------|--------|
| `cache_hit_rate_mean` / `_p50` / `_p95` | fraction (0-1) | prefill `cache hit rate` (parsed from percent) |
| `cached_tokens_sum` | tokens | sum of prefill `#cached-token` in bucket |
| `new_tokens_sum` | tokens | sum of prefill `#new-token` in bucket |

`cache_hit_rate_mean < 0.10 and prefill_events > 5` drives `flag_cache_collapse`. The volume guard avoids firing on near-empty buckets.

## PD-only

NaN when PD mode is not detected.

| column | unit | source |
|--------|------|--------|
| `prealloc_req_mean` / `_p95` / `_max` | requests | `#prealloc-req` |
| `transfer_req_mean` / `_p95` / `_max` | requests | `#transfer-req` |
| `inflight_req_mean` / `_p95` | requests | `#inflight-req` (the "inflight queue") |
| `prealloc_usage_mean` / `_p95` | fraction | `pre-allocated usage` |
| `retracted_req_delta_sum` | retractions | sum of positive deltas in `#retracted-req` (it's a monotonic counter; this is the per-bucket *increase*) |

`retracted_req_delta_sum >= 1` drives `flag_retraction_spike` (critical).

## MTP / speculative

NaN otherwise.

| column | unit | source |
|--------|------|--------|
| `accept_len_mean` / `_p50` / `_p95` | tokens/step | decode `accept len` |
| `accept_rate_mean` / `_p50` / `_p95` | fraction | decode `accept rate` |

`accept_rate_mean < 0.5` drives `flag_low_accept_rate`. Healthy band: 0.8-0.94. A pinned 0.33 typically signals a broken draft model (with `--speculative-num-steps 2`, that's `1/(1+2)`).

## Request-level (--log-requests)

NaN unless `--log-requests` is on upstream and finish lines fired in the bucket.

| column | unit | source |
|--------|------|--------|
| `ttft_ms_p50` / `_p95` / `_p99` | ms | finish `ttft` (seconds * 1000) |
| `e2e_latency_ms_p50` / `_p95` / `_p99` | ms | finish `e2e_latency` (seconds * 1000) |
| `cached_tokens_per_req_mean` | tokens | mean of finish `cached_tokens` |
| `completion_tokens_per_req_mean` | tokens | mean of finish `completion_tokens` |

`ttft_ms_p95 > 5000` drives `flag_ttft_regression`.

## Anomaly flags

All booleans. Stored as `0`/`1`.

| column | severity | rule |
|--------|----------|------|
| `flag_token_pressure` | warn | `token_usage_p95 > 0.95` |
| `flag_queue_backlog` | warn | `queue_req_p95 > 2000` |
| `flag_low_accept_rate` | warn | MTP and `accept_rate_mean < 0.5` |
| `flag_retraction_spike` | critical | `retracted_req_delta_sum >= 1` |
| `flag_cache_collapse` | warn | radix and `cache_hit_rate_mean < 0.10` and `prefill_events > 5` |
| `flag_ttft_regression` | warn | `ttft_ms_p95 > 5000` |
| `flag_throughput_drop` | critical | `gen_tps_mean < 0.5 * rolling_max(gen_tps_mean, 4)` |

## Reading the CSV

- Sort by `gen_tps_mean` ascending to find the slowest hours.
- `awk -F, 'NR==1 || $X==1'` (substitute the column index of any `flag_*`) lists only flagged buckets.
- Diff two rows to compare windows — every column has the same units, so subtraction makes sense for any non-flag column.
- Pivot the boolean flag columns into a heatmap externally if you have many days of logs.

## Worked example

Three buckets from a hypothetical PD log:

```
bucket_start, gen_tps_mean, queue_req_p95, token_usage_p95, retracted_req_delta_sum, flag_retraction_spike, flag_throughput_drop
2025-05-27T18:00:00, 2400, 80, 0.72, 0, 0, 0
2025-05-27T19:00:00, 2380, 95, 0.74, 0, 0, 0
2025-05-27T20:00:00,  900, 1800, 0.91, 4, 1, 1
```

Reading: 20:00 gen_tps collapses to 38% of the rolling max (`flag_throughput_drop`), KV transfers to decode are failing (`retracted_req_delta_sum=4` -> `flag_retraction_spike`), and queue is filling toward backlog (1800 of 2000 threshold). Open the HTML, jump to the per-bucket narrative for 20:00 — it lists the verbatim `KVTransferError` lines so you can match `bootstrap_room` ids back to specific requests.
