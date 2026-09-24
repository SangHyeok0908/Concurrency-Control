#!/usr/bin/env bash
#
# ⑦ step2/benchmark — 방어 전략별 부하 스윕 실행기
#
# 왜 스크립트인가. ④는 같은 부하를 손으로 번갈아 7쌍 인가했고, 그 결과 "실행 간 편차가 전략 간
# 차이보다 커서 확정할 수 없다"로 끝났다(docs/STEP2-PESSIMISTIC-LOCK.md 4절). 게다가 그 실행분은
# 리포트에 전략 이름표가 없어 사후 식별이 불가능해 "재현 가능한 측정 자산이 아니다"라고 스스로
# 단서를 달았다. 이 스크립트는 그 두 가지를 갚는다 — 반복을 자동화해 편차를 중앙값으로 눌러
# 가리고, 실행 하나하나를 CSV 한 줄로 남겨 표의 모든 숫자에 출처를 붙인다.
#
# 실행 순서는 라운드 단위 인터리브다. 한 전략을 5회 연속 돌릴 때 생기는 장기 워밍업 편향을
# 줄이려고 라운드마다 전 전략을 한 바퀴씩 돈다(④가 손으로 하던 interleaved 측정의 자동화).
# 다만 라운드 안의 전략 순서는 고정이라 순서 효과를 완전히 없앤 설계는 아니다. 이 한계는
# 통제 실행 기록과 종합 문서에 명시한다.
#
# 사용법:
#   ./gradlew bootRun --args='--spring.profiles.active=benchmark' 로 앱을 :8080 에 띄운 뒤
#   scripts/benchmark.sh --out /tmp/new-runs.csv                       # Phase A: 50회
#   scripts/benchmark.sh --phase b --out /tmp/new-runs.csv             # Phase B: 같은 CSV에 10회
#   # --out이 정본 경로가 아니면 Phase B 분포도 같은 디렉터리에 파생 이름으로 저장된다.
#   scripts/benchmark.sh --out /tmp/dry.csv --strategies conditional --rounds 1
set -uo pipefail

cd "$(dirname "$0")/.."

BASE_URL="${BASE_URL:-http://localhost:8080}"
MYSQL_CONTAINER="${MYSQL_CONTAINER:-reservation-mysql}"
DEFAULT_OUT_CSV="docs/benchmark/raw-runs.csv"
DEFAULT_ATTEMPT_DISTRIBUTION_JSON="docs/benchmark/optimistic-attempt-distribution-cap20.json"
OUT_CSV="${OUT_CSV:-$DEFAULT_OUT_CSV}"
if [[ ${ATTEMPT_DISTRIBUTION_JSON+x} == x ]]; then
  DISTRIBUTION_PATH_EXPLICIT=1
else
  DISTRIBUTION_PATH_EXPLICIT=0
  ATTEMPT_DISTRIBUTION_JSON=""
fi
ENVIRONMENT_WAIT_SECONDS="${ENVIRONMENT_WAIT_SECONDS:-60}"
ROUNDS=5
PHASE="a"
# ⑤ 재시도 상한. 앱의 @Value 로 기동 시점에 고정되므로 스크립트가 바꿀 수 없다 —
# 여기서는 CSV 에 "이 실행이 어느 상한이었는지"를 적기만 한다. 상한을 적지 않은 ⑤ 측정치는
# 해석이 불가능하다(docs/STEP2-3-BRANCH-STRATEGY.md ⑦).
OPTIMISTIC_CAP=5
STRATEGIES=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --phase)      PHASE="$2"; shift 2 ;;
    --rounds)     ROUNDS="$2"; shift 2 ;;
    --strategies) STRATEGIES="$2"; shift 2 ;;
    --out)        OUT_CSV="$2"; shift 2 ;;
    *) echo "알 수 없는 옵션: $1" >&2; exit 2 ;;
  esac
done

case "$PHASE" in
  a) OPTIMISTIC_CAP=5 ;;
  b) OPTIMISTIC_CAP=20 ;;
  *) echo "phase 는 a 또는 b" >&2; exit 2 ;;
esac

