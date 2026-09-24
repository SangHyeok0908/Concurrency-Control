#!/usr/bin/env bash
# Methodology-v2 two-phase, counterbalanced benchmark campaign runner.
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
PROJECT_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$PROJECT_ROOT" || exit 1

BASE_URL="${BASE_URL:-http://localhost:8080}"
MYSQL_CONTAINER="${MYSQL_CONTAINER:-reservation-mysql}"
BENCHMARK_GRADLEW="${BENCHMARK_GRADLEW:-./gradlew}"
ENVIRONMENT_WAIT_SECONDS="${ENVIRONMENT_WAIT_SECONDS:-60}"
CAMPAIGN_ROOT="docs/benchmark/campaigns"
CANONICAL_OUT="docs/benchmark/raw-runs-v2.csv"
V1_CANONICAL="docs/benchmark/raw-runs.csv"
ROUNDS=10
CAMPAIGN_ID=""
PHASE=""
PRINT_PLAN=0
seen_campaign=0
seen_phase=0
seen_rounds=0
seen_print=0
seen_root=0
seen_canonical=0

ORIGINAL_INVOCATION=$(python3 - "$0" "$@" <<'PY'
import shlex
import sys
print(" ".join(shlex.quote(part) for part in sys.argv[1:]))
PY
) || exit 1

usage_error() {
  echo "benchmark.sh: error: $1" >&2
  exit 2
}

need_value() {
  if [[ $# -lt 2 || -z "$2" || "$2" == --* ]]; then
    usage_error "$1 requires a value"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --campaign-id)
      need_value "$@"
      (( seen_campaign == 0 )) || usage_error "duplicate --campaign-id"
      seen_campaign=1; CAMPAIGN_ID="$2"; shift 2 ;;
    --phase)
      need_value "$@"
      (( seen_phase == 0 )) || usage_error "duplicate --phase"
      seen_phase=1; PHASE="$2"; shift 2 ;;
    --rounds)
      need_value "$@"
      (( seen_rounds == 0 )) || usage_error "duplicate --rounds"
      seen_rounds=1; ROUNDS="$2"; shift 2 ;;
    --print-plan)
      (( seen_print == 0 )) || usage_error "duplicate --print-plan"
      seen_print=1; PRINT_PLAN=1; shift ;;
    --campaign-root)
      need_value "$@"
      (( seen_root == 0 )) || usage_error "duplicate --campaign-root"
      seen_root=1; CAMPAIGN_ROOT="$2"; shift 2 ;;
    --canonical-out)
      need_value "$@"
      (( seen_canonical == 0 )) || usage_error "duplicate --canonical-out"
      seen_canonical=1; CANONICAL_OUT="$2"; shift 2 ;;
    *) usage_error "unknown option: $1" ;;
  esac
done

(( seen_campaign == 1 )) || usage_error "--campaign-id is required"
(( seen_phase == 1 )) || usage_error "--phase is required"
[[ "$CAMPAIGN_ID" =~ ^[A-Za-z0-9._-]+$ ]] || usage_error "invalid campaign id"
[[ "$CAMPAIGN_ID" != "." && "$CAMPAIGN_ID" != ".." ]] \
  || usage_error "campaign id must name a direct child"
case "$PHASE" in
  a) OPTIMISTIC_CAP=5 ;;
  b) OPTIMISTIC_CAP=20 ;;
  *) usage_error "phase must be a or b" ;;
esac
[[ "$ROUNDS" =~ ^[1-9][0-9]*$ ]] || usage_error "rounds must be a positive multiple of 10"
(( ROUNDS % 10 == 0 )) || usage_error "rounds must be a positive multiple of 10"
[[ -n "$CAMPAIGN_ROOT" && -n "$CANONICAL_OUT" ]] || usage_error "paths must not be empty"
case "$CAMPAIGN_ROOT$CANONICAL_OUT" in
  *$'\r'*|*$'\n'*) usage_error "paths must be single-line values" ;;
esac
CAMPAIGN_ROOT_DISPLAY="$CAMPAIGN_ROOT"
CANONICAL_OUT_DISPLAY="$CANONICAL_OUT"

canonical_guard=$(python3 - "$CANONICAL_OUT" "$V1_CANONICAL" <<'PY'
import os
import sys

candidate = os.path.abspath(sys.argv[1])
legacy = os.path.abspath(sys.argv[2])
same = os.path.realpath(candidate) == os.path.realpath(legacy)
if not same and os.path.lexists(candidate) and os.path.lexists(legacy):
    try:
        same = os.path.samefile(candidate, legacy)
    except OSError:
        same = False
if same:
    raise SystemExit(1)
print(candidate)
PY
)
if [[ $? -ne 0 || -z "$canonical_guard" ]]; then
  usage_error "--canonical-out must not resolve to methodology-v1 raw-runs.csv"
