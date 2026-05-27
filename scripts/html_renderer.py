"""HTML renderer: matplotlib PNGs -> base64 -> jinja2 -> self-contained HTML."""
from __future__ import annotations

import base64
import io
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

FLAG_DEFS = [
    ('flag_token_pressure', 'Token pressure (KV cache near OOM)', 'warn', 'token_usage_p95 > 0.95'),
    ('flag_queue_backlog', 'Queue backlog (requests piling up)', 'warn', 'queue_req_p95 > 2000'),
    ('flag_low_accept_rate', 'Low speculative accept rate', 'warn', 'accept_rate_mean < 0.5'),
    ('flag_retraction_spike', 'Retracted requests (PD instability)', 'critical', 'retracted_req_delta_sum >= 1'),
    ('flag_cache_collapse', 'Cache hit rate collapse', 'warn', 'cache_hit_rate_mean < 0.10 and prefill_events > 5'),
    ('flag_ttft_regression', 'TTFT regression', 'warn', 'ttft_ms_p95 > 5000'),
    ('flag_throughput_drop', 'Throughput drop', 'critical', 'gen_tps_mean < 0.5 * rolling_max(4)'),
]


def _png_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=110, bbox_inches='tight')
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode('ascii')


def _line_chart(df: pd.DataFrame, cols: list[tuple[str, str, str]], title: str, ylabel: str, hline: float | None = None, hline_label: str | None = None) -> str | None:
    available = [(col, label, style) for col, label, style in cols if col in df.columns and df[col].notna().any()]
    if not available:
        return None
    fig, ax = plt.subplots(figsize=(10, 3.5))
    x = pd.to_datetime(df['bucket_start'])
    for col, label, style in available:
        ax.plot(x, df[col], style, label=label, linewidth=1.6)
    if hline is not None:
        ax.axhline(hline, color='red', linestyle=':', linewidth=1, label=hline_label or f'threshold {hline}')
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)
    ax.legend(loc='best', fontsize=8)
    fig.autofmt_xdate()
    return _png_b64(fig)


def _bar_chart(df: pd.DataFrame, col: str, title: str, ylabel: str, color: str = 'tomato') -> str | None:
    if col not in df.columns or not df[col].notna().any():
        return None
    fig, ax = plt.subplots(figsize=(10, 3.0))
    x = pd.to_datetime(df['bucket_start'])
    ax.bar(x, df[col], color=color, width=0.03)
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3, axis='y')
    fig.autofmt_xdate()
    return _png_b64(fig)


def _cache_chart(df: pd.DataFrame) -> str | None:
    if 'cache_hit_rate_mean' not in df.columns:
        return None
    fig, ax1 = plt.subplots(figsize=(10, 3.5))
    x = pd.to_datetime(df['bucket_start'])
    ax1.plot(x, df['cache_hit_rate_mean'], '-', color='tab:blue', label='cache_hit_rate_mean', linewidth=1.8)
    ax1.set_ylabel('cache hit rate (fraction)', color='tab:blue')
    ax1.set_ylim(0, 1)
    ax1.grid(True, alpha=0.3)
    if 'prefill_events' in df.columns:
        ax2 = ax1.twinx()
        ax2.bar(x, df['prefill_events'], alpha=0.25, color='tab:orange', width=0.03, label='prefill_events')
        ax2.set_ylabel('prefill events / bucket', color='tab:orange')
    ax1.set_title('Cache hit rate over time (radix)')
    fig.autofmt_xdate()
    return _png_b64(fig)


def _scatter_chart(events: list[dict], event_type: str, field: str, title: str, ylabel: str) -> str | None:
    points = [(e['ts'], e[field]) for e in events if e.get('event_type') == event_type and e.get(field) is not None and e.get('ts') is not None]
    if not points:
        return None
    fig, ax = plt.subplots(figsize=(10, 3.5))
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    ax.scatter(xs, ys, s=14, alpha=0.7)
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    return _png_b64(fig)


def _histogram(events: list[dict], field: str, title: str, xlabel: str) -> str | None:
    values = [e[field] for e in events if e.get(field) is not None]
    if not values:
        return None
    fig, ax = plt.subplots(figsize=(8, 3.0))
    ax.hist(values, bins=20, color='steelblue', alpha=0.85)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel('count')
    ax.grid(True, alpha=0.3, axis='y')
    return _png_b64(fig)


