# sglang log line formats

Reference for every line shape this skill parses. The parser is two-stage (anchor regex + key-value tokenizer) so field-order changes between sglang versions don't break it — the tables below describe what each field *means*, not the order it appears in. The parser accepts both `Prefill batch.` / `Decode batch.` and newer `Prefill batch,` / `Decode batch,` punctuation, plus optional forward-iteration ids such as `Prefill batch [123],`.

Common log prefix: `[YYYY-MM-DD HH:MM:SS TPn]`. The `TPn` tensor-parallel rank is optional; when present, the parser captures it into a `tp_rank` column but does not use it for hour bucketing. Lines without a timestamp inherit the previous parsed timestamp; if they appear before the first timestamp, they are assigned to the first following timestamp bucket. Use `--assume-interval` only for logs with no usable timestamps at all.

## Prefill batch line

```
[2025-05-27 20:14:33 TP0] Prefill batch. #new-seq: 4, #new-token: 1024, #cached-token: 256, cache hit rate: 25.00%, token usage: 0.41, #running-req: 12, #queue-req: 5, #pending-token: 1024, cuda graph: True, input throughput (token/s): 18722.30
[2025-05-27 20:14:33] Prefill batch, #new-seq: 4, #new-token: 1024, #cached-token: 256, token usage: 0.41, #running-req: 12, #queue-req: 5, cuda graph: True, input throughput (token/s): 18722.30
[2025-05-27 20:14:33] Prefill batch [123], #new-seq: 4, #new-token: 1024, token usage: 0.41, #running-req: 12, #queue-req: 5, input throughput (token/s): 18722.30
```

Source: `python/sglang/srt/managers/scheduler_components/metrics_reporter.py::report_prefill_stats`. Fires on each prefill batch when stats logging is on.

| Field | Type | Mode | Meaning |
|------|------|------|---------|
| `#new-seq` | int | all | Sequences admitted in this prefill batch |
| `#new-token` | int | all | New tokens processed (bounded by `--chunked-prefill-size`, default 8192) |
| `#cached-token` | int | all | Tokens served from RadixCache (prefix cache hits) |
| `cache hit rate` | percent | all | `#cached-token / (#new-token + #cached-token)`. Parsed to a 0-1 fraction |
| `token usage` | fraction | all | KV cache pool utilization 0-1. >0.95 risks OOM |
| `#running-req` | int | all | Sequences currently in execution (have reserved KV) |
| `#queue-req` | int | all | Waiting queue size — what the user means by "queue" |
| `#pending-token` | int | all | Tokens waiting in queue across all pending requests |
| `cuda graph` | bool | all | Whether CUDA graphs were used for this batch |
| `input throughput (token/s)` | float | all | Prefill token rate |
| `#prealloc-req` | int | PD | Requests with KV space allocated, awaiting prefill forward |
| `#inflight-req` | int | PD | Requests with forward pass in progress / KV transfer queued. **This is the "inflight queue".** |
| `#bootstrap-req` | int | PD | Requests in bootstrap phase (sometimes printed) |
| `est. prefill TFLOPS/s (per GPU)` | float | `--enable-mfu-metrics` | Prefill FLOPs utilization |
| `fwd occupancy` | percent | with device timer | Forward pass GPU occupancy |

## Decode batch line

```
[2025-05-27 20:14:38 TP0] Decode batch. #running-req: 18, #token: 16384, token usage: 0.71, accept len: 2.40, accept rate: 0.84, pre-allocated usage: 0.28, #prealloc-req: 5, #transfer-req: 3, #retracted-req: 0, cuda graph: True, gen throughput (token/s): 2520.18, #queue-req: 3
[2025-05-27 20:14:38] Decode batch, #running-req: 18, #token: 16384, token usage: 0.71, cuda graph: True, gen throughput (token/s): 2520.18, #queue-req: 3
[2025-05-27 20:14:38] Decode batch [124], #running-req: 18, #token: 16384, token usage: 0.71, cuda graph: True, gen throughput (token/s): 2520.18, #queue-req: 3
```

Source: same `metrics_reporter.py::report_decode_stats`. Fires every `--decode-log-interval` decode iterations (default 40).

