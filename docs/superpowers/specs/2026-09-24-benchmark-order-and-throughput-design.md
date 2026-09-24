# 벤치마크 순서 효과·처리량 지표 개선 설계

- 작성일: 2026-09-24
- 상태: 사용자 검토 대기
- 기준 커밋: `dbb3d67` (`docs: record controlled benchmark rerun`)

## 1. 목적

현재 벤치마크는 애플리케이션 설정 비용을 통제하지만, 실행 순서와 처리량 계산에는 다음 문제가
남아 있다.

1. Phase A의 실행 순서가 매 라운드 `low → extreme`, 각 경합 안에서
   `baseline → unique → conditional → pessimistic → optimistic`으로 고정된다. 전략과 경합 지점이
   직렬 위치, JVM 워밍업, 캐시 상태, 누적 DB 상태와 결합된다.
2. Phase B는 `optimistic@20`만 실행한다. 따라서 앞선 JVM에서 측정한 Phase A 대조군과 직접
   비교하면 앱 재시작과 시간 경과가 섞인다.
3. TPS를 `요청 수 / 최대 개별 응답시간`으로 계산한다. 최대 개별 응답시간은 최초 요청 시작부터
   최종 요청 종료까지의 버스트 벽시계 구간이 아니므로 처리량의 분모가 될 수 없다.
4. 현재 CSV에는 캠페인, 실행 위치, 환경 스냅샷 식별자가 없다. 여러 캠페인을 붙이면 집계기가
   서로 다른 실행을 조용히 합칠 수 있다.

이 설계의 목적은 전략 구현을 바꾸지 않고 측정 방법만 교정해, 순서 효과가 균형화되고 TPS가 실제
요청 타임스탬프에서 계산되며 모든 수치가 하나의 완전한 캠페인까지 추적되게 하는 것이다.

## 2. 전제와 범위

### 2.1 재사용할 완료 산출물

커밋 `adaf6e2`와 `dbb3d67`에서 해결한 애플리케이션 설정 통제를 그대로 재사용한다.

- `application-benchmark.yml`: Hikari maximum/minimum-idle 100, 애플리케이션·SQL 로그 OFF
- `BenchmarkEnvironmentController`: 유효 프로필, 풀 상태, 로그, 백오프, 재시도 상한 공개
- `validate_benchmark_environment.py`: 측정 전 fail-fast 검증
- `docs/benchmark/raw-runs.csv`: 2026-09-24 설정 통제 60행
- `docs/benchmark/2026-09-24-controlled-run.md`: 환경과 기존 산출물 해시

새 프로필이나 환경 엔드포인트를 만들지 않는다. 기존 60행은 `methodology-v1-fixed-order` 증거로
보존하고 덮어쓰지 않는다.

### 2.2 비범위

- 의도적으로 락이 없는 baseline 구현을 수정하지 않는다.
- 방어 전략의 SQL, 트랜잭션, 재시도 정책을 바꾸지 않는다.
- Redisson, 분산 락, 별도 멱등성 키를 도입하지 않는다.
- 운영 환경의 절대 성능이나 다른 하드웨어로 결과를 일반화하지 않는다.
- 데이터베이스를 파괴적으로 초기화하지 않는다. 캠페인 시작 시 DB 행 수와 서버 설정을 기록하고,
  균형 순서로 실행 중 드리프트를 분산한다.

## 3. 채택 설계

### 3.1 측정 단위

하나의 처리(treatment)는 `전략 × 경합 지점`이다. 다섯 전략과 두 경합 지점으로 10개를 만든다.

| ID | 처리 | ID | 처리 |
|---:|---|---:|---|
| 0 | `baseline-low` | 5 | `baseline-extreme` |
| 1 | `unique-low` | 6 | `unique-extreme` |
| 2 | `conditional-low` | 7 | `conditional-extreme` |
| 3 | `pessimistic-low` | 8 | `pessimistic-extreme` |
| 4 | `optimistic-low` | 9 | `optimistic-extreme` |

전략만 회전시키지 않고 경합까지 처리에 포함한다. 그래야 `low`가 항상 먼저 실행되는 편향도 함께
제거된다.

