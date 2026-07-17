# 2-1단계 ② 조건부 UPDATE — 오버부킹을 원자적 연산 하나로 없앤다

> 이 문서는 **스키마 변경 없이** 원자적 조건부 UPDATE 하나로 오버부킹(정원 초과)이 사라지는 것을
> 기록한다. 핵심은 "read→check→decrease 라는 check-then-act 를 DB 한 문장으로 접으면, 락도
> 데드락도 없이 오버부킹이 결정적으로 0이 된다"이다. ①(UNIQUE)이 못 막던 오버부킹을 ②가 맡고,
> ②가 못 하는 중복 *요청*은 ①의 자연 키가 이미 덮는다(③ 멱등성 키는 그래서 생략했다). 방어 순서와 브랜치 전략은
> [STEP2-3-BRANCH-STRATEGY.md](STEP2-3-BRANCH-STRATEGY.md), baseline 재현은
> [STEP1-BASELINE-OVERBOOKING.md](STEP1-BASELINE-OVERBOOKING.md)를 정본으로 한다.

관련 코드:
[`ConditionalUpdateReservationStrategy`](../src/main/java/com/interview/reservation/service/strategy/ConditionalUpdateReservationStrategy.java) ·
[`InterviewSlotRepository#decrementRemaining`](../src/main/java/com/interview/reservation/repository/InterviewSlotRepository.java) ·
[`ConditionalUpdateReservationTest`](../src/test/java/com/interview/reservation/concurrency/ConditionalUpdateReservationTest.java)

---

## 1. 한 줄 요약

서로 다른 지원자들이 마지막 자리를 두고 경쟁하는 **오버부킹**은, 락이나 트랜잭션 격리 승격 없이
`UPDATE interview_slot SET remaining = remaining - 1 WHERE id = ? AND remaining > 0` **한 문장**으로
**결정적으로 0**이 된다. 감소와 정원 검사가 DB 안에서 원자적으로 함께 일어나 check-then-act 레이스가
존재할 자리 자체가 없어지기 때문이다.

| 항목 | 값 |
|---|---|
| 방어 수단 | 원자적 조건부 UPDATE (`WHERE remaining > 0`), **스키마 변경 없음** |
| 막는 문제 | 오버부킹 (서로 다른 지원자가 정원 초과, `remaining` 음수) |
| **못 막는** 문제 | 같은 사용자의 중복 *요청* — ①(V2 UNIQUE)의 자연 키가 방어한다 |
| 검증 계층 | 전략 계층 직접 호출 + Testcontainers MySQL 8, HTTP 스모크 |
| 결정성 | **결정적** — baseline 오버부킹의 "간헐성"과 정면 대비 |

---

## 2. 왜 조건부 UPDATE 하나로 충분한가

baseline·unique 전략의 오버부킹 원인은 **check-then-act**다. 슬롯을 메모리로 읽어와
(`findById`) `isFull()` 로 검사하고 `decrease()` 하는 세 단계 사이에 다른 스레드가 끼어든다 —
둘 다 "자리 있음"을 읽고 둘 다 감소시켜 정원을 넘긴다([STEP1](STEP1-BASELINE-OVERBOOKING.md)).

조건부 UPDATE 는 그 세 단계를 **DB 한 문장으로 접는다.**

```sql
UPDATE interview_slot SET remaining = remaining - 1 WHERE id = ? AND remaining > 0
```

- `remaining > 0` 검사와 `remaining - 1` 감소가 **한 문장 안에서 원자적으로** 수행된다.
  애플리케이션이 값을 메모리로 꺼내 검사하지 않으므로, 검사와 갱신 사이에 끼어들 틈이 없다.
- InnoDB 는 이 UPDATE 가 건드리는 행에 **배타 락**을 걸어 동시 실행을 직렬화한다. 마지막 한
  자리는 정확히 한 요청만 가져가고, 나머지는 `remaining > 0` 이 거짓이 되어 **0행**을 갱신한다.
- 영향 행 수가 **1이면 자리 확보, 0이면 만석**이다. 애플리케이션은 이 반환값만 보고 분기한다.

