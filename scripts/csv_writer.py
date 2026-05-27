"""CSV writer: ordered, documented column layout for the hourly report."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

COLUMN_ORDER = [
    'bucket_start', 'bucket_end',
    'prefill_events', 'decode_events', 'requests_received', 'requests_finished', 'pd_error_count',
    'input_tps_mean', 'input_tps_p50', 'input_tps_p95', 'input_tps_max',
    'gen_tps_mean', 'gen_tps_p50', 'gen_tps_p95', 'gen_tps_max',
    'queue_req_mean', 'queue_req_p50', 'queue_req_p95', 'queue_req_max',
    'running_req_mean', 'running_req_p95', 'running_req_max',
    'pending_token_mean', 'pending_token_p95',
    'token_usage_mean', 'token_usage_p50', 'token_usage_p95', 'token_usage_max',
    'cache_hit_rate_mean', 'cache_hit_rate_p50', 'cache_hit_rate_p95',
    'cached_tokens_sum', 'new_tokens_sum',
    'prealloc_req_mean', 'prealloc_req_p95', 'prealloc_req_max',
    'transfer_req_mean', 'transfer_req_p95', 'transfer_req_max',
    'inflight_req_mean', 'inflight_req_p95',
    'prealloc_usage_mean', 'prealloc_usage_p95',
    'retracted_req_delta_sum',
    'accept_len_mean', 'accept_len_p50', 'accept_len_p95',
    'accept_rate_mean', 'accept_rate_p50', 'accept_rate_p95',
    'ttft_ms_p50', 'ttft_ms_p95', 'ttft_ms_p99',
    'e2e_latency_ms_p50', 'e2e_latency_ms_p95', 'e2e_latency_ms_p99',
    'cached_tokens_per_req_mean', 'completion_tokens_per_req_mean',
    'flag_token_pressure', 'flag_queue_backlog', 'flag_low_accept_rate',
    'flag_retraction_spike', 'flag_cache_collapse', 'flag_ttft_regression',
    'flag_throughput_drop',
]


def write_csv(df: pd.DataFrame, path: str | Path) -> Path:
    out = Path(path)
    ordered = [c for c in COLUMN_ORDER if c in df.columns]
    extras = [c for c in df.columns if c not in COLUMN_ORDER]
    df = df[ordered + extras].copy()

    for col in ('bucket_start', 'bucket_end'):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col]).dt.strftime('%Y-%m-%dT%H:%M:%S')

    df.to_csv(out, index=False, float_format='%.4f')
    return out
