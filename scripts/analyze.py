#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pandas>=2.0",
#   "matplotlib>=3.7",
#   "jinja2>=3.1",
# ]
# ///
"""sglang-log-analyzer CLI.

Parses an sglang server log, buckets by hour, emits CSV + self-contained HTML.

Usage:
  uv run scripts/analyze.py --log path/to/server.log --out reports/
  uv run scripts/analyze.py --log server.log --hour 20 --out reports/
  uv run scripts/analyze.py --log server.log --range 14:30-21:15 --out reports/
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from parser import parse_file, detect_modes  # noqa: E402
from aggregator import aggregate, filter_window  # noqa: E402
from csv_writer import write_csv  # noqa: E402
from html_renderer import render_report  # noqa: E402

SKILL_VERSION = '0.1.0'


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='analyze', description='Analyze an sglang server log.')
    p.add_argument('--log', required=True, help='Path to log file or "-" for stdin')
    p.add_argument('--out', default=None, help='Output directory (default: log dir)')
    p.add_argument('--basename', default=None, help='Override basename for output files')
    p.add_argument('--hour', type=int, default=None, help='Single hour to analyze (HH, 0-23, local time)')
    p.add_argument('--range', dest='range_', default=None, help='Time range HH:MM-HH:MM')
    p.add_argument('--date', default=None, help='Specific date YYYY-MM-DD when log spans multiple days')
    p.add_argument('--mode', default='auto', help='auto|normal|pd|mtp|radix (comma-separable to force)')
    p.add_argument('--assume-interval', type=float, default=None, help='Synthesize timestamps at fixed seconds-interval if log has no [YYYY-MM-DD HH:MM:SS] prefix')
    p.add_argument('--no-html', action='store_true', help='Skip HTML output')
    p.add_argument('--no-csv', action='store_true', help='Skip CSV output')
    p.add_argument('--json', dest='emit_json', action='store_true', help='Also emit JSON summary')
    p.add_argument('--quiet', action='store_true', help='Suppress progress messages')
    return p


def _info(msg: str, quiet: bool):
    if not quiet:
        print(msg, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)

    if args.log != '-' and not Path(args.log).exists():
        print(f'error: log file not found: {args.log}', file=sys.stderr)
        return 3

    log_path = args.log
    basename = args.basename or (Path(log_path).stem if log_path != '-' else 'stdin')
    out_dir = Path(args.out) if args.out else (Path(log_path).parent if log_path != '-' else Path.cwd())
    out_dir.mkdir(parents=True, exist_ok=True)

    _info(f'[analyze] parsing {log_path} ...', args.quiet)
    events, stats = parse_file(log_path, assume_interval=args.assume_interval)
    _info(f'[analyze] parsed: matched={stats.matched} total={stats.total_lines} errors={stats.parse_errors}', args.quiet)

    if not events:
        print('error: no parseable sglang log lines found', file=sys.stderr)
        return 2

    if args.mode == 'auto':
        modes = stats.modes_seen
    else:
        modes = {m.strip() for m in args.mode.split(',') if m.strip()}
        modes |= stats.modes_seen & {'log_requests'}
    _info(f'[analyze] modes: {sorted(modes)}', args.quiet)

    single_bucket = args.hour is not None or args.range_ is not None
    try:
        events_in_window = filter_window(events, args.hour, args.range_, args.date)
    except ValueError as e:
        print(f'error: {e}', file=sys.stderr)
        return 3

    if not events_in_window:
        print('error: no events in requested window', file=sys.stderr)
        return 4

    freq = '1h'
    df = aggregate(events_in_window, freq=freq)

    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    csv_path = out_dir / f'sglang_report_{basename}_{timestamp}.csv'
    html_path = out_dir / f'sglang_report_{basename}_{timestamp}.html'

    if not args.no_csv:
        write_csv(df, csv_path)
        _info(f'[analyze] wrote CSV: {csv_path}', args.quiet)

    if not args.no_html:
        pd_error_samples = [e.get('raw', '') for e in events_in_window if e.get('event_type') == 'pd_error']
        render_report(
            df,
            events_in_window,
            modes,
            log_path=log_path,
            out_path=html_path,
            template_dir=SCRIPT_DIR / 'templates',
            parse_errors=stats.parse_errors,
            single_bucket=single_bucket,
            skill_version=SKILL_VERSION,
            pd_error_samples=pd_error_samples,
        )
        _info(f'[analyze] wrote HTML: {html_path}', args.quiet)

    if args.emit_json:
        json_path = out_dir / f'sglang_report_{basename}_{timestamp}.json'
        summary = {
            'log_path': str(log_path),
            'modes': sorted(modes),
            'events_total': len(events_in_window),
            'parse_errors': stats.parse_errors,
            'bucket_count': len(df),
            'buckets': json.loads(df.to_json(orient='records', date_format='iso')),
        }
        json_path.write_text(json.dumps(summary, indent=2))
        _info(f'[analyze] wrote JSON: {json_path}', args.quiet)

    return 0


if __name__ == '__main__':
    sys.exit(main())