명시적 락(`SELECT ... FOR UPDATE`)도, 격리 수준 승격도, 재시도도 없다. **가장 가벼운
원자적 연산 하나**로 오버부킹이 사라진다는 것이 이 단계의 논지다. 락 3종(④~⑥)은 이 조건부
UPDATE 하나로 **부족해지는 지점**을 보여주기 위한 것이지 오버부킹의 기본 해법이 아니다.

## 3. 설계 — baseline 을 덮어쓰지 않고 *추가*한다

1단계 락 없는 경로(`ReservationService.reserve`, `POST /api/reservations`)와 ①(`unique`)은
**그대로 보존**한다. 방어는 전략(`ReservationStrategy`) 구현체로 나란히 추가하고
`POST /api/reservations/{strategy}` 로 골라 때린다. 그래야 같은 Gatling 부하로 방어별
before/after 를 비교할 수 있다(⑦ 벤치마크).

| 경로 | 전략 | 오버부킹 |
|---|---|---|
| `POST /api/reservations/baseline` | `ReservationService` (락 없음) | **발생**(간헐적) |
| `POST /api/reservations/unique` | `UniqueConstraintReservationStrategy` | **발생** — UNIQUE 는 중복만 막음 |
| `POST /api/reservations/conditional` | `ConditionalUpdateReservationStrategy` | **0** — 원자적 조건부 UPDATE |

**구현 흐름.** 지원자 존재를 먼저 확인(404)한 뒤 조건부 UPDATE 를 던진다. 갱신 행이 1이면
예약을 INSERT, 0이면 `existsById` 로 만석(`SlotFullException`)과 없는 슬롯(`NotFoundException`)을
구분해 번역한다. 슬롯은 예약의 FK 로만 필요하므로 `getReferenceById` 프록시로 참조만 걸어
불필요한 SELECT 를 피한다 — 조건부 UPDATE 가 이미 자리를 확정했으니 엔티티 상태를 메모리로
읽어올 이유가 없다.

## 4. 검증

### 4-1. 통합 테스트 (`ConditionalUpdateReservationTest`)

[`BaselineOverbookingProbeTest`](../src/test/java/com/interview/reservation/concurrency/BaselineOverbookingProbeTest.java)와
**의도적으로 대칭**이다. 같은 구도(정원 경쟁)를 쓰되 단언의 강도가 다르다.

| 테스트 | 단언 | baseline 과의 대비 |
|---|---|---|
| 정원 1 · 경쟁 20 · 10라운드 | 매 라운드 확정 예약 **정확히 1**, `remaining == 0` | baseline 은 "간헐 오버부킹"을 관찰만, ②는 매 라운드 **결정적으로 정원** |
| 정원 3 · 경쟁 20 | 성공 **정확히 3**, 확정 예약 3, `remaining == 0` | 정원만큼만 확정, 나머지는 만석 거절 |

baseline 프로브는 `confirmed >= capacity` 라는 느슨한 불변식만 걸 수 있었지만(간헐성 때문),
②는 `confirmed == capacity` 를 **강하게** 단언한다. 이 단언 강도의 차이 자체가 방어의 효과다.

### 4-2. HTTP 스모크 (bootRun + curl)

| 요청 | 결과 |
|---|---|
| `POST /api/reservations/conditional` (자리 있음) | `201` |
| `POST /api/reservations/conditional` (만석 슬롯) | `409` — `남은 자리가 없습니다: ...` |
| `POST /api/reservations/conditional` (없는 슬롯) | `404` — `슬롯을 찾을 수 없습니다: ...` |
| `POST /api/reservations/baseline` (레거시) | `201` — 동작 불변 |

## 5. 경계선 — 조건부 UPDATE가 못 하는 것

