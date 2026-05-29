# sglang-log-analyzer

OpenClaw skill and standalone CLI for analyzing SGLang server logs.

It parses dense SGLang scheduler logs into an hourly CSV and a self-contained HTML report, so capacity review and incident analysis can start from concrete numbers instead of ad hoc grep.

## What it does

- Parses `Prefill batch`, `Decode batch`, `Receive`, `Finish`, and `KVTransferError` log lines.
- Supports both older `Prefill batch.` / `Decode batch.` lines and newer `Prefill batch,` / `Decode batch,` lines, including optional forward-iteration ids such as `Prefill batch [123],`.
- Reads `--log-requests` finish metrics from either flat `out={...}` fields or newer nested `out['meta_info']` fields.
- Detects normal, PD-disaggregation, MTP/speculative decoding, radix-cache, and `--log-requests` modes.
- Buckets events by hour, or by a requested single hour / time range.
- Generates a CSV with throughput, queue depth, KV usage, cache hit rate, latency, and anomaly flags.
- Generates an offline HTML report with embedded charts and anomaly summaries.
- Runs with `uv` and PEP 723 inline dependencies, so no project-level `pip install` is required.

## Supported log modes

| Mode | Detection signal | Extra fields |
|------|------------------|--------------|
| Normal | `Prefill batch` / `Decode batch` | throughput, queue depth, running requests, token usage |
| PD-disaggregation | `#prealloc-req` or `#transfer-req` | prealloc, transfer, inflight, retracted requests, `KVTransferError` |
| MTP / speculative decoding | `accept rate` in decode lines | `accept_len`, `accept_rate` |
| Radix cache | non-zero `cache hit rate` | cache hit rate, cached tokens, new tokens |
| `--log-requests` | `Receive:` and `Finish:` lines | request latency, TTFT, cached tokens, completion tokens |

Modes can combine. For example, a PD + MTP + `--log-requests` log produces all related metric groups.

## Repository layout

```text
sglang-log-analyzer/
├── SKILL.md
├── README.md
├── references/
│   ├── csv-schema.md
│   ├── html-report.md
│   └── log-format.md
├── scripts/
│   ├── analyze.py
│   ├── aggregator.py
│   ├── csv_writer.py
│   ├── html_renderer.py
│   ├── parser.py
│   └── templates/
│       └── report.html.j2
└── tests/
    └── fixtures/
        ├── log_requests.log
        ├── mtp.log
        ├── normal.log
        ├── pd.log
        ├── radix.log
        ├── real_meta.log
        └── smoke.sh
```

The skill entry point is [`SKILL.md`](SKILL.md). The executable analyzer is [`scripts/analyze.py`](scripts/analyze.py). Detailed schemas and report behavior live in [`references/`](references/).

## Requirements

