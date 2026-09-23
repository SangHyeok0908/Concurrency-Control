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
# 실행 순서는 라운드 단위 인터리브다. 한 전략을 5회 연속 돌리면 JIT·InnoDB 버퍼풀이 그 전략에만
# 유리하게 데워져, 나중에 도는 전략이 구조적으로 손해를 본다. 라운드마다 전 전략을 한 바퀴씩
# 돌리면 그 편향이 전략 사이에 고르게 퍼진다(④가 손으로 하던 interleaved 측정의 자동화).
#
# 사용법:
#   ./gradlew bootRun 으로 앱을 :8080 에 띄운 뒤
#   scripts/benchmark.sh                        # Phase A: ⑤ 상한 5 기준, 5개 전략
#   scripts/benchmark.sh --phase b              # Phase B: ⑤ 상한 20 (앱을 그 설정으로 재기동한 뒤)
#   scripts/benchmark.sh --strategies conditional --rounds 1   # dry-run
set -uo pipefail

cd "$(dirname "$0")/.."

BASE_URL="${BASE_URL:-http://localhost:8080}"
MYSQL_CONTAINER="${MYSQL_CONTAINER:-reservation-mysql}"
OUT_CSV="${OUT_CSV:-docs/benchmark/raw-runs.csv}"
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

if [[ -z "$STRATEGIES" ]]; then
  case "$PHASE" in
    a) STRATEGIES="baseline unique conditional pessimistic optimistic" ;;
    # Phase B 는 ⑤ 하나만 다시 잰다. 상한만 바꾼 재측정이라 다른 전략은 Phase A 값을 쓴다.
    b) STRATEGIES="optimistic"; OPTIMISTIC_CAP=20 ;;
    *) echo "phase 는 a 또는 b" >&2; exit 2 ;;
  esac
fi

# 경합 2지점. "낮음/극단"이라는 이름은 정원 경쟁 기준이며, ⑤에게는 의미가 정반대다
# (cap=100 은 같은 행에 성공적으로 쓰는 횟수가 100 번이라 낙관적 락에게 최악이고,
#  cap=1 은 쓰기가 한 번뿐이라 가장 쉽다 — docs/STEP2-OPTIMISTIC-LOCK.md 5-1절).
CONTENTION_POINTS=("low:100:120" "extreme:1:200")

mysql_q() { docker exec "$MYSQL_CONTAINER" mysql -uroot -N -B -e "$1" reservation 2>/dev/null; }

if [[ ! -f "$OUT_CSV" ]]; then
  echo "ts,phase,round,strategy,optimistic_max_attempts,contention,capacity,contenders,slot_id,requests,ok,ko,mean_ms,p95_ms,max_ms,tps,remaining,confirmed,overbooking,duplicates,retry_succeeded,retry_exhausted,version_conflicts,deadlocks,mean_attempts" > "$OUT_CSV"
fi

