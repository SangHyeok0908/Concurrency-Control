#!/usr/bin/env bash
# 동일 applicant+slot HTTP 재요청을 실제로 발생시키고 HTTP 상태와 DB 불변식을 함께 기록한다.
# 기존 benchmark.sh의 서로 다른 지원자 정원 경쟁과 목적·CSV를 섞지 않는다.
set -uo pipefail

cd "$(dirname "$0")/.."

BASE_URL="${BASE_URL:-http://localhost:8080}"
MYSQL_CONTAINER="${MYSQL_CONTAINER:-reservation-mysql}"
OUT_CSV="${OUT_CSV:-docs/benchmark/duplicate-runs.csv}"
ENVIRONMENT_WAIT_SECONDS="${ENVIRONMENT_WAIT_SECONDS:-60}"
CAPACITY=200
REQUESTS=200
ROUNDS=5
STRATEGIES_CSV="baseline,unique,conditional,pessimistic,optimistic"
PRINT_PLAN=false

usage() {
  cat <<'EOF'
Usage: scripts/benchmark-duplicates.sh [options]

Options:
  --capacity N          slot capacity (default: 200)
  --requests N          identical concurrent requests (default: 200)
  --rounds N            rounds (default: 5)
  --strategies CSV      comma-separated strategies
  --out PATH            output CSV (default: docs/benchmark/duplicate-runs.csv)
  --print-plan          print the cyclic plan without HTTP, DB, or file access
  -h, --help            show this help
EOF
}

die_usage() {
  echo "✗ $1" >&2
  exit 2
}