- Python 3.10+
- [`uv`](https://docs.astral.sh/uv/)
- A SGLang server log file

Install `uv` if needed:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Quick start

Clone the repository:

```bash
git clone https://github.com/Aholi1c/sglang-log-analyzer.git
cd sglang-log-analyzer
```

Run the analyzer on a log file:

```bash
uv run scripts/analyze.py --log /path/to/sglang.log --out reports/
```

Analyze a single hour:

```bash
uv run scripts/analyze.py --log /path/to/sglang.log --hour 20 --out reports/
```

Validate with the bundled fixtures:

```bash
bash tests/fixtures/smoke.sh
```

The first `uv run` may download `pandas`, `matplotlib`, and `jinja2`. Later runs reuse the cached environment.

## Install as an OpenClaw skill

Clone the repository, then symlink the whole repository into your OpenClaw skill directory:

```bash
git clone https://github.com/Aholi1c/sglang-log-analyzer.git ~/skills/sglang-log-analyzer
mkdir -p ~/.openclaw/plugin-skills
ln -s ~/skills/sglang-log-analyzer ~/.openclaw/plugin-skills/sglang-log-analyzer
```

Check that OpenClaw can see the skill:

```bash
head ~/.openclaw/plugin-skills/sglang-log-analyzer/SKILL.md
```

After installation, prompts like these should activate the skill:

- `分析这个 sglang 日志`
- `做一个 8-9 点的小时报表`
- `why did throughput drop at 20:00`
- a `.log` file path whose content includes SGLang scheduler lines

## Standalone CLI usage

The analyzer can also be used without OpenClaw:

```bash
uv run scripts/analyze.py --log PATH [options]
```

### Common commands

```bash
# Whole-file hourly report
uv run scripts/analyze.py --log sglang.log --out reports/

# Single hour, using the latest matching date in the log
uv run scripts/analyze.py --log sglang.log --hour 20 --out reports/

# Custom time window
uv run scripts/analyze.py --log sglang.log --range 14:30-21:15 --out reports/

# Log without timestamps, assuming each line is 1 second apart
uv run scripts/analyze.py --log sglang.log --assume-interval 1 --out reports/

# Read from stdin
cat sglang-a.log sglang-b.log | uv run scripts/analyze.py --log - --basename merged --out reports/
```

### CLI options

| Option | Description |
|--------|-------------|
| `--log PATH` | Required. Log file path, or `-` for stdin. |
| `--out DIR` | Output directory. Defaults to the log file's parent directory. |
| `--basename NAME` | Override the output file basename. |
| `--hour N` | Analyze one hour, from `HH:00` to `HH+1:00`. |
| `--range HH:MM-HH:MM` | Analyze a custom time window. |
| `--date YYYY-MM-DD` | Restrict analysis to one date when the log spans multiple days. |
| `--mode auto\|normal\|pd\|mtp\|radix` | Auto-detect or force one or more comma-separated modes. |
| `--assume-interval SECS` | Synthesize timestamps if the log has no timestamp prefix. |
| `--no-html` | Skip HTML generation. |
| `--no-csv` | Skip CSV generation. |
| `--json` | Also write a JSON summary. |
| `--quiet` | Suppress progress logs. |

Exit codes:

| Code | Meaning |
|------|---------|
| `0` | Success |
| `2` | No parseable SGLang log lines |
| `3` | Bad arguments |
| `4` | No events in the requested time window |

## Output files

Each run writes timestamped files:

```text
reports/
├── sglang_report_<basename>_<YYYYMMDD-HHMMSS>.csv
└── sglang_report_<basename>_<YYYYMMDD-HHMMSS>.html
```

The CSV is the source of truth. The HTML report is for visual review and can be opened offline because charts are embedded as base64 images.

See [`references/csv-schema.md`](references/csv-schema.md) for the full CSV schema and [`references/html-report.md`](references/html-report.md) for report layout details.

## Anomaly flags

| Flag | Severity | Rule |
|------|----------|------|
| `flag_token_pressure` | warn | `token_usage_p95 > 0.95` |
| `flag_queue_backlog` | warn | `queue_req_p95 > 2000` |
| `flag_low_accept_rate` | warn | MTP mode and `accept_rate_mean < 0.5` |
| `flag_retraction_spike` | critical | `retracted_req_delta_sum >= 1` |
| `flag_cache_collapse` | warn | radix mode, `cache_hit_rate_mean < 0.10`, and `prefill_events > 5` |
| `flag_ttft_regression` | warn | `ttft_ms_p95 > 5000` |
| `flag_throughput_drop` | critical | `gen_tps_mean < 0.5 * rolling_max(gen_tps_mean, 4)` |

`retracted_req_delta_sum` is computed from the monotonic SGLang `#retracted-req` counter. The analyzer stores the per-bucket delta, not the raw absolute counter value.

## Reading the report

1. Start with the anomaly summary near the top of the HTML report.
2. If a bucket is flagged, inspect the per-bucket narrative for the driver metrics.
3. Use the throughput, queue, token usage, cache, PD, MTP, and request-latency charts to confirm the trend.
4. Use the CSV for exact comparisons, spreadsheets, or post-processing.

For a single-hour report, the HTML switches from multi-hour trend charts to focused scatter and distribution views where useful.

## Examples

### Why was 20:00 slow?

```bash
uv run scripts/analyze.py --log /var/log/sglang.log --hour 20 --out reports/
```

Check these columns first:

- `gen_tps_mean`
- `queue_req_p95`
- `token_usage_p95`
- `retracted_req_delta_sum`
- `ttft_ms_p95`
- all `flag_*` columns

### Compare 14:00 and 20:00

```bash
uv run scripts/analyze.py --log sglang.log --out reports/
```

Open the generated CSV and compare the rows whose `bucket_start` values contain `14:00` and `20:00`.

### Check speculative decoding health

Look at `accept_rate_mean`:

| Value | Interpretation |
|-------|----------------|
| `0.80` to `0.94` | Healthy |
| `0.50` to `0.80` | Usable but not ideal |
| `< 0.50` | Triggers `flag_low_accept_rate` |
| stable around `0.33` | Often indicates a broken or mismatched draft model |

### Check prefix-cache effectiveness

Look at `cache_hit_rate_mean`:

| Value | Interpretation |
|-------|----------------|
| `> 0.30` | Cache is likely useful |
| near `0` with high `prefill_events` | Prefix cache may be ineffective |

Known SGLang issue: when MTP is enabled, `--log-requests` may emit per-request `cached_tokens=0` ([sgl-project/sglang#20451](https://github.com/sgl-project/sglang/issues/20451)). The prefill-line `cache_hit_rate` is still the preferred signal.

## Troubleshooting

| Symptom | What to check |
|---------|---------------|
| `uv: command not found` | Install `uv`, then open a new shell. |
| `no parseable sglang log lines found` | Confirm the log contains `Prefill batch`, `Decode batch`, `Receive`, or `Finish` lines. |
| The wrong mode was detected | Use `--mode pd`, `--mode mtp`, or a comma-separated value such as `--mode pd,mtp`. |
| The log has no timestamps | Use `--assume-interval SECS`. |
| Some CSV columns are empty | This is expected when the related mode is not active. |
| HTML charts do not render | Try a modern Chromium-based browser, or use the CSV with `--no-html`. |
| Real logs fail but fixtures pass | Compare the real log format with [`references/log-format.md`](references/log-format.md). SGLang logger output may have changed. |

## Development

Run the smoke test before publishing changes:

```bash
bash tests/fixtures/smoke.sh
```

Expected result:

```text
all assertions passed
```

The smoke test runs the analyzer on all bundled fixtures, including the newer nested `meta_info` request-log shape, and checks that the expected anomaly flags or request counters fire.

## References

- [`SKILL.md`](SKILL.md): skill trigger and workflow instructions
- [`references/log-format.md`](references/log-format.md): supported log line shapes
- [`references/csv-schema.md`](references/csv-schema.md): CSV columns, units, and flag rules
- [`references/html-report.md`](references/html-report.md): HTML report sections and behavior
- [SGLang](https://github.com/sgl-project/sglang)
- [uv](https://docs.astral.sh/uv/)
- [PEP 723](https://peps.python.org/pep-0723/)