def render_report(
    df: pd.DataFrame,
    events: list[dict],
    modes: set[str],
    log_path: str,
    out_path: str | Path,
    template_dir: str | Path,
    parse_errors: int = 0,
    single_bucket: bool = False,
    skill_version: str = '0.1.0',
    pd_error_samples: list[str] | None = None,
) -> Path:
    pd_error_samples = pd_error_samples or []
    charts: dict[str, str | None] = {}

    if not single_bucket and len(df) > 0:
        charts['throughput'] = _line_chart(
            df,
            [
                ('input_tps_mean', 'input throughput mean', '-'),
                ('gen_tps_mean', 'gen throughput mean', '-'),
                ('gen_tps_p95', 'gen throughput p95', '--'),
            ],
            'Throughput over time (tokens/s)', 'tokens/s',
        )
        charts['queue'] = _line_chart(
            df,
            [('queue_req_mean', 'queue mean', '-'), ('queue_req_p95', 'queue p95', '--')],
            'Queue depth over time', 'requests',
            hline=2000, hline_label='backlog threshold 2000',
        )
        charts['token_usage'] = _line_chart(
            df,
            [('token_usage_mean', 'token_usage mean', '-'), ('token_usage_p95', 'token_usage p95', '--')],
            'KV cache token usage over time', 'fraction (0-1)',
            hline=0.95, hline_label='pressure threshold 0.95',
        )
        if 'radix' in modes:
            charts['cache'] = _cache_chart(df)
        if 'pd' in modes:
            charts['pd_transfer'] = _line_chart(
                df,
                [('transfer_req_p95', 'transfer_req p95', '-'), ('prealloc_req_p95', 'prealloc_req p95', '--'), ('inflight_req_p95', 'inflight_req p95', ':')],
                'PD transfer/prealloc/inflight over time', 'requests',
            )
            charts['pd_retracted'] = _bar_chart(df, 'retracted_req_delta_sum', 'Retracted-request count per bucket (PD)', 'retractions', color='tomato')
        if 'mtp' in modes:
            charts['mtp_accept'] = _line_chart(
                df,
                [('accept_rate_mean', 'accept_rate mean', '-'), ('accept_rate_p50', 'accept_rate p50', '--')],
                'Speculative accept rate over time', 'fraction (0-1)',
                hline=0.5, hline_label='healthy floor 0.5',
            )
            charts['mtp_acceptlen'] = _line_chart(
                df,
                [('accept_len_mean', 'accept_len mean', '-')],
                'Speculative accept length over time', 'draft tokens accepted / step',
            )
        if 'log_requests' in modes:
            charts['ttft'] = _line_chart(
                df,
                [('ttft_ms_p50', 'ttft p50', '-'), ('ttft_ms_p95', 'ttft p95', '--'), ('ttft_ms_p99', 'ttft p99', ':')],
                'TTFT over time (ms)', 'ms',
                hline=5000, hline_label='regression threshold 5000 ms',
            )
            charts['e2e'] = _line_chart(
                df,
                [('e2e_latency_ms_p50', 'e2e p50', '-'), ('e2e_latency_ms_p95', 'e2e p95', '--'), ('e2e_latency_ms_p99', 'e2e p99', ':')],
                'End-to-end latency over time (ms)', 'ms',
            )
    else:
        charts['gen_tps_scatter'] = _scatter_chart(events, 'decode', 'gen_tps', 'Decode-line gen throughput (token/s)', 'tokens/s')
        charts['token_usage_scatter'] = _scatter_chart(events, 'decode', 'token_usage', 'Decode-line token usage', 'fraction (0-1)')
        if 'log_requests' in modes:
            charts['ttft_hist'] = _histogram(events, 'ttft_ms', 'TTFT histogram (ms)', 'ttft (ms)')
            charts['e2e_hist'] = _histogram(events, 'e2e_latency_ms', 'End-to-end latency histogram (ms)', 'e2e_latency (ms)')

    anomaly_rows = []
    if not df.empty:
        for _, r in df.iterrows():
            fired = [(name, label, sev, expr) for name, label, sev, expr in FLAG_DEFS if r.get(name, 0) == 1]
            if fired:
                anomaly_rows.append({
                    'bucket_start': pd.to_datetime(r['bucket_start']).strftime('%Y-%m-%d %H:%M'),
                    'flags': fired,
                    'metrics': {
                        'token_usage_p95': r.get('token_usage_p95'),
                        'queue_req_p95': r.get('queue_req_p95'),
                        'gen_tps_mean': r.get('gen_tps_mean'),
                        'cache_hit_rate_mean': r.get('cache_hit_rate_mean'),
                        'accept_rate_mean': r.get('accept_rate_mean'),
                        'retracted_req_delta_sum': r.get('retracted_req_delta_sum'),
                        'ttft_ms_p95': r.get('ttft_ms_p95'),
                    },
                })

    env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=select_autoescape(['html']))
    env.filters['fnum'] = lambda v: '—' if v is None or (isinstance(v, float) and (v != v)) else (f'{v:.3f}' if isinstance(v, float) else str(v))
    tmpl = env.get_template('report.html.j2')

    csv_rows = df.fillna('').astype(str).to_dict(orient='records') if not df.empty else []
    csv_columns = list(df.columns) if not df.empty else []

    html = tmpl.render(
        log_path=str(log_path),
        modes=sorted(modes),
        events_total=len(events),
        parse_errors=parse_errors,
        bucket_count=len(df),
        time_range=(
            f"{pd.to_datetime(df['bucket_start'].min()).strftime('%Y-%m-%d %H:%M')} -> {pd.to_datetime(df['bucket_end'].max()).strftime('%Y-%m-%d %H:%M')}"
            if not df.empty else '(no buckets)'
        ),
        single_bucket=single_bucket,
        charts=charts,
        anomaly_rows=anomaly_rows,
        flag_defs=FLAG_DEFS,
        pd_error_samples=pd_error_samples[:5],
        csv_columns=csv_columns,
        csv_rows=csv_rows,
        skill_version=skill_version,
        generated_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    )

    out = Path(out_path)
    out.write_text(html, encoding='utf-8')
    return out