| Field | Type | Mode | Meaning |
|------|------|------|---------|
| `#running-req` | int | all | Sequences currently decoding |
| `#token` | int | all | Total tokens in KV from all running sequences |
| `token usage` | fraction | all | KV pool utilization (same as prefill) |
| `cuda graph` | bool | all | CUDA graphs in use |
| `gen throughput (token/s)` | float | all | Decode generation rate — **primary inference-speed metric** |
| `#queue-req` | int | all | Waiting queue (decode line is the authoritative source — sampled every interval) |
| `accept len` | float | MTP / speculative | Avg draft tokens accepted per step. 1.0 = baseline (no draft accepted), 2.5 = excellent |
| `accept rate` | fraction | MTP / speculative | Proposed-token acceptance rate. 0.8-0.94 healthy; 0.33 pinned = broken draft model |
| `pre-allocated usage` | fraction | PD (decode side) | KV cache pre-allocated for incoming requests from prefill |
| `#prealloc-req` | int | PD (decode) | Requests in prealloc phase (allocated but data not yet transferred) |
| `#transfer-req` | int | PD (decode) | Requests with active KV transfer (RDMA/network in flight) |
| `#retracted-req` | int | PD (decode) | **Monotonic counter** of retracted requests (client disconnect / timeout). The skill computes per-bucket positive deltas, not raw value |

## Receive (--log-requests)

```
[2025-05-27 20:00:05 TP0] Receive: obj=TokenizedGenerateReqInput(rid='req-006', input_ids=[1, 2], sampling_params={'temperature': 0.7})
```

Source: `python/sglang/srt/utils/request_logger.py::log_received_request`. Fires once when tokenizer receives a request. Field set varies by `--log-requests-level`:
- level 0: `rid`, `input_len`, `output_len`
- level 1: + `sampling_params`
- level 2 (default): + truncated text + truncated input/output ids
- level 3: nothing truncated (high disk I/O — only for deep debug)

This skill extracts only `rid` from receive lines (used to count `requests_received` per bucket).

There is also `Receive OpenAI: obj=...` for /v1/chat/completions and similar OpenAI endpoints — same parser handles it.

## Finish (--log-requests)

```
[2025-05-27 20:00:14 TP0] Finish: obj=..., out={'rid': 'req-006', 'prompt_tokens': 320, 'completion_tokens': 220, 'cached_tokens': 80, 'e2e_latency': 8.512, 'ttft': 5.521}
[2025-05-27 20:00:14] Finish: obj=..., out={'meta_info': {'id': 'req-006', 'prompt_tokens': 320, 'completion_tokens': 220, 'cached_tokens': 80, 'e2e_latency': 8.512}}
```

Source: `python/sglang/srt/utils/request_logger.py::log_finished_request`. Fires once per request when generation completes or aborts. Parsed via `ast.literal_eval` on the `out=` dict.

| Field | Type | Meaning |
|------|------|---------|
| `rid` | str | Request id, matches the receive line |
| `prompt_tokens` | int | Input length |
| `completion_tokens` | int | Generated length |
| `cached_tokens` | int | Tokens served from cache for this request |
| `e2e_latency` | seconds | Receive -> last-token latency. Skill stores as `e2e_latency_ms` |
| `ttft` | seconds | Receive -> first-generated-token. Skill stores as `ttft_ms` |

In newer SGLang logs, these fields can be nested under `out['meta_info']`; the skill reads both the flat form and the nested `meta_info` form. `ttft_ms` is left blank when upstream does not emit `ttft`.

## PD error / retraction lines

The skill also captures rare-but-important lines for anomaly correlation:

```
KVTransferError(bootstrap_room=12345): Request rid=abc-123 timed out after 300s in KVPoll.WaitingForInput
KVTransferError(bootstrap_room=12346): Failed to get kvcache from prefill instance, it might be dead
```

Detector: case-insensitive match against `KVTransferError | bootstrap (failed|timeout) | transfer (aborted|failed) | retract(ed)? sequence | timed out after .* in KVPoll`. The matched line is stored verbatim and surfaced in the per-bucket narrative + a "PD error / retraction log samples" section in HTML when present. They count toward `pd_error_count` per bucket but do not directly drive flags — `flag_retraction_spike` is driven by the `#retracted-req` counter delta in decode lines.

## Mode auto-detection

The skill walks events once to set mode flags. Modes are not mutually exclusive.

| Mode | Detector |
|------|----------|
| `normal` | any `Prefill batch` or `Decode batch` line |
| `pd` | any line contains a `#prealloc-req`, `#transfer-req`, `#retracted-req`, `#inflight-req`, or `pre-allocated usage` field |
| `mtp` | any decode line contains `accept len` or `accept rate` |
| `radix` | any prefill line has `cache hit rate > 0` |
| `log_requests` | any `Receive:` or `Finish:` line |

Override with `--mode pd,mtp` if auto-detect misfires (rare; mostly useful when feeding only a partial log slice that omits the detector lines).