### 3.2 Williams 균형 순서

10개 처리에 대해 다음 첫 행을 사용하는 결정적 Williams 설계를 채택한다.

```text
B = [0, 1, 9, 2, 8, 3, 7, 4, 6, 5]
W[r][position] = (B[position] + r) mod 10
```

첫 측정 라운드는 다음 순서다.

```text
baseline-low, unique-low, optimistic-extreme, conditional-low,
pessimistic-extreme, pessimistic-low, conditional-extreme,
optimistic-low, unique-extreme, baseline-extreme
```

완전한 10라운드 블록마다 다음 조건을 만족해야 한다.

- 각 처리가 각 직렬 위치에 정확히 한 번 나타난다.
- 경합을 합치면 각 전략이 각 위치에 두 번 나타난다.
- 각 위치에는 `low`와 `extreme`이 각각 다섯 번 나타난다.
- 라운드 내부에서 서로 다른 두 처리의 모든 방향성 인접 쌍이 한 번씩 나타난다. 라운드 경계의
  선행 효과까지 균형화했다고 주장하지 않는다.

기본 측정 라운드는 10이다. 공개 결과를 만드는 `--rounds`는 양의 10의 배수만 허용한다. 20라운드는
완전한 블록을 두 번 반복하며 `schedule_cycle`로 구분한다. 동일 입력의 계획은 항상 같은 바이트열을
출력해야 한다. `--print-plan`은 앱이나 DB에 접근하지 않고 실행 계획만 출력한다.

### 3.3 Phase와 비교 범위

하나의 캠페인은 앱을 두 번 기동한다.

- Phase A: 검증된 `optimisticMaxAttempts=5`, 5전략 × 2경합 × 10라운드 = 100행
- Phase B: 검증된 `optimisticMaxAttempts=20`, 5전략 × 2경합 × 10라운드 = 100행

두 Phase 모두 같은 Williams 계획으로 모든 전략을 실행한다. 재시도 상한을 사용하지 않는 네 전략도
Phase B에서 다시 실행해, `optimistic@20`의 대조군을 같은 JVM 기동·시간대에 둔다.

- `optimistic@5`는 Phase A 대조군과 비교한다.
- `optimistic@20`은 Phase B 대조군과 비교한다.
- `optimistic@5`와 `optimistic@20`의 차이는 서로 다른 앱 기동 사이의 민감도 분석으로만 보고한다.
  동일 프로세스의 짝비교라고 표현하지 않는다.
- 하나의 캠페인 안에서 Phase 순서는 A→B로 고정하되, 모든 전략 비교는 Phase 안에서 끝낸다. 향후
  캠페인을 반복할 때만 전체 Phase 순서를 B→A로 교대할 수 있다.

각 Phase의 환경 검증이 끝난 뒤, 측정 전에 같은 첫 Williams 행의 10개 처리를 한 번씩 워밍업한다.
워밍업은 CSV에 넣지 않고 캠페인 매니페스트에 성공 여부만 기록한다. 한 전략만 선택적으로
워밍업하지 않는다.

### 3.4 실제 버스트 TPS

`stats.js`는 요청 수, OK/KO, 평균, p95, 최대 응답시간을 읽는 데 계속 사용한다. TPS만 Gatling
3.13.5의 `simulation.log`에 기록된 개별 요청 시각으로 계산한다.

```text
burst_start_epoch_ms = min(matched request start)
burst_end_epoch_ms   = max(matched request end)
burst_wall_ms        = burst_end_epoch_ms - burst_start_epoch_ms
tps                  = requests * 1000 / burst_wall_ms
```

파서는 정확한 요청 이름 `reserve [strategy cap=N cont=M]`과 일치하는 OK·KO 요청을 모두 포함한다.
시드 요청과 다른 이름의 요청은 제외한다. `atOnceUsers`라도 시작 시각이 완전히 같다고 가정하지 않는다.

