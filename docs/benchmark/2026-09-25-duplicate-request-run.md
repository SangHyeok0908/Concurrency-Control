# 2026-09-25 동일 요청 검증 실행 기록

이 파일은 같은 `(applicant_id, slot_id)` 예약 요청을 실제 HTTP로 동시에 보낸
[`duplicate-runs.csv`](duplicate-runs.csv) 25행이 어떤 환경과 계약으로 만들어졌는지 고정한다.
서로 다른 지원자가 정원을 경쟁한 기존 60행은
[통제 재측정 실행 기록](2026-09-24-controlled-run.md)과
[`raw-runs.csv`](raw-runs.csv)에 별도로 보존한다. 두 workload는 목적이 다르므로 응답시간이나 TPS를
서로 비교하지 않는다.

## 검증 질문과 통과 조건

매 실행마다 정원 200 슬롯 하나와 지원자 한 명을 새로 만들고, 같은 요청 body를
`atOnceUsers(200)`으로 보냈다. 검증 대상은 다음 두 질문이다.

1. 전역 `UNIQUE(applicant_id, slot_id)`가 어느 예약 경로에서도 중복 행과 이중 좌석 차감을
   막는가?
2. 각 경로는 거절된 199개 재요청을 어떤 HTTP 상태로 반환하는가?

한 실행은 아래 조건을 **모두** 만족해야 통과한다. `duplicate_rows=0` 하나만으로는 예약이 아예
생기지 않은 실행도 통과할 수 있으므로 충분하지 않다.

- HTTP 상태 버킷의 합이 200이고, `201=1`, 기타 상태와 무응답은 0이다.
- DB의 확정 예약 수와 정확한 applicant-slot pair 행 수가 각각 1이다.
- `remaining=199`, 좌석 소모량은 1, 중복 초과 행 수는 0이다.
- `/unique`는 나머지 199건을 모두 409로 반환하며 500·503은 0이다.

## 실행 환경

| 항목 | 값 |
|---|---|
| 정본 측정 시각 | 2026-09-25 13:56:23–13:58:32 KST |
| 측정 전 Git HEAD | `7645faf85921836d42a7952316af70ecf58ba7c8` + 이 변경의 미커밋 동일 요청 검증 코드 |
| OS | macOS 26.6.2 (25G83), arm64 |
| Java | Amazon Corretto 21.0.9 |
| Gradle | 8.14.5 (wrapper) |
| Spring Boot | 3.5.16 |
| Gatling | 3.13.5.4 |
| MySQL | 8.0.46, Docker `mysql:8.0`, `max_connections=151` |
| 정본 측정 시작 DB | 슬롯 492행, 지원자 73,255행, 예약 14,827행 누적 |
| 정본 측정 종료 DB | 슬롯 517행, 지원자 73,280행, 예약 14,852행 누적 |

독립 Gatling OK/KO 계측을 추가한 뒤 정본 실행 전에 `/unique` 1회 smoke run을 별도 임시 CSV로
수행했다. smoke run은
`201=1`, `409=199`, 확정/pair/좌석 소모가 모두 1, `remaining=199`로 통과했지만 정본 25행에는
포함하지 않았다. 따라서 위 시작 DB 카운트는 smoke run 이후 값이다.

## 애플리케이션 통제값

앱은 `spring.profiles.active=benchmark`로 실행했다. 스크립트는 부하 전에
`/api/metrics/benchmark-environment` 응답을 검사하며, 아래 값이 하나라도 다르면 Gatling을
실행하지 않는다.

| 항목 | 통제값 |
|---|---|
| Hikari maximum / minimum idle / 준비된 연결 | 100 / 100 / 100 |
| Hibernate `show_sql` / `format_sql` / `use_sql_comments` | `false` / `false` / `false` |
| root / Hibernate SQL / bind logger | `OFF` / `OFF` / `OFF` |
| 백오프 | exponential jitter, base 10ms, max 200ms |
| 낙관적 최대 시도 | 5 |

## 실행 순서