if [[ ! "$ROUNDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "rounds 는 1 이상의 정수여야 함: $ROUNDS" >&2
  exit 2
fi

if [[ -z "$STRATEGIES" ]]; then
  if [[ "$PHASE" == "a" ]]; then
    STRATEGIES="baseline unique conditional pessimistic optimistic"
  else
    # Phase B 는 ⑤ 하나만 다시 잰다. 상한만 바꾼 재측정이라 다른 전략은 Phase A 값을 쓴다.
    STRATEGIES="optimistic"
  fi
fi

if [[ "$PHASE" == "b" && "$STRATEGIES" != "optimistic" ]]; then
  echo "Phase B는 optimistic 전략의 상한 20 재측정 전용임" >&2
  exit 2
fi

if (( DISTRIBUTION_PATH_EXPLICIT == 0 )); then
  if [[ "$OUT_CSV" == "$DEFAULT_OUT_CSV" ]]; then
    ATTEMPT_DISTRIBUTION_JSON="$DEFAULT_ATTEMPT_DISTRIBUTION_JSON"
  elif [[ "$OUT_CSV" == *.csv ]]; then
    ATTEMPT_DISTRIBUTION_JSON="${OUT_CSV%.csv}-optimistic-attempt-distribution-cap20.json"
  else
    ATTEMPT_DISTRIBUTION_JSON="${OUT_CSV}-optimistic-attempt-distribution-cap20.json"
  fi
fi

# 경합 2지점. "낮음/극단"이라는 이름은 정원 경쟁 기준이며, ⑤에게는 의미가 정반대다
# (cap=100 은 같은 행에 성공적으로 쓰는 횟수가 100 번이라 낙관적 락에게 최악이고,
#  cap=1 은 쓰기가 한 번뿐이라 가장 쉽다 — docs/STEP2-OPTIMISTIC-LOCK.md 5-1절).
CONTENTION_POINTS=("low:100:120" "extreme:1:200")

mysql_q() { docker exec "$MYSQL_CONTAINER" mysql -uroot -N -B -e "$1" reservation 2>/dev/null; }

if [[ "$PHASE" == "a" && -e "$OUT_CSV" ]]; then
  echo "✗ Phase A 출력 파일이 이미 존재함: $OUT_CSV" >&2
  echo "  기존 데이터와 섞이지 않도록 새 --out 경로를 사용하세요." >&2
  exit 2
fi
if [[ "$PHASE" == "b" && ! -f "$OUT_CSV" ]]; then
  echo "✗ Phase B가 이어 쓸 Phase A CSV가 없음: $OUT_CSV" >&2
  exit 2
fi
if [[ "$PHASE" == "b" ]]; then
  if [[ -z "$ATTEMPT_DISTRIBUTION_JSON" ]]; then
    echo "✗ Phase B 분포 출력 경로가 비어 있음" >&2
    exit 2
  fi
  if [[ ! -d "$(dirname "$ATTEMPT_DISTRIBUTION_JSON")" ]]; then
    echo "✗ Phase B 분포 출력 디렉터리가 없음: $(dirname "$ATTEMPT_DISTRIBUTION_JSON")" >&2
    exit 2
  fi
  if ! python3 scripts/validate_phase_b_input.py "$OUT_CSV" --rounds "$ROUNDS"; then
    echo "✗ Phase B는 같은 rounds로 완주한 Phase A CSV에만 이어 쓸 수 있음" >&2
    exit 2
  fi
fi

verify_benchmark_environment() {
  local response validation rc second

  response=$(curl -fsS "$BASE_URL/api/metrics/benchmark-environment") || {
    echo "✗ benchmark 환경 API에 연결할 수 없음: $BASE_URL" >&2
    echo "  spring.profiles.active=benchmark 로 앱을 기동했는지 확인하세요." >&2
    return 1
  }

  for (( second = 0; second < ENVIRONMENT_WAIT_SECONDS; second++ )); do
    validation=$(printf '%s' "$response" | python3 scripts/validate_benchmark_environment.py \
      --expected-max-attempts "$OPTIMISTIC_CAP" 2>&1)
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

# CSV를 만들기 전에 검증한다. 잘못 띄운 앱의 결과가 정본 파일에 한 줄이라도 섞이면 안 된다.
verify_benchmark_environment || exit 1

if [[ ! -f "$OUT_CSV" ]]; then
  if ! echo "ts,phase,round,strategy,optimistic_max_attempts,contention,capacity,contenders,slot_id,requests,ok,ko,mean_ms,p95_ms,max_ms,tps,remaining,confirmed,overbooking,duplicates,retry_succeeded,retry_exhausted,version_conflicts,deadlocks,mean_attempts" > "$OUT_CSV"; then
    echo "✗ CSV 출력 파일을 만들 수 없음: $OUT_CSV" >&2
    exit 1
  fi
fi

total=$(( ROUNDS * $(echo "$STRATEGIES" | wc -w) * ${#CONTENTION_POINTS[@]} ))
done_n=0
rows_written=0
distribution_staging=""
cleanup_distribution_staging() {
  if [[ -n "$distribution_staging" && -f "$distribution_staging" ]]; then
    rm -f -- "$distribution_staging"
  fi
}
trap cleanup_distribution_staging EXIT
echo "▶ phase=$PHASE rounds=$ROUNDS strategies=[$STRATEGIES] → 총 $total 실행 → $OUT_CSV"

for (( round = 1; round <= ROUNDS; round++ )); do
  for point in "${CONTENTION_POINTS[@]}"; do
    IFS=':' read -r label capacity contenders <<< "$point"
    for strategy in $STRATEGIES; do
      done_n=$(( done_n + 1 ))
      printf '[%2d/%2d] round=%d %-11s %-7s cap=%-3d cont=%-3d ' \
        "$done_n" "$total" "$round" "$strategy" "$label" "$capacity" "$contenders"

      # ⑤의 재시도 분포는 누적 카운터라, 이 실행 하나의 분포만 보려면 직전에 비워야 한다.
      if ! curl -fsS -X DELETE "$BASE_URL/api/metrics/optimistic-retries" > /dev/null; then
        echo "✗ 낙관적 재시도 통계 초기화 실패 — 측정을 중단함" >&2
        exit 1
      fi

      # 시뮬레이션이 만든 슬롯 id 를 밖으로 내보내지 않으므로, 실행 전후의 최대 id 차이로
      # "이번 실행이 만든 슬롯"을 특정한다. 스윕이 순차 실행이라 이 방식이 성립한다.
      if ! before_slot=$(mysql_q "SELECT COALESCE(MAX(id), 0) FROM interview_slot"); then
        echo "✗ 실행 전 슬롯 조회 실패 — 측정을 중단함" >&2
        exit 1
      fi
      if [[ ! "$before_slot" =~ ^[0-9]+$ ]]; then
        echo "✗ 실행 전 슬롯 id가 올바르지 않음: $before_slot" >&2
        exit 1
      fi

      start_ns=$(date +%s)
      ./gradlew gatlingRun --console=plain -q \
        -DbaseUrl="$BASE_URL" -Dstrategy="$strategy" \
        -Dcapacity="$capacity" -Dcontenders="$contenders" \
        > /tmp/gatling-run.log 2>&1
      rc=$?
      elapsed=$(( $(date +%s) - start_ns ))

      if [[ $rc -ne 0 ]]; then
        echo "✗ gatlingRun 실패(rc=$rc) — 부분 CSV를 완료로 취급하지 않고 중단함" >&2
        tail -5 /tmp/gatling-run.log >&2
        exit 1
      fi

      if ! slot_id=$(mysql_q "SELECT MAX(id) FROM interview_slot"); then
        echo "✗ 실행 후 슬롯 조회 실패 — 측정을 중단함" >&2
        exit 1
      fi
      if [[ ! "$slot_id" =~ ^[0-9]+$ || "$slot_id" -le "$before_slot" ]]; then
        echo "✗ 새 슬롯이 생기지 않음 — 시드 실패로 보고 중단함" >&2
        exit 1
      fi

      if ! report_dir=$(ls -dt build/reports/gatling/*/ 2>/dev/null | head -1) \
          || [[ -z "$report_dir" ]]; then
        echo "✗ Gatling 리포트 디렉터리를 찾지 못함 — 측정을 중단함" >&2
        exit 1
      fi
      if ! stats=$(python3 scripts/parse_gatling_report.py \
          "$report_dir" "$strategy" "$capacity" "$contenders") \
          || [[ -z "$stats" ]]; then
        echo "✗ 리포트 파싱 실패: $report_dir — 측정을 중단함" >&2
        exit 1
      fi
      IFS=',' read -r requests ok ko mean_ms p95_ms max_ms tps <<< "$stats"

      # 정합성은 리포트가 아니라 DB 에서 읽는다. 오버부킹은 HTTP 응답이 아니라 확정 행 수로만
      # 증명되기 때문이다(1단계부터 지켜 온 원칙).
      if ! confirmed=$(mysql_q "SELECT COUNT(*) FROM reservation WHERE slot_id = $slot_id AND status = 'CONFIRMED'") \
          || ! remaining=$(mysql_q "SELECT remaining FROM interview_slot WHERE id = $slot_id") \
          || ! duplicates=$(mysql_q "SELECT COALESCE(SUM(c - 1), 0) FROM (SELECT COUNT(*) c FROM reservation WHERE slot_id = $slot_id GROUP BY applicant_id, slot_id HAVING COUNT(*) > 1) d"); then
        echo "✗ 정합성 DB 조회 실패 — 측정을 중단함" >&2
        exit 1
      fi
      if [[ ! "$confirmed" =~ ^[0-9]+$ || ! "$remaining" =~ ^-?[0-9]+$ \
          || ! "$duplicates" =~ ^[0-9]+$ ]]; then
        echo "✗ 정합성 DB 값이 올바르지 않음 — 측정을 중단함" >&2
        exit 1
      fi
      overbooking=$(( confirmed > capacity ? confirmed - capacity : 0 ))

      if ! retry_json=$(curl -fsS "$BASE_URL/api/metrics/optimistic-retries"); then
        echo "✗ 낙관적 재시도 통계 조회 실패 — CSV에 기록하지 않고 중단함" >&2
        exit 1
      fi
      if ! retry_stats=$(printf '%s' "$retry_json" | python3 scripts/parse_optimistic_metrics.py); then
        echo "✗ 낙관적 재시도 통계 검증 실패 — CSV에 기록하지 않고 중단함" >&2
        exit 1
      fi
      IFS=',' read -r r_ok r_exhausted r_conflicts r_deadlocks r_mean <<< "$retry_stats"

      if [[ "$PHASE" == "b" && "$strategy" == "optimistic" && "$label" == "low" ]]; then
        # 마지막 낮은 경합 관측을 staging에 보관하고 Phase B 전체가 완주한 뒤에만 공개한다.
        distribution_staging="${ATTEMPT_DISTRIBUTION_JSON}.tmp.$$"
        if ! printf '%s\n' "$retry_json" > "$distribution_staging"; then
          echo "✗ 낙관적 재시도 분포 staging 실패 — 측정을 중단함" >&2
          exit 1
        fi
      fi

      if ! printf '%s,%s,%d,%s,%d,%s,%d,%d,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%d,%s,%s,%s,%s,%s,%s\n' \
        "$(date +%Y-%m-%dT%H:%M:%S)" "$PHASE" "$round" "$strategy" "$OPTIMISTIC_CAP" \
        "$label" "$capacity" "$contenders" "$slot_id" \
        "$requests" "$ok" "$ko" "$mean_ms" "$p95_ms" "$max_ms" "$tps" \
        "$remaining" "$confirmed" "$overbooking" "$duplicates" \
        "$r_ok" "$r_exhausted" "$r_conflicts" "$r_deadlocks" "$r_mean" >> "$OUT_CSV"; then
        echo "✗ CSV 행 기록 실패 — 측정을 중단함" >&2
        exit 1
      fi
      rows_written=$(( rows_written + 1 ))

      printf '✓ %3ss  ok=%-3s ko=%-3s mean=%-4sms tps=%-6s 확정=%-3s 오버부킹=%s\n' \
        "$elapsed" "$ok" "$ko" "$mean_ms" "$tps" "$confirmed" "$overbooking"
    done
  done
done

if (( rows_written != total )); then
  echo "✗ 예정한 $total행 중 ${rows_written}행만 기록됨 — 완료로 표시하지 않음" >&2
  exit 1
fi
if [[ "$PHASE" == "b" ]]; then
  if [[ -z "$distribution_staging" || ! -s "$distribution_staging" ]]; then
    echo "✗ Phase B 분포 결과가 없어 완료할 수 없음" >&2
    exit 1
  fi
  if ! mv "$distribution_staging" "$ATTEMPT_DISTRIBUTION_JSON"; then
    echo "✗ 낙관적 재시도 분포 게시 실패 — 완료로 표시하지 않음" >&2
    exit 1
  fi
  distribution_staging=""
  echo "▶ 재시도 분포 — $ATTEMPT_DISTRIBUTION_JSON"
fi
echo "▶ 완료 — $OUT_CSV"