fi
CANONICAL_OUT="$canonical_guard"
CAMPAIGN_ROOT=$(python3 - "$CAMPAIGN_ROOT" <<'PY'
import os
import sys
print(os.path.abspath(sys.argv[1]))
PY
) || usage_error "invalid --campaign-root"
[[ -n "$CAMPAIGN_ROOT" ]] || usage_error "invalid --campaign-root"

if (( PRINT_PLAN == 1 )); then
  exec python3 scripts/benchmark_schedule.py --rounds "$ROUNDS"
fi

[[ -n "$BENCHMARK_GRADLEW" ]] || usage_error "BENCHMARK_GRADLEW must not be empty"
[[ "$ENVIRONMENT_WAIT_SECONDS" =~ ^[0-9]+$ ]] \
  || usage_error "ENVIRONMENT_WAIT_SECONDS must be a non-negative integer"

CAMPAIGN_DIR="$CAMPAIGN_ROOT/$CAMPAIGN_ID"
CAMPAIGN_CSV="$CAMPAIGN_DIR/raw-runs.csv"
MANIFEST="$CAMPAIGN_DIR/manifest.md"
ENVIRONMENT_A="$CAMPAIGN_DIR/environment-phase-a.json"
ENVIRONMENT_B="$CAMPAIGN_DIR/environment-phase-b.json"
[[ "$(dirname "$CAMPAIGN_DIR")" == "$CAMPAIGN_ROOT" ]] \
  || usage_error "campaign directory escaped campaign root"
if [[ "$PHASE" == "a" ]]; then
  ENVIRONMENT_PATH="$ENVIRONMENT_A"
else
  ENVIRONMENT_PATH="$ENVIRONMENT_B"
fi
APP_START_ID="$CAMPAIGN_ID-phase-$PHASE"
if [[ "$PHASE" == "a" ]]; then
  ENVIRONMENT_DISPLAY="$CAMPAIGN_ROOT_DISPLAY/$CAMPAIGN_ID/environment-phase-a.json"
else
  ENVIRONMENT_DISPLAY="$CAMPAIGN_ROOT_DISPLAY/$CAMPAIGN_ID/environment-phase-b.json"
fi

path_exists() { [[ -e "$1" || -L "$1" ]]; }

manifest_has_exactly_one() {
  local count
  count=$(grep -Fxc -- "$1" "$MANIFEST" 2>/dev/null || true)
  [[ "$count" == "1" ]]
}

if [[ "$PHASE" == "a" ]]; then
  path_exists "$CAMPAIGN_DIR" \
    && usage_error "Phase A requires a new campaign id: $CAMPAIGN_ID"
else
  [[ -d "$CAMPAIGN_DIR" && ! -L "$CAMPAIGN_DIR" ]] \
    || usage_error "Phase B requires an existing campaign directory"
  [[ -f "$MANIFEST" && ! -L "$MANIFEST" ]] \
    || usage_error "Phase B requires the Phase A manifest"
  [[ -f "$ENVIRONMENT_A" && ! -L "$ENVIRONMENT_A" ]] \
    || usage_error "Phase B requires the Phase A environment snapshot"
  [[ -f "$CAMPAIGN_CSV" && ! -L "$CAMPAIGN_CSV" ]] \
    || usage_error "Phase B requires the Phase A campaign CSV"
  path_exists "$ENVIRONMENT_B" \
    && usage_error "Phase B has already been attempted for this campaign"
  manifest_has_exactly_one "- phase_a_status: complete" \
    || usage_error "Phase A manifest is not complete"
  if grep -Fqx -- "## Phase B" "$MANIFEST" \
      || grep -Eq -- '^- phase_b_' "$MANIFEST"; then
    usage_error "Phase B has already been attempted for this campaign"
  fi
  python3 scripts/validate_benchmark_campaign.py "$CAMPAIGN_CSV" \
      --require phase-a --rounds "$ROUNDS" >/dev/null \
    || usage_error "Phase B requires complete matching Phase A evidence"
  python3 - "$CAMPAIGN_CSV" "$ENVIRONMENT_A" "$CAMPAIGN_ID" <<'PY' \
    || usage_error "Phase A environment snapshot hash mismatch"
import csv
import hashlib
import sys

with open(sys.argv[2], "rb") as handle:
    actual = hashlib.sha256(handle.read()).hexdigest()
with open(sys.argv[1], "r", newline="", encoding="utf-8") as handle:
    rows = list(csv.DictReader(handle))
hashes = {row.get("environment_sha256") for row in rows if row.get("phase") == "a"}
campaigns = {row.get("campaign_id") for row in rows}
if hashes != {actual} or campaigns != {sys.argv[3]}:
    print("Phase A environment snapshot or campaign identity mismatch", file=sys.stderr)
    raise SystemExit(1)
PY
fi