while (( $# > 0 )); do
  case "$1" in
    --capacity)
      (( $# >= 2 )) || die_usage "--capacity requires a value"
      CAPACITY="$2"
      shift 2
      ;;
    --requests)
      (( $# >= 2 )) || die_usage "--requests requires a value"
      REQUESTS="$2"
      shift 2
      ;;
    --rounds)
      (( $# >= 2 )) || die_usage "--rounds requires a value"
      ROUNDS="$2"
      shift 2
      ;;
    --strategies)
      (( $# >= 2 )) || die_usage "--strategies requires a value"
      STRATEGIES_CSV="$2"
      shift 2
      ;;
    --out)
      (( $# >= 2 )) || die_usage "--out requires a value"
      OUT_CSV="$2"
      shift 2
      ;;
    --print-plan)
      PRINT_PLAN=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die_usage "unknown option: $1"
      ;;
  esac
done

[[ "$CAPACITY" =~ ^[1-9][0-9]*$ ]] || die_usage "capacity must be a positive integer"
[[ "$REQUESTS" =~ ^[1-9][0-9]*$ ]] || die_usage "requests must be a positive integer"
[[ "$ROUNDS" =~ ^[1-9][0-9]*$ ]] || die_usage "rounds must be a positive integer"
(( CAPACITY >= REQUESTS )) || die_usage "capacity must be at least requests"
[[ -n "$STRATEGIES_CSV" ]] || die_usage "strategies must not be empty"

IFS=',' read -r -a STRATEGIES <<< "$STRATEGIES_CSV"
seen_strategies_csv=""
for strategy in "${STRATEGIES[@]}"; do
  [[ -n "$strategy" ]] || die_usage "strategies must not contain an empty value"
  case "$strategy" in
    baseline|unique|conditional|pessimistic|optimistic) ;;
    *) die_usage "unknown strategy: $strategy" ;;
  esac
  [[ ",$seen_strategies_csv," != *",$strategy,"* ]] \
    || die_usage "duplicate strategy: $strategy"
  seen_strategies_csv="${seen_strategies_csv:+$seen_strategies_csv,}$strategy"
done

if (( ${#STRATEGIES[@]} == 5 && ROUNDS != 5 )); then
  die_usage "full campaign requires exactly 5 rounds"
fi

print_plan() {
  local round position index strategy_count
  strategy_count=${#STRATEGIES[@]}
  echo "round,position,strategy,capacity,requests"
  for (( round = 1; round <= ROUNDS; round++ )); do
    for (( position = 1; position <= strategy_count; position++ )); do
      index=$(( (round + position - 2) % strategy_count ))
      printf '%d,%d,%s,%d,%d\n' \
        "$round" "$position" "${STRATEGIES[$index]}" "$CAPACITY" "$REQUESTS"
    done
  done
}

if [[ "$PRINT_PLAN" == true ]]; then
  print_plan
  exit 0
fi

[[ ! -e "$OUT_CSV" ]] || die_usage "output already exists: $OUT_CSV"
[[ -d "$(dirname "$OUT_CSV")" ]] || die_usage "output directory does not exist: $(dirname "$OUT_CSV")"

mysql_q() {
  docker exec "$MYSQL_CONTAINER" mysql -uroot -N -B -e "$1" reservation 2>/dev/null
}

verify_benchmark_environment() {
  local response validation rc second
  response=$(curl -fsS "$BASE_URL/api/metrics/benchmark-environment") || {
    echo "✗ benchmark 환경 API에 연결할 수 없음: $BASE_URL" >&2
    echo "  spring.profiles.active=benchmark 로 앱을 기동했는지 확인하세요." >&2
    return 1
  }

  for (( second = 0; second < ENVIRONMENT_WAIT_SECONDS; second++ )); do
    validation=$(printf '%s' "$response" | python3 scripts/validate_benchmark_environment.py \
      --expected-max-attempts 5 2>&1)
    rc=$?
    case "$rc" in
      0)
        echo "▶ $validation"
        return 0
        ;;
      2)
        sleep 1
        response=$(curl -fsS "$BASE_URL/api/metrics/benchmark-environment") || return 1
        ;;
      *)
        echo "$validation" >&2
        return 1
        ;;
    esac
  done

  echo "$validation" >&2
  echo "✗ Hikari 풀 준비를 ${ENVIRONMENT_WAIT_SECONDS}초 안에 마치지 못함" >&2
  return 1
}

verify_benchmark_environment || exit 1

GATLING_LOG=$(mktemp "${TMPDIR:-/tmp}/duplicate-gatling.log.XXXXXX") || exit 1
cleanup() {
  rm -f -- "$GATLING_LOG"
}
trap cleanup EXIT

total=$(( ROUNDS * ${#STRATEGIES[@]} ))
done_count=0
rows_written=0

echo "▶ 동일 요청 검증: rounds=$ROUNDS strategies=[$STRATEGIES_CSV] cap=$CAPACITY requests=$REQUESTS"
for (( round = 1; round <= ROUNDS; round++ )); do
  for (( position = 1; position <= ${#STRATEGIES[@]}; position++ )); do
    index=$(( (round + position - 2) % ${#STRATEGIES[@]} ))
    strategy="${STRATEGIES[$index]}"
    done_count=$(( done_count + 1 ))
    printf '[%2d/%2d] round=%d position=%d %-11s ' \
      "$done_count" "$total" "$round" "$position" "$strategy"

    if ! before_slot=$(mysql_q "SELECT COALESCE(MAX(id), 0) FROM interview_slot") \
        || ! before_applicant=$(mysql_q "SELECT COALESCE(MAX(id), 0) FROM applicant"); then
      echo "✗ 실행 전 seed id 조회 실패" >&2
      exit 1
    fi
    [[ "$before_slot" =~ ^[0-9]+$ && "$before_applicant" =~ ^[0-9]+$ ]] || {
      echo "✗ 실행 전 seed id가 올바르지 않음" >&2
      exit 1
    }

    ./gradlew gatlingRun --console=plain -q \
      --simulation com.interview.reservation.loadtest.DuplicateReservationSimulation \
      -DbaseUrl="$BASE_URL" -Dstrategy="$strategy" \
      -Dcapacity="$CAPACITY" -Drequests="$REQUESTS" \
      > "$GATLING_LOG" 2>&1
    gatling_rc=$?
    if (( gatling_rc != 0 )); then
      echo "✗ gatlingRun 실패(rc=$gatling_rc)" >&2
      tail -20 "$GATLING_LOG" >&2
      exit 1
    fi

    if ! slot_id=$(mysql_q "SELECT MAX(id) FROM interview_slot") \
        || ! applicant_id=$(mysql_q "SELECT MAX(id) FROM applicant"); then
      echo "✗ 실행 후 seed id 조회 실패" >&2
      exit 1
    fi
    if [[ ! "$slot_id" =~ ^[0-9]+$ || ! "$applicant_id" =~ ^[0-9]+$ \
        || "$slot_id" -le "$before_slot" || "$applicant_id" -le "$before_applicant" ]]; then
      echo "✗ 새 슬롯 또는 지원자가 생기지 않음 — seed 실패" >&2
      exit 1
    fi

    if ! confirmed=$(mysql_q \
        "SELECT COUNT(*) FROM reservation WHERE slot_id = $slot_id AND status = 'CONFIRMED'") \
        || ! remaining=$(mysql_q \
        "SELECT remaining FROM interview_slot WHERE id = $slot_id") \
        || ! pair_reservations=$(mysql_q \
        "SELECT COUNT(*) FROM reservation WHERE slot_id = $slot_id AND applicant_id = $applicant_id") \
        || ! duplicate_rows=$(mysql_q \
        "SELECT COALESCE(SUM(c - 1), 0) FROM (SELECT COUNT(*) c FROM reservation WHERE slot_id = $slot_id GROUP BY applicant_id, slot_id HAVING COUNT(*) > 1) d"); then
      echo "✗ DB 불변식 조회 실패" >&2
      exit 1
    fi
    if [[ ! "$confirmed" =~ ^[0-9]+$ || ! "$remaining" =~ ^-?[0-9]+$ \
        || ! "$pair_reservations" =~ ^[0-9]+$ || ! "$duplicate_rows" =~ ^[0-9]+$ ]]; then
      echo "✗ DB 불변식 값이 올바르지 않음" >&2
      exit 1
    fi

    mode="append"
    (( rows_written == 0 )) && mode="create"
    if ! python3 scripts/duplicate_benchmark.py record "$OUT_CSV" \
        --mode "$mode" \
        --timestamp "$(date +%Y-%m-%dT%H:%M:%S)" \
        --round "$round" \
        --position "$position" \
        --strategy "$strategy" \
        --capacity "$CAPACITY" \
        --requests "$REQUESTS" \
        --slot-id "$slot_id" \
        --applicant-id "$applicant_id" \
        --gatling-log "$GATLING_LOG" \
        --remaining "$remaining" \
        --confirmed "$confirmed" \
        --pair-reservations "$pair_reservations" \
        --duplicate-rows "$duplicate_rows"; then
      echo "✗ 동일 요청 불변식 실패 — 실패 행은 $OUT_CSV 에 보존됨" >&2
      exit 1
    fi
    rows_written=$(( rows_written + 1 ))
    printf '✓ 확정=%s pair=%s remaining=%s duplicates=%s\n' \
      "$confirmed" "$pair_reservations" "$remaining" "$duplicate_rows"
  done
done

if (( rows_written != total )); then
  echo "✗ 예정한 $total행 중 ${rows_written}행만 기록됨" >&2
  exit 1
fi

if (( ${#STRATEGIES[@]} == 5 && ROUNDS == 5 )); then
  python3 scripts/duplicate_benchmark.py summarize "$OUT_CSV" --rounds 5 || exit 1
fi
echo "▶ 완료 — $OUT_CSV"
