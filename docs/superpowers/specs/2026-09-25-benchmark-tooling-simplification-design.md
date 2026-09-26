# 벤치마크 도구 단순화 설계

- 작성일: 2026-09-25
- 상태: 사용자 검토 대기
- 대상 브랜치: `step2/benchmark-methodology-v2`
- 대체 대상: `2026-09-24-benchmark-order-and-throughput-design.md`의 실행 도구 구조

## 1. 목적

이 프로젝트의 핵심은 벤치마크 플랫폼을 만드는 것이 아니라, 같은 예약 부하에서 동시성 방어 전략의
정확성과 성능을 반복 측정해 선택 근거를 남기는 것이다. 실행 도구는 프로젝트 작성자가 직접 설명하고
수정할 수 있을 정도로 작아야 한다.

현재 방법론 v2는 실행 순서 편향, 잘못된 TPS 분모, Phase 간 비교 오염을 바로잡았지만, 로컬 단일
사용자 실험에 필요하지 않은 파일 게시 경쟁·심볼릭 링크 교체·개별 I/O 장애까지 방어하면서 실행 코드
12개와 대규모 장애 주입 테스트로 확장됐다.

이번 변경은 올바른 실험 설계는 유지하고 구현만 단순화한다.

## 2. 성공 조건

1. 정원 경쟁 벤치마크는 한 실행기와 한 요약기로 이해할 수 있어야 한다.
2. 5개 전략 × 2개 경합 조건의 10개 조합은 10라운드 동안 위치 편향 없이 실행된다.
3. Phase A는 낙관적 재시도 상한 5, Phase B는 상한 20인 별도 앱 기동에서 모든 전략을 측정한다.
4. TPS는 예약 요청의 첫 시작부터 마지막 종료까지 실제 버스트 시간으로 계산한다.
5. 실행 결과에는 HTTP 지표, DB 정합성, 낙관적 재시도 지표가 남는다.
6. 누락·중복된 조합, 잘못된 요청 수·TPS·오버부킹 계산은 공개 요약 전에 거부한다.
7. 기존 방법론 v2의 성공 캠페인과 문서화된 결론은 보존한다.
8. `./gradlew check`가 단순화된 스크립트 테스트를 포함해 통과한다.

## 3. 비범위

- 여러 사용자가 동시에 같은 결과 파일을 게시하는 상황
- 공격자가 출력 경로나 임시 파일을 심볼릭 링크로 교체하는 상황
- `fdopen`, `flush`, `fsync`, `close` 각각의 장애 주입
- 실패한 실행을 여러 상태의 CSV 행으로 영구 보존하는 기능
- 완성된 CSV를 별도 정본 경로로 원자적으로 승격하는 기능
- 과거 v1 요약을 바이트 단위로 재현하는 호환 모드
- 실행 중단 지점부터 이어서 재개하는 기능

실패 시 실행기는 즉시 종료하고, 이미 기록된 부분 CSV와 한 줄 오류를 남긴다. 공개 요약기는 완전한
캠페인만 받아들이므로 부분 결과가 최종 결론에 섞이지 않는다.

## 4. 목표 구조

최종적으로 활성 실행 도구를 다음 세 파일로 줄인다.

```text
scripts/
├── benchmark_capacity.py
├── benchmark_duplicates.py
└── summarize_benchmark.py
```

현재 PR 브랜치에는 이후 `master`에서 별도로 완성된 동일 요청 벤치마크가 포함돼 있지 않다. 이 PR에서는
정원 경쟁 도구를 `benchmark_capacity.py`와 `summarize_benchmark.py`로 먼저 단순화한다. 브랜치가
통합된 뒤 `benchmark-duplicates.sh`와 `duplicate_benchmark.py`를 `benchmark_duplicates.py` 하나로
합친다. 로컬 `master`의 중복 요청 작업을 이 PR에 억지로 복사하거나 재작성하지 않는다.

### 4.1 `benchmark_capacity.py`

다음 책임을 한 파일 안의 짧은 함수들로 제공한다.