# Generate the plan before claiming campaign state. A deterministic scheduler
# failure therefore does not burn a campaign id.
PLAN_FILE=""
REPORT_SNAPSHOT=""
GATLING_LOG=""
VALIDATOR_ERROR=""
cleanup() {
  [[ -z "$PLAN_FILE" || ! -e "$PLAN_FILE" ]] || rm -f -- "$PLAN_FILE"
  [[ -z "$REPORT_SNAPSHOT" || ! -e "$REPORT_SNAPSHOT" ]] || rm -f -- "$REPORT_SNAPSHOT"
  [[ -z "$GATLING_LOG" || ! -e "$GATLING_LOG" ]] || rm -f -- "$GATLING_LOG"
  [[ -z "$VALIDATOR_ERROR" || ! -e "$VALIDATOR_ERROR" ]] || rm -f -- "$VALIDATOR_ERROR"
}
trap cleanup EXIT
PLAN_FILE=$(mktemp "${TMPDIR:-/tmp}/benchmark-plan.XXXXXX") || exit 1
REPORT_SNAPSHOT=$(mktemp "${TMPDIR:-/tmp}/benchmark-reports.XXXXXX") || exit 1
GATLING_LOG=$(mktemp "${TMPDIR:-/tmp}/benchmark-gatling.XXXXXX") || exit 1
VALIDATOR_ERROR=$(mktemp "${TMPDIR:-/tmp}/benchmark-environment.XXXXXX") || exit 1
python3 scripts/benchmark_schedule.py --rounds "$ROUNDS" > "$PLAN_FILE" \
  || { echo "benchmark.sh: schedule generation failed" >&2; exit 1; }

one_line() { printf '%s' "$1" | tr '\r\n' '  '; }
now() { date +%Y-%m-%dT%H:%M:%S; }

invocation="$ORIGINAL_INVOCATION"

manifest_set() {
  local key value
  key="$1"
  value=$(one_line "$2")
  python3 - "$MANIFEST" "$key" "$value" <<'PY'
import os
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
prefix = "- %s: " % sys.argv[2]
lines = path.read_text(encoding="utf-8").splitlines(True)
indexes = [index for index, line in enumerate(lines) if line.startswith(prefix)]
if len(indexes) != 1:
    raise SystemExit("manifest key is missing or duplicated: %s" % sys.argv[2])
ending = "\n" if lines[indexes[0]].endswith("\n") else ""
lines[indexes[0]] = prefix + sys.argv[3] + ending
descriptor, name = tempfile.mkstemp(
    prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent))
try:
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
        handle.write("".join(lines))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(name, str(path))
except BaseException:
    try:
        os.unlink(name)
    except FileNotFoundError:
        pass
    raise
PY
}

create_manifest() {
  local created predecessor_raw_runs predecessor_execution_record
  created=$(now)
  predecessor_raw_runs=$(python3 - "$CAMPAIGN_DIR" \
      "$PROJECT_ROOT/docs/benchmark/raw-runs.csv" <<'PY'
import os
import sys
from urllib.parse import quote

print(quote(os.path.relpath(
    os.path.realpath(sys.argv[2]), start=os.path.realpath(sys.argv[1])), safe="/"))
PY
  ) || return 1
  predecessor_execution_record=$(python3 - "$CAMPAIGN_DIR" \
      "$PROJECT_ROOT/docs/benchmark/2026-09-24-controlled-run.md" <<'PY'
import os
import sys
from urllib.parse import quote

print(quote(os.path.relpath(
    os.path.realpath(sys.argv[2]), start=os.path.realpath(sys.argv[1])), safe="/"))
PY
  ) || return 1
  printf '%s\n' \
    "# Benchmark Campaign $CAMPAIGN_ID" "" "## Campaign" "" \
    "- campaign_id: $CAMPAIGN_ID" \
    "- rounds_per_phase: $ROUNDS" \
    "- schedule_version: williams-10-v1" \
    "- predecessor_methodology: methodology-v1-fixed-order" \
    "- predecessor_raw_runs: [docs/benchmark/raw-runs.csv]($predecessor_raw_runs)" \
    "- predecessor_execution_record: [docs/benchmark/2026-09-24-controlled-run.md]($predecessor_execution_record)" \
    "- created_at: $created" \
    "- gatling_version: 3.13.5" "" "## Phase A" "" \
    "- phase_a_invocation: $invocation" \
    "- phase_a_status: running" \
    "- phase_a_app_start_id: $CAMPAIGN_ID-phase-a" \
    "- phase_a_environment_path: $CAMPAIGN_ROOT_DISPLAY/$CAMPAIGN_ID/environment-phase-a.json" \
    "- phase_a_environment_sha256: pending" \
    "- phase_a_expected_max_attempts: 5" \
    "- phase_a_git_head: pending" \
    "- phase_a_os: pending" \
    "- phase_a_java: pending" \
    "- phase_a_gradle: pending" \
    "- phase_a_mysql: pending" \
    "- phase_a_max_connections: pending" \
    "- phase_a_initial_slot_count: pending" \
    "- phase_a_initial_reservation_count: pending" \
    "- phase_a_started_at: $created" \
    "- phase_a_completed_at: pending" \
    "- phase_a_warmup_status: pending" \
    "- phase_a_measured_status: pending" \
    "- phase_a_measured_rows: 0" \
    "- phase_a_measurement_started_at: pending" \
    "- phase_a_measurement_completed_at: pending" \
    "- phase_a_failure_reason: none" > "$MANIFEST"
}

