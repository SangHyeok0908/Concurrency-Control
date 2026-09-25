# 동일 요청 중복 검증 설계

> **상태:** 구현 완료된 내부 설계 기록입니다. 실행 정본은 [2026-09-25-duplicate-request-run.md](../../benchmark/2026-09-25-duplicate-request-run.md)입니다.

- 작성일: 2026-09-25
- 상태: 승인됨 (2026-09-25)
- 기준 커밋: `7645faf` (`docs: plan benchmark methodology v2 implementation`)

## 1. 문제

`docs/benchmark/raw-runs.csv`의 60회 측정은 슬롯 하나에 **서로 다른 지원자**를 한 번씩 보낸
정원 경쟁 실험이다. 따라서 모든 `(applicant_id, slot_id)`가 애초에 다르고, 그 CSV의
`duplicates=0`은 동일 요청 방어를 통과했다는 증거가 아니다.

기존 60행은 설정이 통제된 정원 경쟁의 원시 기록이며 실행 기록에 SHA-256이 고정돼 있다. 원시
파일을 수정하거나 새 실험을 같은 표에 합치지 않는다. 대신 동일 지원자와 동일 슬롯으로 실제 HTTP
동시 재요청을 만드는 별도 workload와 별도 원시 결과를 추가한다.

## 2. 범위

### 포함

- 한 슬롯과 한 지원자만 시드한 뒤 동일 JSON body를 200번 동시에 전송하는 Gatling simulation
- `baseline`, `unique`, `conditional`, `pessimistic`, `optimistic` 다섯 경로를 각각 5회 검증
- HTTP 상태 분포와 DB 불변식을 함께 기록하는 별도 CSV
- 기존 60회 표에서 `중복 0`을 검증 결과처럼 보이게 하는 열과 문구 제거
- 실제 25회 결과, 실행 환경, 재현 명령 문서화

### 제외

- baseline 및 방어 전략 구현 변경
- `/conditional` 등에서 UNIQUE 위반이 500으로 번역되는 API 의미 수정
- 별도 멱등성 키 또는 Redis `SETNX` 도입
- 새 workload의 지연시간·TPS로 전략 우열 판단
- 기존 `raw-runs.csv` 및 아카이브 CSV 수정

## 3. 실험 구도

각 실행은 다음 조건을 사용한다.

| 항목 | 값 |
|---|---|
| 슬롯 | 새 슬롯 1개 |
| 지원자 | 새 지원자 1명 |
| 정원 | 200 |
| 동시 요청 | 동일 `(applicantId, slotId)` 200건, `atOnceUsers(200)` |
| 전략 | baseline, unique, conditional, pessimistic, optimistic(@5) |
| 반복 | 전략별 5회, 총 25회 |
| 애플리케이션 | `benchmark` 프로필, 환경 검증 통과 후 실행 |

정원을 1로 두지 않는다. 정원 1이면 첫 성공 뒤 나머지가 INSERT까지 가지 않고 만석 409로 끝날 수
있어 UNIQUE 충돌과 롤백을 검증하지 못한다. 정원 200은 모든 요청이 자리를 얻으려는 경로에 들어가게
하며, 실패 트랜잭션의 좌석 감소가 함께 롤백되는지도 드러낸다.

다섯 라운드는 전략 목록을 한 칸씩 회전한다. 이 결과는 성능 순위에 쓰지 않지만, 모든 전략이 각
실행 위치에 한 번씩 놓이므로 상태 분포를 볼 때도 고정 순서와 전략이 결합되지 않는다.

## 4. 판정 계약

`duplicate_rows=0`만으로는 예약이 하나도 만들어지지 않은 실패도 통과한다. 따라서 매 실행에서
다음을 함께 만족해야 한다.

- 실제 HTTP 결과 수 합계 = 200
- HTTP 201 = 1
- 네트워크 응답 없음 = 0
- 미분류 HTTP 상태 = 0
- Gatling OK + KO = 200
- Gatling OK = HTTP 201 + HTTP 409
- Gatling KO = HTTP 500 + HTTP 503 + 미분류 상태 + 무응답
- 해당 슬롯의 확정 예약 = 1
- 해당 `(applicant_id, slot_id)` 예약 행 = 1
- 중복 행 수 = 0
- 남은 좌석 = 199
- 소모 좌석 = 1

