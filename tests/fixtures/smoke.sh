#!/usr/bin/env bash
# Smoke test: run analyze.py on each fixture, assert rows + key flag values.
# Exits 0 on success, non-zero on first failure.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT

run() {
  local fx="$1"
  uv run --quiet "$SKILL_DIR/scripts/analyze.py" \
    --log "$SKILL_DIR/tests/fixtures/${fx}.log" \
    --out "$OUT" --basename "$fx" --quiet --no-html
}

assert() {
  local fx="$1" col="$2" expected_any="$3"
  local csv
  csv=$(ls "$OUT"/sglang_report_"$fx"_*.csv | head -1)
  local hits
  hits=$(awk -F, -v col="$col" -v val="$expected_any" '
    NR==1 { for (i=1;i<=NF;i++) if ($i==col) c=i; next }
    $c==val { print }
  ' "$csv" | wc -l | tr -d ' ')
  if [ "$hits" -lt 1 ]; then
    echo "FAIL ${fx}: expected at least one row with ${col}=${expected_any}, got 0"
    echo "    csv: $csv"
    return 1
  fi
  echo "OK   ${fx}: ${col}=${expected_any} fired in $hits row(s)"
}

assert_no() {
  local fx="$1" col="$2"
  local csv
  csv=$(ls "$OUT"/sglang_report_"$fx"_*.csv | head -1)
  local hits
  hits=$(awk -F, -v col="$col" '
    NR==1 { for (i=1;i<=NF;i++) if ($i==col) c=i; next }
    $c=="1" { print }
  ' "$csv" | wc -l | tr -d ' ')
  if [ "$hits" -gt 0 ]; then
    echo "FAIL ${fx}: ${col} should not fire but fired in ${hits} row(s)"
    return 1
  fi
  echo "OK   ${fx}: ${col} did not fire"
}

echo "smoke test: $SKILL_DIR/tests/fixtures/* -> $OUT"

run normal
run pd
run mtp
run radix
run log_requests

assert_no normal flag_retraction_spike
assert_no normal flag_low_accept_rate
assert_no normal flag_cache_collapse

assert    pd flag_retraction_spike 1
assert    mtp flag_low_accept_rate 1
assert    radix flag_cache_collapse 1
assert    log_requests flag_ttft_regression 1

echo
echo "all assertions passed"