이 프로젝트의 Gatling 3.13.5 `simulation.log`는 바이너리다. 파서는 고정된 3.13.5 형식의 실행 헤더와
요청 레코드를 읽고, 사용자·그룹·오류 레코드를 건너뛸 때도 공유 문자열 캐시를 유지한다. 실행기는
Gatling 프로세스가 0으로 끝나고 HTML 통계가 생성된 경우에만 파서를 호출한다. 파서는 다음 조건을
하나라도 만족하지 못하면 측정을 실패시킨다.

- 지원하는 로그 버전이 아니다.
- 실행 헤더나 레코드 중간에서 EOF를 만났거나 레코드가 손상됐다.
- 정확한 요청 이름의 레코드 수가 `contenders`와 다르다.
- 요청 종료가 시작보다 이르거나 버스트 구간이 0 이하이다.
- 바이너리 로그의 OK/KO/전체 수와 `stats.js` 수가 다르다.

Gatling 로그에는 파일 종료 마커나 체크섬이 없으므로, 온전한 레코드 경계에서 잘린 파일 자체를
판별한다고 주장하지 않는다. 대신 Gatling 정상 종료, 정확한 요청 수, `stats.js` 교차 검증을 함께
요구해 측정에 필요한 요청 레코드가 빠진 경우를 거부한다.

CSV에는 원시 시작·종료 시각과 `burst_wall_ms`를 함께 남겨 TPS를 재계산할 수 있게 한다.

### 3.5 환경 증거

현재 환경 API와 검증기를 그대로 사용하되 성공 여부만 콘솔에 남기지 않는다. 각 앱 기동마다 API
응답을 키 정렬된 정규 JSON으로 저장하고 SHA-256을 계산한다.

- `app_start_id`: 캠페인 안에서 앱 기동을 식별한다.
- `environment_sha256`: 해당 기동에서 받은 정규 API 응답 JSON의 해시다.
- Phase A의 모든 행은 상한 5 환경 해시 하나를 공유한다.
- Phase B의 모든 행은 상한 20 환경 해시 하나를 공유한다.

실행기는 환경 검증에 성공할 때 `app_start_id=<campaign_id>-phase-<a|b>`를 부여한다. 이 값은 OS
프로세스 ID를 증명하려는 값이 아니라, 검증된 Phase 실행을 CSV와 매니페스트 사이에서 조인하는
식별자다. 정규 응답은
`docs/benchmark/campaigns/<campaign_id>/environment-phase-<a|b>.json`, 매니페스트는
`docs/benchmark/campaigns/<campaign_id>/manifest.md`에 저장한다.

캠페인 매니페스트에는 환경 JSON 경로와 해시뿐 아니라 Git HEAD, OS·Java·Gradle·Gatling·MySQL
버전, MySQL `max_connections`, 측정 시작 시 슬롯·예약 행 수, 워밍업 결과를 남긴다. 호스트 부하를
완전히 통제했다고 주장하지 않고, 관측 가능한 전제만 기록한다.

## 4. v2 원시 데이터

캠페인은 먼저 `docs/benchmark/campaigns/<campaign_id>/raw-runs.csv`에 기록한다. 두 Phase의 200행을
완주하고 v2 검증기를 통과한 파일만 `docs/benchmark/raw-runs-v2.csv`로 원자적으로 승격한다. 실패한
캠페인의 행과 매니페스트는 해당 캠페인 디렉터리에 남으므로 새 ID의 재실행이나 정본을 막지 않는다.
기존 정본이 있으면 자동으로 덮어쓰지 않고 별도의 아카이브 단계를 요구한다.

기존 `raw-runs.csv`의 행과 해시는 수정하지 않는다. 2026-09-24 실행 기록도 당시 실행의 불변 기록으로
남기고, 새 v2 매니페스트에서 이를 선행 방법론으로 연결한다.

v2 CSV는 다음 필드를 갖는다.

```text
schema_version,campaign_id,phase,app_start_id,environment_sha256,
schedule_version,schedule_cycle,schedule_row,measured_round,
position_in_round,treatment_id,ts,strategy,optimistic_max_attempts,
contention,capacity,contenders,slot_id,requests,ok,ko,
mean_ms,p95_ms,max_ms,burst_start_epoch_ms,burst_end_epoch_ms,
burst_wall_ms,tps,remaining,confirmed,overbooking,duplicates,
retry_succeeded,retry_exhausted,version_conflicts,deadlocks,mean_attempts,
run_status,error_reason
```