- 명령행 인자 검사
- 10개 조합의 Williams 실행 순서 생성
- benchmark 환경 API 검증과 Phase별 환경 JSON 저장
- Phase 시작 시 10개 조합 워밍업
- Gatling 실행과 새 리포트 선택
- Gatling 통계·버스트 sentinel 파싱
- MySQL 정합성 조회
- 낙관적 재시도 통계 파싱
- CSV 행 기록
- Phase A 완료 및 Phase A+B 완료 여부 검사

명령은 기존처럼 앱을 자동 시작하거나 종료하지 않는다.

```bash
python3 scripts/benchmark_capacity.py \
  --campaign-id ID --phase a --rounds 10 --out PATH

python3 scripts/benchmark_capacity.py \
  --campaign-id ID --phase b --rounds 10 --out PATH

python3 scripts/benchmark_capacity.py \
  --campaign-id ID --phase a --rounds 10 --print-plan
```

Phase A는 존재하지 않는 출력 파일만 만들고 Phase B는 완전한 Phase A에만 이어 쓴다. 출력 파일을
다른 정본 경로로 승격하지 않는다. 사용자가 지정한 새 파일 자체가 해당 캠페인의 결과다.
환경 스냅샷은 출력 파일 옆의 `<OUT>.phase-a-environment.json`과
`<OUT>.phase-b-environment.json`에 저장하며 기존 파일을 덮어쓰지 않는다.

### 4.2 Gatling 버스트 시간

`BaselineReservationSimulation`은 바이너리 `simulation.log`를 외부에서 해독하지 않는다.

- 각 예약 요청 직전에 `AtomicLong`으로 최소 시작 시각을 기록한다.
- 각 예약 요청 직후에 최대 종료 시각과 완료 수를 기록한다.
- `Simulation.after()`에서 한 개의 JSON sentinel을 표준 출력으로 남긴다.

```text
BENCHMARK_BURST_METRICS={"requests":200,"startEpochMs":...,"endEpochMs":...,"wallMs":...}
```

실행기는 이 값으로 `requests * 1000 / wallMs`를 계산한다. 평균·p95·최대 응답시간과 OK/KO는
`stats.js`에서 읽고, sentinel의 요청 수와 교차 확인한다. 이 방식은 현재
`DuplicateReservationSimulation.after()`가 상태 집계를 출력하는 방식과 같다.

이에 따라 `gatling_binary_log.py`, 바이너리 fixture와 해당 전용 테스트를 제거한다.

### 4.3 결과 형식

이미 체크인된 방법론 v2 성공 캠페인의 원시 CSV와 환경 스냅샷은 수정하지 않는다. 단순 실행기는
기존 성공 캠페인과 호환되도록 현재 v2 CSV 헤더를 그대로 사용한다. 따라서 기존 200행 정본도 새
요약기로 다시 읽을 수 있고, 데이터를 변환하거나 재측정하기 전까지 현재 결론을 유지할 수 있다.

```text
schema_version,campaign_id,phase,app_start_id,environment_sha256,
schedule_version,schedule_cycle,schedule_row,measured_round,
position_in_round,treatment_id,ts,strategy,optimistic_max_attempts,
contention,capacity,contenders,slot_id,requests,ok,ko,mean_ms,p95_ms,
max_ms,burst_start_epoch_ms,burst_end_epoch_ms,burst_wall_ms,tps,
remaining,confirmed,overbooking,duplicates,retry_succeeded,
retry_exhausted,version_conflicts,deadlocks,mean_attempts,run_status,
error_reason
```

새 실행기는 성공한 실행만 CSV에 기록하므로 `run_status`는 항상 `ok`, `error_reason`은 항상 빈 값이다.
실패 행의 단계별 필드 조합을 지원하지 않는다. 나머지 계획·환경 필드는 실행기가 이미 알고 있는 값을
그대로 기록할 뿐, 별도 261줄 스키마 계층을 두지 않는다.

필수 결과는 다음 네 묶음이다.

- 실행 조건: campaign, phase, round, position, strategy, contention, capacity, contenders
- HTTP 결과: requests, ok, ko, mean, p95, max, burst wall, TPS
- DB 결과: slot, remaining, confirmed, overbooking, duplicates
- 낙관적 결과: succeeded, exhausted, version conflicts, deadlocks, mean attempts