append_phase_b_manifest() {
  local started
  started=$(now)
  printf '%s\n' "" "## Phase B" "" \
    "- phase_b_attempted: true" \
    "- phase_b_status: not_started" \
    "- phase_b_invocation: $invocation" \
    "- phase_b_app_start_id: $CAMPAIGN_ID-phase-b" \
    "- phase_b_environment_path: $ENVIRONMENT_DISPLAY" \
    "- phase_b_environment_sha256: pending" \
    "- phase_b_expected_max_attempts: 20" \
    "- phase_b_git_head: pending" \
    "- phase_b_os: pending" \
    "- phase_b_java: pending" \
    "- phase_b_gradle: pending" \
    "- phase_b_mysql: pending" \
    "- phase_b_max_connections: pending" \
    "- phase_b_initial_slot_count: pending" \
    "- phase_b_initial_reservation_count: pending" \
    "- phase_b_started_at: $started" \
    "- phase_b_completed_at: pending" \
    "- phase_b_warmup_status: pending" \
    "- phase_b_measured_status: pending" \
    "- phase_b_measured_rows: 0" \
    "- phase_b_measurement_started_at: pending" \
    "- phase_b_measurement_completed_at: pending" \
    "- phase_b_failure_reason: none" >> "$MANIFEST"
}

if [[ "$PHASE" == "a" ]]; then
  mkdir -p "$CAMPAIGN_ROOT" || exit 1
  mkdir "$CAMPAIGN_DIR" || usage_error "Phase A campaign id was claimed concurrently"
  create_manifest \
    || { echo "benchmark.sh: cannot create campaign manifest" >&2; exit 1; }
else
  append_phase_b_manifest || exit 1
  manifest_set phase_b_status running || exit 1
fi

phase_key() { printf 'phase_%s_%s' "$PHASE" "$1"; }
set_phase_fact() { manifest_set "$(phase_key "$1")" "$2"; }
fail_before_measurement() {
  local category reason
  category="$1"
  reason=$(one_line "$2")
  set_phase_fact status failed >/dev/null 2>&1 || true
  if [[ "$category" == "warmup_failed" ]]; then
    set_phase_fact warmup_status failed >/dev/null 2>&1 || true
  else
    set_phase_fact warmup_status not_started >/dev/null 2>&1 || true
  fi
  set_phase_fact measured_status not_started >/dev/null 2>&1 || true
  set_phase_fact completed_at "$(now)" >/dev/null 2>&1 || true
  set_phase_fact failure_reason "$reason" >/dev/null 2>&1 || true
  echo "benchmark.sh: $category: $reason" >&2
  return 1
}

mysql_q() {
  docker exec "$MYSQL_CONTAINER" mysql -uroot -N -B -e "$1" reservation 2>/dev/null
}