- `schema_version`: `2`
- `schedule_version`: `williams-10-v1`
- `phase`: `a` 또는 `b`
- `app_start_id`: `<campaign_id>-phase-<phase>`
- `schedule_cycle`: `floor((measured_round - 1) / 10) + 1`로 계산한 1부터의 블록 번호
- `schedule_row`: `((measured_round - 1) mod 10) + 1`로 계산한 1–10의 행 번호
- `measured_round`: Phase 안에서 1부터 시작하는 전체 라운드 번호
- `position_in_round`: 1–10
- `optimistic_max_attempts`: 전략별 값이 아니라 해당 행을 실행한 프로세스에서 검증한 설정값이다.
- `run_status`: `ok`, `seed_failed`, `gatling_failed`, `parse_failed`, `db_read_failed`,
  `retry_metrics_failed` 중 하나
- `error_reason`: 실패 원인의 한 줄 요약이며 성공 시 비운다.

`campaign_id`는 `[A-Za-z0-9._-]+`만 허용한다. CSV 생성과 행 추가는 Python 표준 `csv` 모듈로
수행해 `error_reason`을 포함한 모든 필드를 올바르게 인용한다. `error_reason`에 `\r`이나 `\n`이
있으면 기록을 거부한다. 쉘 문자열 연결로 CSV를 만들지 않는다.

측정 도중 실패하면 계획 메타데이터와 실패 상태를 한 행 기록하고, 측정값을 얻지 못한 열은 빈 값으로
둔 뒤 즉시 비정상 종료한다. 성공한 것처럼 다음 처리로 넘어가지 않는다. CSV 자체를 쓸 수 없는
실패는 행 대신 매니페스트와 stderr에 남기고 비정상 종료한다. 재개 기능은 만들지 않는다. 새 캠페인
ID로 처음부터 다시 실행한다.

모든 행은 `schema_version`부터 `contenders`까지의 계획·환경 필드, `ts`, `run_status`를 반드시
채운다. 성공 행은 `error_reason`이 비어 있고 나머지 측정 필드가 모두 있어야 한다. 실패 행은
`error_reason`이 필수이며, 실패 시점까지 얻은 측정 필드만 채우고 이후 필드는 빈 CSV 값으로 둔다.
빈 값과 숫자 0은 구분하며 문자열 `null`은 쓰지 않는다. 실행 파이프라인은 환경 검증 → 워밍업 →
재시도 통계 초기화 → 실행 전 DB 조회 → Gatling → 새 슬롯 확인 → 리포트 파싱 → DB 불변식 조회 →
재시도 통계 조회 → CSV 기록 순이다. 처음 관측된 실패 단계가 `run_status`를 결정하고 즉시 종료하므로
한 행에 여러 실패 상태를 합치지 않는다.

각 캠페인 파일은 한 캠페인만 담는다. Phase A는 존재하지 않는 캠페인 CSV에만 쓸 수 있고, Phase B는
같은 캠페인의 완전한 100행 Phase A 뒤에만 추가할 수 있다. 환경 검증 실패와 워밍업 실패는 측정
CSV를 만들기 전에 중단하고 매니페스트에만 기록한다. 집계기의 다중 캠페인 거부 규칙은 사용자가
진단 파일을 수동으로 합쳤을 때도 오염을 막는 방어선이다.

## 5. 검증과 집계

v2 집계기는 표를 만들기 전에 캠페인을 검증한다.

1. 여러 `campaign_id`가 있으면 `--campaign-id`를 요구하며 자동으로 합치지 않는다.
2. 각 Phase가 100개의 성공 행을 가져야 한다.
3. 각 라운드에 10개 처리가 한 번씩 있어야 한다.
4. 각 10라운드 블록에서 처리×위치 및 라운드 내부 방향성 인접 쌍 균형을 검증한다.
5. Phase별 환경 해시는 하나이며 기대 재시도 상한은 A=5, B=20이어야 한다.
6. 모든 요청 수가 해당 `contenders`와 같고 `run_status=ok`여야 한다.
7. 불완전·중복·불균형·실패 캠페인은 게시용 요약을 만들지 않는다.