`unique` 전략에는 HTTP 의미까지 더 강하게 단언한다.

- HTTP 409 = 199
- HTTP 500 = 0
- HTTP 503 = 0

다른 네 전략은 전역 UNIQUE 덕분에 DB 불변식은 지켜져도 UNIQUE 위반을 도메인 409로 번역하지 않을
수 있다. 그 500/503은 숨기거나 성공으로 바꾸지 않고 상태별로 기록한다. 이는 데이터 안전성과 API
응답 의미가 별도 문제임을 보여주는 결과다.

## 5. 산출물

### Gatling

`DuplicateReservationSimulation`은 기존 `BaselineReservationSimulation`을 수정하지 않고 별도
클래스로 둔다. 시드 단계는 슬롯과 지원자를 하나씩 만들고, 부하 단계의 모든 가상 사용자가 같은
두 ID를 읽는다.

요청 check는 201과 409만 정상으로 인정한다. 첫 status check는 실제 응답 코드를 세션에 저장하고,
후속 action이 thread-safe counter에 다음 버킷으로 집계한다.

```text
201, 409, 500, 503, other, no_response
```

simulation 종료 후 HTTP 버킷과 Gatling의 check 판정을 서로 독립적으로 센 JSON sentinel 두 줄을
출력한다. Python 검증기는 두 관측값이 일치하는지도 확인한다.

```text
DUPLICATE_HTTP_STATUS_COUNTS={"201":1,"409":199,"500":0,"503":0,"other":0,"no_response":0}
DUPLICATE_GATLING_COUNTS={"ok":200,"ko":0}
```

### 실행기와 CSV

`scripts/benchmark-duplicates.sh`는 기존 환경 검증기를 재사용하고, 매 실행의 새 슬롯·지원자를
실행 전후 최대 ID로 식별한 뒤 DB를 조회한다. 기존 capacity runner도 simulation FQN을 명시해 두
simulation이 공존해도 선택이 모호하지 않게 한다.

`docs/benchmark/duplicate-runs.csv`는 다음 필드를 가진다.

```text
schema_version,ts,round,position,strategy,workload,capacity,requests,
slot_id,applicant_id,http_201,http_409,http_500,http_503,http_other,
http_no_response,ok,ko,remaining,confirmed,pair_reservations,
duplicate_rows,seats_consumed,invariant_pass
```

원시 HTTP/Gatling sentinel 파싱, 행 검증, CSV 직렬화, 25행 캠페인 완결성 검사는 Python 표준
라이브러리로 수행한다. 불변식 실패 행도 `invariant_pass=false`로 기록한 뒤 실행을 실패시킨다.

## 6. 문서 표현

- 기존 60회는 항상 `서로 다른 지원자의 정원 경쟁 60회`라고 부른다.
- 기존 60회 결과 표와 생성기에서 `중복` 열을 제거한다.
- 기존 CSV의 `duplicates` 필드는 역사 자료이므로 유지하되 중복 검증 근거로 인용하지 않는다.
- 새 25회는 `동일 요청 검증`으로 별도 표·원시 CSV·실행 기록을 둔다.
- 최종 결론은 `UNIQUE가 중복 행을 막고, 조건부 UPDATE가 정원 초과를 막는다`처럼 각 불변식의
  증거를 분리해 연결한다.

## 7. 완료 조건

1. 스크립트 단위 테스트가 status 손실, DB 불변식 실패, unique 응답 의미 실패, 불완전·불균형
   캠페인을 거부한다.
2. `./gradlew check`가 통과한다.
3. 실제 `unique` 단일 dry-run에서 201 1건, 409 199건과 DB 행 1건을 확인한다.
4. 실제 25회가 모두 `invariant_pass=true`로 기록된다.
5. 기존 60행 파일의 SHA-256이 바뀌지 않는다.
6. README와 벤치마크 문서가 두 workload를 혼합해 주장하지 않는다.