total=$(( ROUNDS * $(echo "$STRATEGIES" | wc -w) * ${#CONTENTION_POINTS[@]} ))
done_n=0
echo "▶ phase=$PHASE rounds=$ROUNDS strategies=[$STRATEGIES] → 총 $total 실행 → $OUT_CSV"

for (( round = 1; round <= ROUNDS; round++ )); do
  for point in "${CONTENTION_POINTS[@]}"; do
    IFS=':' read -r label capacity contenders <<< "$point"
    for strategy in $STRATEGIES; do
      done_n=$(( done_n + 1 ))
      printf '[%2d/%2d] round=%d %-11s %-7s cap=%-3d cont=%-3d ' \
        "$done_n" "$total" "$round" "$strategy" "$label" "$capacity" "$contenders"

      # ⑤의 재시도 분포는 누적 카운터라, 이 실행 하나의 분포만 보려면 직전에 비워야 한다.
      curl -s -X DELETE "$BASE_URL/api/metrics/optimistic-retries" > /dev/null

      # 시뮬레이션이 만든 슬롯 id 를 밖으로 내보내지 않으므로, 실행 전후의 최대 id 차이로
      # "이번 실행이 만든 슬롯"을 특정한다. 스윕이 순차 실행이라 이 방식이 성립한다.
      before_slot=$(mysql_q "SELECT COALESCE(MAX(id), 0) FROM interview_slot")

      start_ns=$(date +%s)
      ./gradlew gatlingRun --console=plain -q \
        -Dstrategy="$strategy" -Dcapacity="$capacity" -Dcontenders="$contenders" \
        > /tmp/gatling-run.log 2>&1
      rc=$?
      elapsed=$(( $(date +%s) - start_ns ))

      if [[ $rc -ne 0 ]]; then
        echo "✗ gatlingRun 실패(rc=$rc) — /tmp/gatling-run.log 확인"
        tail -5 /tmp/gatling-run.log
        continue
      fi

      slot_id=$(mysql_q "SELECT MAX(id) FROM interview_slot")
      if [[ "$slot_id" == "$before_slot" ]]; then
        echo "✗ 새 슬롯이 생기지 않음 — 시드 실패로 보고 건너뜀"
        continue
      fi

      report_dir=$(ls -dt build/reports/gatling/*/ 2>/dev/null | head -1)
      stats=$(python3 scripts/parse_gatling_report.py "$report_dir" "$strategy" "$capacity" "$contenders")
      if [[ -z "$stats" ]]; then
        echo "✗ 리포트 파싱 실패: $report_dir"
        continue
      fi
      IFS=',' read -r requests ok ko mean_ms p95_ms max_ms tps <<< "$stats"

      # 정합성은 리포트가 아니라 DB 에서 읽는다. 오버부킹은 HTTP 응답이 아니라 확정 행 수로만
      # 증명되기 때문이다(1단계부터 지켜 온 원칙).
      confirmed=$(mysql_q "SELECT COUNT(*) FROM reservation WHERE slot_id = $slot_id AND status = 'CONFIRMED'")
      remaining=$(mysql_q "SELECT remaining FROM interview_slot WHERE id = $slot_id")
      duplicates=$(mysql_q "SELECT COALESCE(SUM(c - 1), 0) FROM (SELECT COUNT(*) c FROM reservation WHERE slot_id = $slot_id GROUP BY applicant_id, slot_id HAVING COUNT(*) > 1) d")
      overbooking=$(( confirmed > capacity ? confirmed - capacity : 0 ))

      retry_json=$(curl -s "$BASE_URL/api/metrics/optimistic-retries")
      IFS=',' read -r r_ok r_exhausted r_conflicts r_deadlocks r_mean <<< \
        "$(python3 -c "
import json,sys
d = json.loads(sys.argv[1])
print('%s,%s,%s,%s,%s' % (d['succeeded'], d['retryExhausted'], d['versionConflicts'], d['deadlocks'], d['meanAttemptsPerSuccess']))
" "$retry_json")"

      printf '%s,%s,%d,%s,%d,%s,%d,%d,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%d,%s,%s,%s,%s,%s,%s\n' \
        "$(date +%Y-%m-%dT%H:%M:%S)" "$PHASE" "$round" "$strategy" "$OPTIMISTIC_CAP" \
        "$label" "$capacity" "$contenders" "$slot_id" \
        "$requests" "$ok" "$ko" "$mean_ms" "$p95_ms" "$max_ms" "$tps" \
        "$remaining" "$confirmed" "$overbooking" "$duplicates" \
        "$r_ok" "$r_exhausted" "$r_conflicts" "$r_deadlocks" "$r_mean" >> "$OUT_CSV"

      printf '✓ %3ss  ok=%-3s ko=%-3s mean=%-4sms tps=%-6s 확정=%-3s 오버부킹=%s\n' \
        "$elapsed" "$ok" "$ko" "$mean_ms" "$tps" "$confirmed" "$overbooking"
    done
  done
done

echo "▶ 완료 — $OUT_CSV"