- **중복 *요청*의 우아한 처리.** 같은 사용자가 더블클릭/재시도로 두 번 요청하면, 조건부 UPDATE 는
  자리가 남아 있는 한 **둘 다 자리를 소모**한다(서로 다른 요청이므로). 이것을 되돌리는 것은
  ①(V2 UNIQUE)이다 — 같은 (지원자, 슬롯)의 두 번째 INSERT 가 제약 위반으로 롤백되고 그때 감소도
  함께 되돌아간다. **자연 키가 곧 멱등성 키**이므로 별도의 멱등성 키(③)는 도입하지 않기로 판단했다
  ([근거](STEP2-3-BRANCH-STRATEGY.md#skip-idempotency-key)).
- **다중 인스턴스 이상의 조율.** 단일 DB 안의 오버부킹은 이 한 문장으로 끝나지만, DB 밖의
  자원 조율이나 분산 락 명분은 → **⑥(Redisson)**.

## 6. 그럼 락(④~⑥)은 왜 필요한가 — "부족해지는 지점"의 정체

이 단계에서 반드시 짚어야 할 오해가 하나 있다. **락 3종은 조건부 UPDATE가 오버부킹을
"못 막아서" 꺼내는 것이 아니다.** 위 검증대로 이 문제(정원 카운터 하나 감소)에서는 조건부
UPDATE만으로 오버부킹이 결정적으로 0이고, 오히려 락 획득/해제도 데드락 위험도 없어 **더
가볍다**(PROJECT_PLAN 3장). 즉 *이 문제 모양에 한해서는 조건부 UPDATE가 사실상 최적해다* —
이건 축소해야 할 결론이 아니라 포트폴리오가 내세울 **정직한 발견**이다.

조건부 UPDATE가 통하는 건 세 조건이 동시에 맞아떨어져서다: 대상이 **단일 행**이고, 변경이
**순수 산술 델타(-1)**이며, 가드가 **그 한 컬럼(`remaining > 0`)**으로 표현된다. 락이 값을 하는
지점은 오버부킹이 재발하는 곳이 아니라, **이 세 조건이 깨져 문제의 모양이 커지는** 곳이다.

| 세 조건이 깨지는 지점 | 왜 조건부 UPDATE로 안 되나 | 등판하는 도구 |
|---|---|---|
| 불변식이 **여러 행/여러 문장**에 걸침<br>(예: "지원자당 전체 확정 예약 2건 이하", "예약+공용 쿼터 차감+감사 로그"를 원자적으로) | 읽고-검사-쓰기를 한 문장에 접을 수 없어 다시 check-then-act | ④ 비관적 락(`SELECT … FOR UPDATE`)으로 읽은 행들을 잠가 임계 구역 구성 |
| 새 값이 **앱에서 계산**돼야 하는 임의의 read-modify-write<br>(예: 대기열 순번 배정, 좌석별 속성) | 델타가 `-1`처럼 단순하지 않아 조건부 UPDATE로 표현 불가 | ⑤ 낙관적 락(`@Version`)이 "읽은 뒤 아무도 안 건드렸다"를 커밋 시점에 검증 |
| 임계 구역이 **단일 DB 밖**으로 확장<br>(예: 외부 결제 API·다른 서비스 DB·정확히 한 번 알림, 다중 인스턴스) | 원자성의 근거인 InnoDB 행 락이 DB 바깥엔 닿지 못함 | ⑥ 분산 락(Redisson `RLock`)이 자원·인스턴스를 가로지르는 뮤텍스 제공 |

그래서 ⑦ 벤치마크의 예상 결론은 "락이 정합성을 **더** 준다"가 아니다. 같은 문제에서 조건부
UPDATE도 락도 오버부킹 0이며, 조건부 UPDATE 쪽이 왕복·락 보유 시간이 짧아 **처리량이 더 높을
공산이 크다**. 락은 **문제가 다중 행·임의 계산·다중 자원으로 확장될 때만** 값을 한다 — "도구를
문제 모양에 맞춰 골랐다"가 이 프로젝트의 논지이며, 락 3종은 그 경계를 실증하기 위한 대조군이다.

## 7. 다음

**③ `step2/idempotency-key`는 생략한다.** 락도 조건부 UPDATE도 중복 *요청* 자체는 막지 못하지만,
이 도메인에서 그 공백을 메우는 것은 멱등성 키가 아니라 ①의 자연 키 `UNIQUE(applicant_id, slot_id)`다 —
(지원자, 슬롯) 쌍이 그 자체로 요청의 정체성이라 클라이언트가 별도 키를 발급할 이유가 없다
([판단 근거](STEP2-3-BRANCH-STRATEGY.md#skip-idempotency-key)).

**다음: ④ `step2/pessimistic-lock`** — 위 6절의 세 조건 중 첫 번째("불변식이 여러 행에 걸침")가
깨지는 자리에서 `SELECT ... FOR UPDATE`가 무엇을 해주는지, 그 대가는 무엇인지 실측한다.
