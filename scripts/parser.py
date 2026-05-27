"""sglang log parser: regex anchor + key-value tokenizer + mode auto-detect."""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Iterator

PREFIX_RE = re.compile(
    r'^\[(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)'
    r'(?:\s+TP(?P<tp>\d+))?\]\s*'
)
PREFILL_RE = re.compile(r'Prefill batch\.\s*(?P<fields>.+?)\s*$')
DECODE_RE = re.compile(r'Decode batch\.\s*(?P<fields>.+?)\s*$')
RECEIVE_RE = re.compile(
    r"Receive(?:\sOpenAI)?:\s*obj=(?P<obj>.+?)(?:,\s*headers=.*)?\s*$"
)
FINISH_RE = re.compile(r"Finish:.*?out=(?P<out>\{.+?\})\s*(?:,\s*headers=.*)?$")
PD_ERROR_RE = re.compile(
    r'(KVTransferError|bootstrap (?:failed|timeout)|transfer (?:aborted|failed)'
    r'|retract(?:ed)? sequence|timed out after .* in KVPoll)',
    re.IGNORECASE,
)

KEY_MAP = {
    '#new-seq': 'new_seq',
    '#new-token': 'new_token',
    '#cached-token': 'cached_token',
    'cache hit rate': 'cache_hit_rate',
    'token usage': 'token_usage',
    '#running-req': 'running_req',
    '#queue-req': 'queue_req',
    '#pending-token': 'pending_token',
    'cuda graph': 'cuda_graph',
    'input throughput (token/s)': 'input_tps',
    'gen throughput (token/s)': 'gen_tps',
    'accept len': 'accept_len',
    'accept rate': 'accept_rate',
    'pre-allocated usage': 'prealloc_usage',
    '#prealloc-req': 'prealloc_req',
    '#transfer-req': 'transfer_req',
    '#retracted-req': 'retracted_req',
    '#inflight-req': 'inflight_req',
    '#bootstrap-req': 'bootstrap_req',
    'est. prefill TFLOPS/s (per GPU)': 'prefill_tflops',
    'est. decode TFLOPS/s (per GPU)': 'decode_tflops',
    'fwd occupancy': 'fwd_occupancy',
}

_BOOL_KEYS = {'cuda_graph'}
_PERCENT_KEYS = {'cache_hit_rate'}


@dataclass
class ParseStats:
    total_lines: int = 0
    matched: int = 0
    parse_errors: int = 0
    modes_seen: set[str] = field(default_factory=set)


def _coerce(key: str, raw: str):
    if key in _BOOL_KEYS:
        return raw.strip().lower() == 'true'
    s = raw.strip()
    if s.endswith('%'):
        s = s[:-1]
        try:
            return float(s) / 100.0
        except ValueError:
            return None
    if key in _PERCENT_KEYS:
        try:
            v = float(s)
        except ValueError:
            return None
        return v / 100.0 if v > 1.5 else v
    try:
        if '.' in s or 'e' in s.lower():
            return float(s)
        return int(s)
    except ValueError:
        return s


def tokenize_fields(blob: str) -> dict:
    out: dict = {}
    for chunk in blob.split(', '):
        if ':' not in chunk:
            continue
        k, _, v = chunk.partition(':')
        k = k.strip()
        canonical = KEY_MAP.get(k, k.replace(' ', '_').replace('#', '').replace('(', '').replace(')', '').replace('/', '_'))
        out[canonical] = _coerce(canonical, v)
    return out