기존 25열 CSV는 명시적인 `--legacy-v1` 모드에서만 재현한다. v1과 v2를 한 표에 합치지 않는다.

표의 지표 이름은 계산 단위를 드러낸다.

- `실행별 평균 응답의 중앙값 (min–max)`
- `실행별 p95의 중앙값 (min–max)`
- `버스트 TPS의 중앙값 (min–max)`

②와 ④ 같은 라운드 비교는 `(campaign_id, phase, measured_round, contention)`으로 조인하고 두 실행의
직렬 위치도 함께 표시한다. Phase B의 비낙관 전략 수치는 Phase B 표에 별도로 표시한다.

## 6. 컴포넌트 경계

구현 계획은 다음 책임을 분리한다.

1. **`scripts/benchmark_schedule.py`**: 처리 목록과 라운드 수를 받아 결정적 실행 계획을 만든다.
   HTTP·DB에 의존하지 않는다.
2. **`scripts/parse_gatling_report.py`**: `stats.js`와 바이너리 `simulation.log`를 교차 검증해 한
   실행의 지표를 반환한다.
3. **`scripts/benchmark.sh`**: 기존 환경 검증, 워밍업, 슬롯 시드 확인, Gatling 실행, DB 불변식
   조회, CSV 기록을 순서대로 조율한다.
4. **`scripts/validate_benchmark_campaign.py`**: v2 스키마와 Williams 균형, Phase 완결성, 환경
   일관성을 검사한다.
5. **`scripts/summarize_benchmark.py`**: 검증된 한 캠페인만 표·그래프 데이터로 변환한다.
6. **`scripts/validate_benchmark_environment.py`**: 기존 검증 동작을 유지하면서 성공한 API 응답을
   정규 JSON으로 저장하는 선택지를 추가한다.
7. **`scripts/write_benchmark_row.py`**: stdin에서 정확히 하나의 JSON 객체를 받아 v2 필드와
   `run_status`별 필수·공백 규칙을 검증한 뒤 Python `csv.DictWriter`로 기록한다. `--create`는 기존
   파일을 거부하고 헤더와 첫 행을 쓰며, `--append`는 정확한 v2 헤더를 확인한 뒤 한 행을 추가한다.
   JSON `null`은 빈 CSV 값으로 직렬화하고 `error_reason`의 CR/LF는 거부한다. 검증·쓰기 실패는
   비정상 종료하며 부분 행을 남기지 않는다. 실행기 안의 쉘 `printf`로 v2 행을 직렬화하지 않는다.

스케줄러·파서·검증기는 순수 Python 인터페이스로 테스트하고, Bash 실행기는 이 도구들의 종료 코드만
조율한다. 새 외부 런타임 의존성은 추가하지 않는다.

## 7. 테스트 전략

구현은 테스트 주도로 진행한다.

### 스케줄러

- 10라운드가 Phase당 100개 계획을 만든다.
- 각 라운드가 10개 처리를 정확히 한 번 포함한다.
- 각 처리가 각 위치에 한 번, 라운드 내부의 각 방향성 인접 쌍이 한 번 나타난다.
- 첫 행이 명세의 스냅샷과 일치한다.
- 같은 입력은 같은 출력을 만들며 0·음수·10의 배수가 아닌 공개 라운드는 거부한다.

### 바이너리 로그 파서

- Gatling 3.13.5에서 캡처한 작은 고정 fixture를 예상 요청 수·최초 시작·최종 종료·TPS와 대조한다.
- 시작 시각이 서로 다른 요청, OK/KO 혼합, 다른 요청 이름을 포함한 fixture를 검증한다.
- 공유 문자열 선언·참조와 사용자·그룹·오류 레코드 건너뛰기를 검증한다.
- 지원하지 않는 버전, 레코드 중간에서 잘린 로그, 0ms 구간, 요청 수/`stats.js` 불일치를 거부한다.

### 환경·캠페인