환경 API 원문은 Phase별 JSON 한 번만 저장한다. 해당 파일의 SHA-256은 기존 데이터 호환을 위해 각
행의 `environment_sha256`에 기록하지만, 원자적 스냅샷 게시나 파일 교체 공격까지 방어하지 않는다.

### 4.4 `summarize_benchmark.py`

요약기는 다음 핵심 검증만 수행한다.

- 하나의 캠페인
- 모든 행의 `run_status=ok`
- Phase A/B의 재시도 상한 5/20과 Phase별 단일 환경 해시
- Phase별 연속된 10라운드 블록
- 매 라운드 10개 조합과 위치가 각각 한 번
- 10라운드 동안 각 조합이 각 위치에 한 번
- 모든 행의 `requests == contenders == ok + ko`
- `overbooking == max(confirmed - capacity, 0)`
- `tps == round(requests * 1000 / burst_wall_ms, 1)`

검증 후 Phase별 전략 표, 중앙값과 min–max, 오버부킹 최댓값, 조건부 UPDATE와 비관적 락의
라운드별 비교, 낙관적 재시도 표와 Mermaid 차트를 출력한다.

v1 바이트 호환 렌더러와 여러 캠페인을 섞은 진단 모드는 제거한다. 기존 v1 CSV와 실행 문서는 역사
자료로 남지만 활성 요약 코드가 이를 계속 지원하지 않는다.

## 5. 제거 대상

정원 경쟁 v2에서 다음 파일을 제거하고 기능을 위 두 파일로 흡수한다.

- `scripts/benchmark.sh`
- `scripts/benchmark_schedule.py`
- `scripts/benchmark_schema.py`
- `scripts/gatling_binary_log.py`
- `scripts/parse_gatling_report.py`
- `scripts/parse_optimistic_metrics.py`
- `scripts/promote_benchmark_campaign.py`
- `scripts/validate_benchmark_campaign.py`
- `scripts/validate_benchmark_environment.py`
- `scripts/validate_phase_b_input.py`
- `scripts/write_benchmark_row.py`

각 파일에 대응하던 테스트도 제거하고, 사용자에게 의미 있는 동작을 검증하는 집중된 테스트로
대체한다.

## 6. 테스트 범위

`test_benchmark_capacity.py`는 다음을 확인한다.

- Williams 10라운드 위치 균형과 결정성
- 잘못된 라운드 수 거부
- Gatling sentinel·`stats.js` 요청 수 불일치 거부
- Phase A 출력 파일 덮어쓰기 거부
- 완전하지 않은 Phase A 뒤의 Phase B 거부
- Phase별 환경 상한 불일치 거부
- 성공 행의 TPS와 오버부킹 계산

`test_summarize_benchmark.py`는 다음을 확인한다.

- 완전한 캠페인만 요약
- 조합 누락·중복·불균형 거부
- 요청 수·TPS·오버부킹 식 위반 거부
- 중앙값·min–max·짝비교·재시도 표 생성

파일 시스템 공격과 개별 저수준 I/O 장애 주입 테스트는 만들지 않는다.

## 7. 문서와 빌드

- `build.gradle`의 `benchmarkScriptTest`는 새 집중 테스트만 실행한다.
- 재현 명령은 `benchmark_capacity.py` 기준으로 갱신한다.
- 기존 과설계 구현을 지시한 2026-09-24 spec·plan은 새 설계로 대체되므로 제거한다.
- 기존 성공 캠페인 데이터와 현재 포트폴리오 결론은 유지한다.
- 새 실행기로 실제 캠페인을 다시 측정하기 전에는 기존 수치를 바꾸지 않는다.

## 8. 완료 기준

1. 활성 정원 경쟁 실행 도구가 `benchmark_capacity.py`와 `summarize_benchmark.py` 두 파일이다.
2. `BaselineReservationSimulation`이 버스트 sentinel을 출력한다.
3. 제거 대상 모듈과 저수준 장애 주입 테스트가 남아 있지 않다.
4. 새 단위 테스트와 `./gradlew check`가 통과한다.
5. 기존 원시 데이터와 문서 수치가 의도치 않게 변경되지 않는다.
6. 변경 내용은 사용자 승인 전 커밋하지 않는다.