```bash
docker compose up -d
./gradlew bootRun --args='--spring.profiles.active=benchmark'

# 별도 임시 경로의 사전 smoke — 정본 CSV에는 포함하지 않음
scripts/benchmark-duplicates.sh \
  --rounds 1 --strategies unique \
  --out /private/tmp/duplicate-smoke-v2.PkQa1U/smoke.csv

# 5개 경로 × 5라운드 = 정본 25행
scripts/benchmark-duplicates.sh \
  --out /private/tmp/duplicate-campaign-v2.6LpN2R/duplicate-runs.csv
python3 scripts/duplicate_benchmark.py summarize \
  /private/tmp/duplicate-campaign-v2.6LpN2R/duplicate-runs.csv --rounds 5
```

검증된 25행을 저장소의 줄바꿈 형식(LF)으로 정규화해 `docs/benchmark/duplicate-runs.csv` 정본으로
게시했다. 아래 해시는 게시된 파일 기준이다.

장기 워밍업과 고정 위치 효과를 한 전략에 몰지 않도록 라운드마다 시작 전략을 한 칸씩 옮겼다.

| 라운드 | 실행 순서 |
|---:|---|
| 1 | baseline → unique → conditional → pessimistic → optimistic |
| 2 | unique → conditional → pessimistic → optimistic → baseline |
| 3 | conditional → pessimistic → optimistic → baseline → unique |
| 4 | pessimistic → optimistic → baseline → unique → conditional |
| 5 | optimistic → baseline → unique → conditional → pessimistic |

각 실행은 새 슬롯·지원자의 ID를 확인하고, Gatling이 내보낸 201/409/500/503/기타/무응답 버킷,
그 버킷과 독립적으로 센 Gatling check의 OK/KO, 실행 직후 MySQL의 확정 수·pair 수·잔여석·중복
초과 행을 한 CSV 행에 함께 기록한다. 누락된 HTTP 응답, 두 계측의 불일치, 부분 실행은 합계
검증에서 실패한다.

## 결과

| 경로 | 실행 | 전체 요청 | 201 | 409 | 500 | 503 | 기타/무응답 | DB 불변식 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| baseline | 5 | 1,000 | 5 | 0 | 995 | 0 | 0 | **PASS** |
| unique | 5 | 1,000 | 5 | 995 | 0 | 0 | 0 | **PASS** |
| conditional | 5 | 1,000 | 5 | 0 | 995 | 0 | 0 | **PASS** |
| pessimistic | 5 | 1,000 | 5 | 0 | 995 | 0 | 0 | **PASS** |
| optimistic | 5 | 1,000 | 5 | 0 | 995 | 0 | 0 | **PASS** |

25회 모두 예약 행과 좌석 소모는 정확히 1이었고, `remaining=199`, `duplicate_rows=0`이었다.
V2 UNIQUE가 테이블 전역 제약이므로 다섯 경로 모두 데이터는 지켰다. 그러나 제약 위반을
`DuplicateReservationException`으로 번역하는 것은 `/unique`뿐이라, 나머지 네 경로는 각 실행의
재요청 199건을 500으로 반환했다. 즉 **DB 중복 방어는 검증됐지만 최종 `/conditional`의 재요청
응답 계약은 아직 미완성**이다.

이 workload의 응답시간은 전략 성능 순위를 정하는 데 사용하지 않는다. 409와 처리되지 않은 500은
서로 다른 애플리케이션 경로를 타므로, 시간 비교는 방어 메커니즘보다 오류 번역 차이를 더 크게
반영하기 때문이다.

## 산출물 무결성

- `duplicate-runs.csv`는 헤더 1행 + 데이터 25행이다.
- `duplicate-runs.csv` SHA-256:
  `a455df4a9246c789827dfa6bf897f33419d1f74b777b01d8aca792608859b834`
- 기존 `raw-runs.csv` SHA-256:
  `7824f10229b460e911a49f47c46bb8d6338587de1401709c09a8e919ab1ed3c3`

마지막 해시는 2026-09-24 실행 기록의 값과 같다. 이번 검증은 기존 정원 경쟁 원시 데이터에 행을
추가하거나 내용을 바꾸지 않고 별도 CSV로 만들었다.