- Spring 설정 상한 20에서 환경 엔드포인트가 20을 보고하는 테스트를 추가한다.
- 환경 불일치 시 Gatling 전에 중단하고 성공 행을 쓰지 않는 것을 검증한다.
- Phase B가 같은 캠페인의 완전한 Phase A에만 이어지는지 검증한다.
- 실패 행이 있는 캠페인, 위치가 중복된 캠페인, 여러 캠페인 자동 혼합을 거부한다.
- 실패·불완전 캠페인이 `raw-runs-v2.csv` 정본으로 승격되지 않는지 검증한다.
- v1은 명시적 legacy 모드에서만 기존 요약을 재현한다.

### 전체 검증

- `./gradlew check`가 Java·스크립트 테스트를 모두 통과한다.
- `--print-plan` 결과로 두 Phase 각각 100행과 Williams 불변식을 확인한다.
- 실제 200행 캠페인을 완주한 뒤 v2 검증기를 통과한다.
- CSV에서 다시 생성한 표·그래프 수치와 체크인 문서가 일치한다.

## 8. 문서와 결과 교체

200행 재측정이 끝나기 전에는 기존 결론을 새 방법론의 결과처럼 바꾸지 않는다. 완료 뒤 다음 문서의
수치와 서술을 v2 결과로 갱신한다.

- `docs/STEP2-DEFENSE-BENCHMARK.md`
- `README.md`
- `PROJECT_PLAN.md`
- `docs/STEP2-3-BRANCH-STRATEGY.md`
- 벤치마크 숫자를 인용하는 조건부 UPDATE·비관적 락·낙관적 락 문서와 코드 주석

기존 `docs/benchmark/raw-runs.csv`와 `2026-09-24-controlled-run.md`는 수정하지 않는다. 새 v2
매니페스트와 종합 문서에서 이를 `methodology-v1-fixed-order` 선행 자료로 명시하고, v2 CSV·환경
JSON을 새 정본으로 연결한다.

정확성 결론도 새 캠페인이 확인한 범위에서만 유지한다. 방향이 달라진 성능 순위와 승패는 과거 수치를
보존하되 현재 결론에서는 교체한다.

## 9. 대안과 기각 이유

### 무작위 순서

구현은 간단하지만 한 번의 10라운드 표본에서 위치와 선행 처리 균형을 보장하지 않는다. seed를
기록해도 불균형한 실험이 재현될 뿐이다.

### 전략만 순환하는 5라운드

전략 위치는 분산하지만 `low`가 항상 먼저인 문제와 경합 간 carryover가 남는다. 이 프로젝트가
지적받은 두 순서 효과를 모두 닫지 못한다.

### 기존 TPS를 proxy로 이름만 변경

과장 표시는 줄지만 처리량을 측정하지 못하는 문제는 남는다. Gatling 버전이 고정돼 있고 실제 요청
시각이 로그에 있으므로 정확한 구간을 읽는 편이 타당하다.

## 10. 완료 기준

다음 조건이 모두 충족돼야 이 문제를 해결한 것으로 본다.

- 스케줄·파서·캠페인 검증 회귀 테스트가 실패→구현→성공 순서로 확인된다.
- 전체 `./gradlew check`가 성공한다.
- 상한 5와 20에서 각각 모든 전략·경합을 10라운드 실행한 200개 성공 행이 있다.
- 각 Phase가 Williams 균형 검증을 통과한다.
- 모든 TPS가 바이너리 로그의 실제 버스트 구간에서 재계산 가능하다.
- 환경 스냅샷과 해시가 각 행에서 캠페인 매니페스트까지 추적된다.
- 불완전하거나 실패한 캠페인은 게시용 요약을 생성하지 못한다.
- 완전한 단일 캠페인만 `docs/benchmark/raw-runs-v2.csv`로 승격되고 기존 정본을 자동 덮어쓰지 않는다.
- v2 수치로 의존 문서와 코드 주석을 갱신하고, v1 자료는 변경 없이 보존한다.
- baseline의 의도적인 무방어 동작과 나머지 전략 구현은 바뀌지 않는다.