def _parse_ts(raw: str) -> datetime | None:
    raw = raw.replace('T', ' ').replace(',', '.')
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def parse_line(line: str, fallback_ts: datetime | None = None) -> dict | None:
    line = line.rstrip('\n')
    if not line:
        return None
    m = PREFIX_RE.match(line)
    ts = None
    tp_rank = None
    body = line
    if m:
        ts = _parse_ts(m.group('ts'))
        tp = m.group('tp')
        tp_rank = int(tp) if tp is not None else None
        body = line[m.end():]
    if ts is None:
        ts = fallback_ts

    pm = PREFILL_RE.search(body)
    if pm:
        fields = tokenize_fields(pm.group('fields'))
        return {'ts': ts, 'tp_rank': tp_rank, 'event_type': 'prefill', **fields}

    dm = DECODE_RE.search(body)
    if dm:
        fields = tokenize_fields(dm.group('fields'))
        return {'ts': ts, 'tp_rank': tp_rank, 'event_type': 'decode', **fields}

    fm = FINISH_RE.search(body)
    if fm:
        try:
            out = ast.literal_eval(fm.group('out'))
            if isinstance(out, dict):
                normalized = {
                    'ts': ts,
                    'tp_rank': tp_rank,
                    'event_type': 'finish',
                    'rid': out.get('rid'),
                    'e2e_latency_ms': _num(out.get('e2e_latency')) * 1000 if out.get('e2e_latency') is not None else None,
                    'ttft_ms': _num(out.get('ttft')) * 1000 if out.get('ttft') is not None else None,
                    'cached_tokens': _num(out.get('cached_tokens')),
                    'completion_tokens': _num(out.get('completion_tokens')),
                    'prompt_tokens': _num(out.get('prompt_tokens')),
                }
                return normalized
        except (ValueError, SyntaxError):
            return {'ts': ts, 'tp_rank': tp_rank, 'event_type': 'parse_error', 'raw': line[:200]}

    rm = RECEIVE_RE.search(body)
    if rm:
        rid_match = re.search(r"rid=['\"]?([\w\-:]+)['\"]?", rm.group('obj'))
        return {
            'ts': ts,
            'tp_rank': tp_rank,
            'event_type': 'receive',
            'rid': rid_match.group(1) if rid_match else None,
        }

    if PD_ERROR_RE.search(body):
        return {
            'ts': ts,
            'tp_rank': tp_rank,
            'event_type': 'pd_error',
            'raw': line[:300],
        }

    return None


def _num(v):
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def detect_modes(events: Iterable[dict]) -> set[str]:
    modes: set[str] = set()
    for ev in events:
        et = ev.get('event_type')
        if et == 'prefill' or et == 'decode':
            modes.add('normal')
            if any(k in ev for k in ('prealloc_req', 'transfer_req', 'retracted_req', 'inflight_req', 'prealloc_usage')):
                modes.add('pd')
            if 'accept_rate' in ev or 'accept_len' in ev:
                modes.add('mtp')
            chr_ = ev.get('cache_hit_rate')
            if isinstance(chr_, (int, float)) and chr_ > 0:
                modes.add('radix')
        elif et in ('receive', 'finish'):
            modes.add('log_requests')
    return modes


def parse_file(
    path: str | Path,
    assume_interval: float | None = None,
) -> tuple[list[dict], ParseStats]:
    p = Path(path) if path != '-' else None
    stats = ParseStats()
    events: list[dict] = []
    last_ts: datetime | None = None
    synthetic_ts = datetime(1970, 1, 1)

    if p is None:
        import sys
        source: Iterator[str] = iter(sys.stdin)
    else:
        source = iter(p.read_text(errors='replace').splitlines())

    for raw in source:
        stats.total_lines += 1
        ev = parse_line(raw, fallback_ts=last_ts)
        if ev is None:
            continue
        if ev.get('event_type') == 'parse_error':
            stats.parse_errors += 1
            continue
        if ev.get('ts') is None:
            if assume_interval is not None:
                ev['ts'] = synthetic_ts
                synthetic_ts = synthetic_ts + timedelta(seconds=assume_interval)
            else:
                continue
        else:
            last_ts = ev['ts']
        stats.matched += 1
        events.append(ev)

    stats.modes_seen = detect_modes(events)
    return events, stats


if __name__ == '__main__':
    import json
    import sys

    if len(sys.argv) < 2:
        print('usage: parser.py <log>', file=sys.stderr)
        sys.exit(2)
    events, stats = parse_file(sys.argv[1])
    print(f'# matched={stats.matched} total={stats.total_lines} errors={stats.parse_errors} modes={sorted(stats.modes_seen)}', file=sys.stderr)
    for ev in events[:20]:
        ev2 = {**ev, 'ts': ev['ts'].isoformat() if ev.get('ts') else None}
        print(json.dumps(ev2, default=str))
