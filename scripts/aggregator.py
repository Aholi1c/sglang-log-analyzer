"""sglang log aggregator: events -> hourly DataFrame with percentiles + anomaly flags."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

import numpy as np
import pandas as pd

NUMERIC_PERCENTILE_COLS = {
    'input_tps': ('mean', 'p50', 'p95', 'max'),
    'gen_tps': ('mean', 'p50', 'p95', 'max'),
    'queue_req': ('mean', 'p50', 'p95', 'max'),
    'running_req': ('mean', 'p95', 'max'),
    'pending_token': ('mean', 'p95'),
    'token_usage': ('mean', 'p50', 'p95', 'max'),
    'cache_hit_rate': ('mean', 'p50', 'p95'),
    'prealloc_req': ('mean', 'p95', 'max'),
    'transfer_req': ('mean', 'p95', 'max'),
    'inflight_req': ('mean', 'p95'),
    'prealloc_usage': ('mean', 'p95'),
    'accept_len': ('mean', 'p50', 'p95'),
    'accept_rate': ('mean', 'p50', 'p95'),
}

REQUEST_PERCENTILE_COLS = {
    'ttft_ms': ('p50', 'p95', 'p99'),
    'e2e_latency_ms': ('p50', 'p95', 'p99'),
}

PERCENTILE_Q = {'p50': 0.50, 'p95': 0.95, 'p99': 0.99}


def _agg_one(series: pd.Series, stats: Iterable[str]) -> dict:
    out = {}
    s = series.dropna() if hasattr(series, 'dropna') else series
    if len(s) == 0:
        return {st: np.nan for st in stats}
    for st in stats:
        if st == 'mean':
            out[st] = float(s.mean())
        elif st == 'max':
            out[st] = float(s.max())
        elif st in PERCENTILE_Q:
            out[st] = float(s.quantile(PERCENTILE_Q[st]))
    return out


def _retracted_delta(group: pd.DataFrame) -> float:
    s = group['retracted_req'].dropna()
    if len(s) < 1:
        return 0.0
    if len(s) == 1:
        return 0.0
    diffs = s.diff().dropna()
    return float(diffs[diffs > 0].sum())


def aggregate(events: list[dict], freq: str = '1h') -> pd.DataFrame:
    if not events:
        return pd.DataFrame()

    df = pd.DataFrame(events)
    df = df[df['ts'].notna()].copy()
    df['ts'] = pd.to_datetime(df['ts'])
    df.set_index('ts', inplace=True)
    df.sort_index(inplace=True)

    scheduler_df = df[df['event_type'].isin(['prefill', 'decode'])].copy()
    request_df = df[df['event_type'] == 'finish'].copy()
    receive_df = df[df['event_type'] == 'receive'].copy()
    pd_error_df = df[df['event_type'] == 'pd_error'].copy()

    grouper = pd.Grouper(freq=freq, origin='epoch')

    rows = []
    if not scheduler_df.empty:
        bucket_groups = scheduler_df.groupby(grouper)
    else:
        bucket_groups = []

    all_buckets = set()
    if not scheduler_df.empty:
        all_buckets.update(scheduler_df.groupby(grouper).groups.keys())
    if not request_df.empty:
        all_buckets.update(request_df.groupby(grouper).groups.keys())
    if not receive_df.empty:
        all_buckets.update(receive_df.groupby(grouper).groups.keys())
    if not pd_error_df.empty:
        all_buckets.update(pd_error_df.groupby(grouper).groups.keys())

    for bucket_start in sorted(all_buckets):
        if pd.isna(bucket_start):
            continue
        bucket_end = bucket_start + _freq_to_timedelta(freq)
        row: dict = {'bucket_start': bucket_start, 'bucket_end': bucket_end}

        sched_bucket = scheduler_df.loc[(scheduler_df.index >= bucket_start) & (scheduler_df.index < bucket_end)]
        prefill_bucket = sched_bucket[sched_bucket['event_type'] == 'prefill']
        decode_bucket = sched_bucket[sched_bucket['event_type'] == 'decode']
        req_bucket = request_df.loc[(request_df.index >= bucket_start) & (request_df.index < bucket_end)]
        recv_bucket = receive_df.loc[(receive_df.index >= bucket_start) & (receive_df.index < bucket_end)]
        err_bucket = pd_error_df.loc[(pd_error_df.index >= bucket_start) & (pd_error_df.index < bucket_end)]

        row['prefill_events'] = len(prefill_bucket)
        row['decode_events'] = len(decode_bucket)
        row['requests_received'] = len(recv_bucket)
        row['requests_finished'] = len(req_bucket)
        row['pd_error_count'] = len(err_bucket)

        for col, stats in NUMERIC_PERCENTILE_COLS.items():
            if col not in sched_bucket.columns:
                for st in stats:
                    row[f'{col}_{st}'] = np.nan
                continue
            source = sched_bucket[col] if col in ('queue_req', 'running_req', 'token_usage') else None
            if col == 'input_tps':
                source = prefill_bucket['input_tps'] if 'input_tps' in prefill_bucket.columns else pd.Series(dtype=float)
            elif col == 'gen_tps':
                source = decode_bucket['gen_tps'] if 'gen_tps' in decode_bucket.columns else pd.Series(dtype=float)
            elif col == 'pending_token':
                source = prefill_bucket['pending_token'] if 'pending_token' in prefill_bucket.columns else pd.Series(dtype=float)
            elif col == 'cache_hit_rate':
                source = prefill_bucket['cache_hit_rate'] if 'cache_hit_rate' in prefill_bucket.columns else pd.Series(dtype=float)
            elif col in ('prealloc_req', 'transfer_req', 'inflight_req', 'prealloc_usage', 'accept_len', 'accept_rate'):
                source = sched_bucket[col]
            elif source is None:
                source = sched_bucket[col]
            agg = _agg_one(source, stats)
            for st, val in agg.items():
                row[f'{col}_{st}'] = val

        row['cached_tokens_sum'] = float(prefill_bucket['cached_token'].sum()) if 'cached_token' in prefill_bucket.columns else 0.0
        row['new_tokens_sum'] = float(prefill_bucket['new_token'].sum()) if 'new_token' in prefill_bucket.columns else 0.0
        row['retracted_req_delta_sum'] = _retracted_delta(sched_bucket) if 'retracted_req' in sched_bucket.columns else 0.0

        for col, stats in REQUEST_PERCENTILE_COLS.items():
            if col in req_bucket.columns and len(req_bucket) > 0:
                agg = _agg_one(req_bucket[col], stats)
                for st, val in agg.items():
                    row[f'{col}_{st}'] = val
            else:
                for st in stats:
                    row[f'{col}_{st}'] = np.nan
        if len(req_bucket) > 0 and 'cached_tokens' in req_bucket.columns:
            row['cached_tokens_per_req_mean'] = float(req_bucket['cached_tokens'].mean())
            row['completion_tokens_per_req_mean'] = float(req_bucket['completion_tokens'].mean()) if 'completion_tokens' in req_bucket.columns else np.nan
        else:
            row['cached_tokens_per_req_mean'] = np.nan
            row['completion_tokens_per_req_mean'] = np.nan

        rows.append(row)

    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result = result.sort_values('bucket_start').reset_index(drop=True)
    result = _add_anomaly_flags(result)
    return result


def _freq_to_timedelta(freq: str) -> timedelta:
    f = freq.lower().strip()
    if f.endswith('h'):
        return timedelta(hours=int(f[:-1]))
    if f.endswith('min') or f.endswith('m'):
        n = f.rstrip('min').rstrip('m')
        return timedelta(minutes=int(n))
    if f.endswith('s'):
        return timedelta(seconds=int(f[:-1]))
    return timedelta(hours=1)


def _add_anomaly_flags(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['flag_token_pressure'] = (df.get('token_usage_p95', pd.Series(dtype=float)) > 0.95).astype(int)
    df['flag_queue_backlog'] = (df.get('queue_req_p95', pd.Series(dtype=float)) > 2000).astype(int)

    if 'accept_rate_mean' in df.columns:
        df['flag_low_accept_rate'] = (df['accept_rate_mean'] < 0.5).fillna(False).astype(int)
    else:
        df['flag_low_accept_rate'] = 0

    df['flag_retraction_spike'] = (df.get('retracted_req_delta_sum', 0) >= 1).astype(int)

    if 'cache_hit_rate_mean' in df.columns:
        df['flag_cache_collapse'] = (
            (df['cache_hit_rate_mean'] < 0.10)
            & (df.get('prefill_events', 0) > 5)
        ).fillna(False).astype(int)
    else:
        df['flag_cache_collapse'] = 0

    if 'ttft_ms_p95' in df.columns:
        df['flag_ttft_regression'] = (df['ttft_ms_p95'] > 5000).fillna(False).astype(int)
    else:
        df['flag_ttft_regression'] = 0

    if 'gen_tps_mean' in df.columns:
        rolling = df['gen_tps_mean'].rolling(window=4, min_periods=1).max()
        df['flag_throughput_drop'] = (
            (df['gen_tps_mean'] < 0.5 * rolling) & (rolling > 0)
        ).fillna(False).astype(int)
    else:
        df['flag_throughput_drop'] = 0

    return df


def filter_window(events: list[dict], hour: int | None, range_str: str | None, date: str | None) -> list[dict]:
    if hour is None and range_str is None and date is None:
        return events

    target_date: datetime | None = None
    if date is not None:
        target_date = datetime.strptime(date, '%Y-%m-%d').date()
    elif events:
        dates = sorted({e['ts'].date() for e in events if e.get('ts') is not None})
        if dates:
            target_date = dates[-1]

    if hour is not None:
        if target_date is None:
            raise ValueError('no parseable dates in events for --hour')
        start = datetime.combine(target_date, datetime.min.time()).replace(hour=hour)
        end = start + timedelta(hours=1)
    elif range_str is not None:
        s_str, e_str = range_str.split('-')
        sh, sm = map(int, s_str.split(':'))
        eh, em = map(int, e_str.split(':'))
        if target_date is None:
            raise ValueError('no parseable dates in events for --range')
        start = datetime.combine(target_date, datetime.min.time()).replace(hour=sh, minute=sm)
        end = datetime.combine(target_date, datetime.min.time()).replace(hour=eh, minute=em)
        if end <= start:
            end = end + timedelta(days=1)
    else:
        start = datetime.combine(target_date, datetime.min.time())
        end = start + timedelta(days=1)

    return [e for e in events if e.get('ts') is not None and start <= e['ts'] < end]