capture_metadata() {
  local git_head os_version java_version gradle_version mysql_version
  local max_connections counts counts_without_tab
  local initial_slots initial_reservations extra_counts
  git_head=$(git rev-parse HEAD 2>/dev/null) || return 1
  os_version=$(uname -a 2>/dev/null) || return 1
  java_version=$(java -version 2>&1 | head -1) || return 1
  gradle_version=$("$BENCHMARK_GRADLEW" --version --console=plain 2>&1 \
    | awk '/^Gradle / { print; exit }') || return 1
  [[ -n "$gradle_version" ]] || return 1
  mysql_version=$(mysql_q "SELECT VERSION()") || return 1
  max_connections=$(mysql_q "SELECT @@max_connections") || return 1
  counts=$(mysql_q "/* initial benchmark counts */ SELECT (SELECT COUNT(*) FROM interview_slot), (SELECT COUNT(*) FROM reservation)") \
    || return 1
  case "$counts" in *$'\n'*) return 1 ;; esac
  counts_without_tab=${counts//$'\t'/}
  [[ $((${#counts} - ${#counts_without_tab})) -eq 1 ]] || return 1
  IFS=$'\t' read -r initial_slots initial_reservations extra_counts <<< "$counts"
  [[ -z "$extra_counts" && "$initial_slots" =~ ^[0-9]+$ \
      && "$initial_reservations" =~ ^[0-9]+$ ]] || return 1
  case "$mysql_version$max_connections" in *$'\n'*) return 1 ;; esac
  [[ "$max_connections" =~ ^[1-9][0-9]*$ ]] || return 1
  set_phase_fact git_head "$git_head" || return 1
  set_phase_fact os "$os_version" || return 1
  set_phase_fact java "$java_version" || return 1
  set_phase_fact gradle "$gradle_version" || return 1
  set_phase_fact mysql "$mysql_version" || return 1
  set_phase_fact max_connections "$max_connections" || return 1
  set_phase_fact initial_slot_count "$initial_slots" || return 1
  set_phase_fact initial_reservation_count "$initial_reservations" || return 1
}

fetch_environment() {
  local response validation rc waited line found_hash stored_hash
  waited=0
  while :; do
    response=$(curl -fsS "$BASE_URL/api/metrics/benchmark-environment") \
      || { STEP_ERROR="cannot reach benchmark environment endpoint"; return 1; }
    validation=$(printf '%s' "$response" \
      | python3 scripts/validate_benchmark_environment.py \
          --expected-max-attempts "$OPTIMISTIC_CAP" \
          --canonical-output "$ENVIRONMENT_PATH" 2>"$VALIDATOR_ERROR")
    rc=$?
    if [[ $rc -eq 0 ]]; then
      found_hash=""
      while IFS= read -r line; do
        if [[ "$line" =~ ^environment_sha256=([0-9a-f]{64})$ ]]; then
          [[ -z "$found_hash" ]] \
            || { STEP_ERROR="environment validator returned duplicate hashes"; return 1; }
          found_hash="${BASH_REMATCH[1]}"
        fi
      done <<< "$validation"
      [[ -n "$found_hash" ]] \
        || { STEP_ERROR="environment validator did not return a canonical hash"; return 1; }
      stored_hash=$(python3 - "$ENVIRONMENT_PATH" <<'PY'
import hashlib
import sys
with open(sys.argv[1], "rb") as handle:
    print(hashlib.sha256(handle.read()).hexdigest())
PY
) || { STEP_ERROR="cannot hash stored environment snapshot"; return 1; }
      [[ "$stored_hash" == "$found_hash" ]] \
        || { STEP_ERROR="stored environment snapshot hash mismatch"; return 1; }
      ENVIRONMENT_SHA256="$found_hash"
      return 0
    fi
    if [[ $rc -eq 2 && $waited -lt $ENVIRONMENT_WAIT_SECONDS ]]; then
      waited=$((waited + 1))
      sleep 1
      continue
    fi
    STEP_ERROR=$(one_line "$(cat "$VALIDATOR_ERROR")")
    [[ -n "$STEP_ERROR" ]] || STEP_ERROR="environment validation failed"
    return 1
  done
}

STEP_ERROR=""
ENVIRONMENT_SHA256=""
fetch_environment \
  || { fail_before_measurement environment_failed "$STEP_ERROR"; exit 1; }
set_phase_fact environment_sha256 "$ENVIRONMENT_SHA256" || exit 1
capture_metadata \
  || { fail_before_measurement precondition_failed "cannot capture benchmark preconditions"; exit 1; }

reset_retry_metrics() {
  curl -fsS -X DELETE "$BASE_URL/api/metrics/optimistic-retries" >/dev/null
}

query_max_slot() {
  local value
  value=$(mysql_q "SELECT COALESCE(MAX(id), 0) FROM interview_slot") || return 1
  [[ "$value" =~ ^[0-9]+$ ]] || return 1
  printf '%s' "$value"
}

snapshot_reports() {
  local directory name
  : > "$REPORT_SNAPSHOT" || return 1
  for directory in build/reports/gatling/*; do
    [[ -d "$directory" ]] || continue
    name=${directory##*/}
    printf '%s\n' "$name" >> "$REPORT_SNAPSHOT" || return 1
  done
}

select_new_report() {
  local directory name count selected
  count=0
  selected=""
  for directory in build/reports/gatling/*; do
    [[ -d "$directory" ]] || continue
    name=${directory##*/}
    if ! grep -Fqx -- "$name" "$REPORT_SNAPSHOT"; then
      count=$((count + 1))
      selected="$directory"
    fi
  done
  [[ $count -eq 1 ]] || return 1
  [[ -f "$selected/simulation.log" && -f "$selected/js/stats.js" ]] || return 1
  REPORT_DIR="$selected"
}

invoke_gatling() {
  "$BENCHMARK_GRADLEW" gatlingRun --console=plain -q \
    -DbaseUrl="$BASE_URL" -Dstrategy="$strategy" \
    -Dcapacity="$capacity" -Dcontenders="$contenders" \
    >"$GATLING_LOG" 2>&1 </dev/null
}

run_warmup_entry() {
  local before_slot after_slot
  reset_retry_metrics \
    || { STEP_ERROR="retry metric reset failed"; return 1; }
  before_slot=$(query_max_slot) \
    || { STEP_ERROR="pre-run slot query failed"; return 1; }
  snapshot_reports \
    || { STEP_ERROR="cannot snapshot Gatling report directories"; return 1; }
  invoke_gatling \
    || { STEP_ERROR="Gatling warmup failed"; return 1; }
  after_slot=$(query_max_slot) \
    || { STEP_ERROR="post-run slot query failed"; return 1; }
  [[ "$after_slot" -gt "$before_slot" ]] \
    || { STEP_ERROR="warmup did not create a new slot"; return 1; }
  select_new_report \
    || { STEP_ERROR="warmup did not create exactly one complete report"; return 1; }
}

set_phase_fact warmup_status running || exit 1
warmup_count=0
while IFS=$'\t' read -r schedule_version schedule_cycle schedule_row measured_round \
    position_in_round treatment_id strategy contention capacity contenders; do
  [[ "$schedule_version" != "schedule_version" ]] || continue
  run_warmup_entry \
    || { fail_before_measurement warmup_failed "$STEP_ERROR"; exit 1; }
  warmup_count=$((warmup_count + 1))
  [[ $warmup_count -lt 10 ]] || break
done < "$PLAN_FILE"
[[ $warmup_count -eq 10 ]] \
  || { fail_before_measurement warmup_failed "schedule row 1 did not contain ten treatments"; exit 1; }
set_phase_fact warmup_status complete || exit 1

slot_id=""; requests=""; ok=""; ko=""; mean_ms=""; p95_ms=""; max_ms=""
burst_start_epoch_ms=""; burst_end_epoch_ms=""; burst_wall_ms=""; tps=""
remaining=""; confirmed=""; overbooking=""; duplicates=""
retry_succeeded=""; retry_exhausted=""; version_conflicts=""; deadlocks=""
mean_attempts=""; run_status=""; error_reason=""; rows_written=0

build_row_json() {
  local timestamp
  timestamp=$(now)
  python3 - "2" "$CAMPAIGN_ID" "$PHASE" "$APP_START_ID" "$ENVIRONMENT_SHA256" \
    "$schedule_version" "$schedule_cycle" "$schedule_row" "$measured_round" \
    "$position_in_round" "$treatment_id" "$timestamp" "$strategy" "$OPTIMISTIC_CAP" \
    "$contention" "$capacity" "$contenders" "$slot_id" "$requests" "$ok" "$ko" \
    "$mean_ms" "$p95_ms" "$max_ms" "$burst_start_epoch_ms" "$burst_end_epoch_ms" \
    "$burst_wall_ms" "$tps" "$remaining" "$confirmed" "$overbooking" "$duplicates" \
    "$retry_succeeded" "$retry_exhausted" "$version_conflicts" "$deadlocks" \
    "$mean_attempts" "$run_status" "$error_reason" <<'PY'
import json
import sys

fields = [
    "schema_version", "campaign_id", "phase", "app_start_id",
    "environment_sha256", "schedule_version", "schedule_cycle", "schedule_row",
    "measured_round", "position_in_round", "treatment_id", "ts", "strategy",
    "optimistic_max_attempts", "contention", "capacity", "contenders", "slot_id",
    "requests", "ok", "ko", "mean_ms", "p95_ms", "max_ms",
    "burst_start_epoch_ms", "burst_end_epoch_ms", "burst_wall_ms", "tps",
    "remaining", "confirmed", "overbooking", "duplicates", "retry_succeeded",
    "retry_exhausted", "version_conflicts", "deadlocks", "mean_attempts",
    "run_status", "error_reason",
]
values = sys.argv[1:]
if len(values) != len(fields):
    raise SystemExit("internal benchmark row field mismatch")
print(json.dumps(dict(zip(fields, values)), ensure_ascii=False, allow_nan=False))
PY
}

write_current_row() {
  local payload mode
  payload=$(build_row_json) || return 1
  if [[ "$PHASE" == "a" && $rows_written -eq 0 ]]; then
    mode="--create"
  else
    mode="--append"
  fi
  printf '%s\n' "$payload" \
    | python3 scripts/write_benchmark_row.py "$mode" "$CAMPAIGN_CSV" || return 1
  rows_written=$((rows_written + 1))
}

clear_measurements() {
  slot_id=""; requests=""; ok=""; ko=""; mean_ms=""; p95_ms=""; max_ms=""
  burst_start_epoch_ms=""; burst_end_epoch_ms=""; burst_wall_ms=""; tps=""
  remaining=""; confirmed=""; overbooking=""; duplicates=""
  retry_succeeded=""; retry_exhausted=""; version_conflicts=""; deadlocks=""
  mean_attempts=""
}

record_measured_failure() {
  local status reason
  status="$1"
  reason=$(one_line "$2")
  run_status="$status"
  error_reason="$reason"
  if ! write_current_row; then
    set_phase_fact status failed >/dev/null 2>&1 || true
    set_phase_fact measured_status writer_failed >/dev/null 2>&1 || true
    set_phase_fact measured_rows "$rows_written" >/dev/null 2>&1 || true
    set_phase_fact measurement_completed_at "$(now)" >/dev/null 2>&1 || true
    set_phase_fact completed_at "$(now)" >/dev/null 2>&1 || true
    set_phase_fact failure_reason "row writer failed after $status" >/dev/null 2>&1 || true
    echo "benchmark.sh: writer failed; no recursive failure row was attempted" >&2
    return 1
  fi
  set_phase_fact status failed >/dev/null 2>&1 || true
  set_phase_fact measured_status failed >/dev/null 2>&1 || true
  set_phase_fact measured_rows "$rows_written" >/dev/null 2>&1 || true
  set_phase_fact measurement_completed_at "$(now)" >/dev/null 2>&1 || true
  set_phase_fact completed_at "$(now)" >/dev/null 2>&1 || true
  set_phase_fact failure_reason "$reason" >/dev/null 2>&1 || true
  echo "benchmark.sh: $status: $reason" >&2
  return 1
}

parse_report_values() {
  local output output_without_commas extra value
  output="$1"
  case "$output" in *$'\n'*) return 1 ;; esac
  output_without_commas=${output//,/}
  [[ $((${#output} - ${#output_without_commas})) -eq 9 ]] || return 1
  IFS=',' read -r requests ok ko mean_ms p95_ms max_ms \
    burst_start_epoch_ms burst_end_epoch_ms burst_wall_ms tps extra <<< "$output"
  [[ -z "$extra" ]] || return 1
  for value in "$requests" "$ok" "$ko" "$mean_ms" "$p95_ms" "$max_ms" \
      "$burst_start_epoch_ms" "$burst_end_epoch_ms" "$burst_wall_ms"; do
    [[ "$value" =~ ^[0-9]+$ ]] || return 1
  done
  [[ "$tps" =~ ^[0-9]+([.][0-9]+)?$ ]]
}

read_invariants() {
  local output output_without_tabs extra
  output=$(mysql_q "/* benchmark invariant tuple */ SELECT s.remaining, (SELECT COUNT(*) FROM reservation r WHERE r.slot_id = s.id AND r.status = 'CONFIRMED'), (SELECT COALESCE(SUM(d.c - 1), 0) FROM (SELECT COUNT(*) AS c FROM reservation WHERE slot_id = $slot_id GROUP BY applicant_id, slot_id HAVING COUNT(*) > 1) d) FROM interview_slot s WHERE s.id = $slot_id") \
    || return 1
  case "$output" in *$'\n'*) return 1 ;; esac
  output_without_tabs=${output//$'\t'/}
  [[ $((${#output} - ${#output_without_tabs})) -eq 2 ]] || return 1
  IFS=$'\t' read -r remaining confirmed duplicates extra <<< "$output"
  [[ -z "$extra" && "$remaining" =~ ^-?[0-9]+$ \
      && "$confirmed" =~ ^[0-9]+$ && "$duplicates" =~ ^[0-9]+$ ]] || return 1
  overbooking=$(( confirmed > capacity ? confirmed - capacity : 0 ))
}

read_retry_metrics() {
  local retry_json output output_without_commas extra value
  retry_json=$(curl -fsS "$BASE_URL/api/metrics/optimistic-retries") || return 1
  output=$(printf '%s' "$retry_json" | python3 scripts/parse_optimistic_metrics.py) \
    || return 1
  case "$output" in *$'\n'*) return 1 ;; esac
  output_without_commas=${output//,/}
  [[ $((${#output} - ${#output_without_commas})) -eq 4 ]] || return 1
  IFS=',' read -r retry_succeeded retry_exhausted version_conflicts deadlocks \
    mean_attempts extra <<< "$output"
  [[ -z "$extra" ]] || return 1
  for value in "$retry_succeeded" "$retry_exhausted" "$version_conflicts" "$deadlocks"; do
    [[ "$value" =~ ^[0-9]+$ ]] || return 1
  done
  [[ "$mean_attempts" =~ ^[0-9]+([.][0-9]+)?$ ]]
}

measure_entry() {
  local before_slot after_slot report_output
  clear_measurements
  run_status=""
  error_reason=""
  reset_retry_metrics \
    || { record_measured_failure retry_metrics_failed "retry metric reset failed"; return 1; }
  before_slot=$(query_max_slot) \
    || { record_measured_failure db_read_failed "pre-run slot query failed"; return 1; }
  snapshot_reports \
    || { record_measured_failure db_read_failed "cannot snapshot report directories"; return 1; }
  invoke_gatling \
    || { record_measured_failure gatling_failed "Gatling exited nonzero"; return 1; }
  after_slot=$(query_max_slot) \
    || { record_measured_failure db_read_failed "post-run slot query failed"; return 1; }
  [[ "$after_slot" -gt "$before_slot" ]] \
    || { record_measured_failure seed_failed "Gatling did not create a new slot"; return 1; }
  slot_id="$after_slot"
  select_new_report \
    || { record_measured_failure parse_failed "expected exactly one complete new Gatling report"; return 1; }
  report_output=$(python3 scripts/parse_gatling_report.py \
    "$REPORT_DIR" "$strategy" "$capacity" "$contenders") \
    || { record_measured_failure parse_failed "Gatling report parser failed"; return 1; }
  if ! parse_report_values "$report_output"; then
    clear_measurements
    slot_id="$after_slot"
    record_measured_failure parse_failed "Gatling report parser returned malformed values"
    return 1
  fi
  if ! read_invariants; then
    remaining=""; confirmed=""; overbooking=""; duplicates=""
    record_measured_failure db_read_failed "database invariant tuple failed validation"
    return 1
  fi
  if ! read_retry_metrics; then
    retry_succeeded=""; retry_exhausted=""; version_conflicts=""; deadlocks=""
    mean_attempts=""
    record_measured_failure retry_metrics_failed "retry metric snapshot failed validation"
    return 1
  fi
  run_status="ok"
  error_reason=""
  if ! write_current_row; then
    set_phase_fact status failed >/dev/null 2>&1 || true
    set_phase_fact measured_status writer_failed >/dev/null 2>&1 || true
    set_phase_fact measured_rows "$rows_written" >/dev/null 2>&1 || true
    set_phase_fact measurement_completed_at "$(now)" >/dev/null 2>&1 || true
    set_phase_fact completed_at "$(now)" >/dev/null 2>&1 || true
    set_phase_fact failure_reason "row writer failed" >/dev/null 2>&1 || true
    echo "benchmark.sh: writer failed; no recursive failure row was attempted" >&2
    return 1
  fi
}

set_phase_fact measured_status running || exit 1
set_phase_fact measurement_started_at "$(now)" || exit 1
while IFS=$'\t' read -r schedule_version schedule_cycle schedule_row measured_round \
    position_in_round treatment_id strategy contention capacity contenders; do
  [[ "$schedule_version" != "schedule_version" ]] || continue
  measure_entry || exit 1
done < "$PLAN_FILE"

expected_rows=$((ROUNDS * 10))
if [[ $rows_written -ne $expected_rows ]]; then
  set_phase_fact status failed >/dev/null 2>&1 || true
  set_phase_fact measured_status failed >/dev/null 2>&1 || true
  set_phase_fact measured_rows "$rows_written" >/dev/null 2>&1 || true
  set_phase_fact measurement_completed_at "$(now)" >/dev/null 2>&1 || true
  set_phase_fact completed_at "$(now)" >/dev/null 2>&1 || true
  set_phase_fact failure_reason "expected $expected_rows measured rows, wrote $rows_written" \
    >/dev/null 2>&1 || true
  echo "benchmark.sh: measured row count mismatch" >&2
  exit 1
fi

if [[ "$PHASE" == "a" ]]; then
  set_phase_fact measured_rows "$rows_written" || exit 1
  python3 scripts/validate_benchmark_campaign.py "$CAMPAIGN_CSV" \
      --require phase-a --rounds "$ROUNDS" >/dev/null || {
    set_phase_fact status failed >/dev/null 2>&1 || true
    set_phase_fact measured_status validation_failed >/dev/null 2>&1 || true
    set_phase_fact measurement_completed_at "$(now)" >/dev/null 2>&1 || true
    set_phase_fact completed_at "$(now)" >/dev/null 2>&1 || true
    set_phase_fact failure_reason "Phase A campaign validation failed" >/dev/null 2>&1 || true
    echo "benchmark.sh: Phase A campaign validation failed" >&2
    exit 1
  }
  set_phase_fact measured_status complete || exit 1
  set_phase_fact measurement_completed_at "$(now)" || exit 1
  set_phase_fact status complete || exit 1
  set_phase_fact completed_at "$(now)" || exit 1
  echo "benchmark campaign Phase A complete: $CAMPAIGN_CSV"
  exit 0
fi

set_phase_fact measured_rows "$rows_written" || exit 1
python3 scripts/validate_benchmark_campaign.py "$CAMPAIGN_CSV" \
    --require complete --rounds "$ROUNDS" >/dev/null || {
  set_phase_fact status failed >/dev/null 2>&1 || true
  set_phase_fact measured_status validation_failed >/dev/null 2>&1 || true
  set_phase_fact measurement_completed_at "$(now)" >/dev/null 2>&1 || true
  set_phase_fact failure_reason "complete campaign validation failed" >/dev/null 2>&1 || true
  set_phase_fact completed_at "$(now)" >/dev/null 2>&1 || true
  echo "benchmark.sh: complete campaign validation failed" >&2
  exit 1
}
set_phase_fact measured_status complete || exit 1
set_phase_fact measurement_completed_at "$(now)" || exit 1

if [[ "$ROUNDS" == "10" ]]; then
  python3 scripts/promote_benchmark_campaign.py "$CAMPAIGN_CSV" \
      --to "$CANONICAL_OUT" || {
    set_phase_fact status promotion_failed >/dev/null 2>&1 || true
    set_phase_fact failure_reason "canonical promotion failed" >/dev/null 2>&1 || true
    set_phase_fact completed_at "$(now)" >/dev/null 2>&1 || true
    echo "benchmark.sh: canonical promotion failed; campaign CSV was preserved" >&2
    exit 1
  }
  set_phase_fact status complete || exit 1
  set_phase_fact completed_at "$(now)" || exit 1
  echo "benchmark campaign complete: $CAMPAIGN_CSV"
else
  set_phase_fact status complete_noncanonical || exit 1
  set_phase_fact completed_at "$(now)" || exit 1
  echo "benchmark campaign complete_noncanonical: $CAMPAIGN_CSV"
fi
exit 0
