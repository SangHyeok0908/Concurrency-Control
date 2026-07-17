# 2-1단계 ① UNIQUE 제약 — 중복 예약 DB 최후 방어선

> 이 문서는 **가장 가벼운 방어**인 DB `UNIQUE` 제약이 무슨 문제를 없애고 무슨 문제를 못 없애는지
> 기록한다. 핵심은 "중복 예약은 애플리케이션 코드에 버그가 있어도 DB가 최종적으로 막는다"이며,
> 동시에 **UNIQUE는 오버부킹을 막지 못한다**는 경계선을 분명히 한다. 방어 도입 순서와 브랜치 전략은
> [STEP2-3-BRANCH-STRATEGY.md](STEP2-3-BRANCH-STRATEGY.md), 스키마 배경은 [ERD.md](ERD.md)를 정본으로 한다.

관련 코드:
[`UniqueConstraintReservationStrategy`](../src/main/java/com/interview/reservation/service/strategy/UniqueConstraintReservationStrategy.java) ·
[`ReservationStrategy`](../src/main/java/com/interview/reservation/service/strategy/ReservationStrategy.java) ·
[`V2__add_unique_reservation.sql`](../src/main/resources/db/migration/V2__add_unique_reservation.sql) ·
[`UniqueConstraintReservationTest`](../src/test/java/com/interview/reservation/concurrency/UniqueConstraintReservationTest.java)

---

## 1. 한 줄 요약

같은 지원자가 같은 슬롯을 두 번 예약하는 **중복 예약**은, 애플리케이션에 락이 없어도
`UNIQUE(applicant_id, slot_id)` 하나로 **DB 레벨에서 매번 결정적으로 차단**된다.
반면 서로 다른 지원자들의 **오버부킹(정원 경쟁)은 이 제약이 건드리지 않는다** — 그건 ②(조건부 UPDATE) 몫이다.

| 항목 | 값 |
|---|---|
| 방어 수단 | `ALTER TABLE reservation ADD UNIQUE (applicant_id, slot_id)` (V2, append-only) |
| 막는 문제 | 중복 예약 (같은 (지원자, 슬롯) 쌍이 2행 이상) |
| **못 막는** 문제 | 오버부킹 (서로 다른 지원자가 정원 초과). 재시도한 클라이언트에게 기존 결과를 돌려주지도 않는다(6절) |
| 검증 계층 | 서비스/전략 계층 직접 호출 + Testcontainers MySQL 8, HTTP 스모크 |
| 결정성 | **결정적** — baseline 오버부킹의 "간헐성"과 대비된다 |

---

## 2. 왜 이것이 "최후 방어선"인가

애플리케이션 레벨 중복 검사(`SELECT ... 있으면 거절`)는 check-then-act라 baseline 오버부킹과
똑같은 레이스에 노출된다 — 두 요청이 동시에 "없음"을 읽고 둘 다 INSERT 한다. DB의 `UNIQUE`는
그 뒤에 서서, 코드가 레이스로 뚫려도 **두 번째 INSERT를 물리적으로 거부**한다. 방어가 애플리케이션
한 겹뿐일 때 생기는 신뢰 공백을 DB가 메운다. 그래서 "최후 방어선(last line of defense)"이다.

## 3. 설계 — 방어는 baseline을 덮어쓰지 않고 *추가*한다

1단계 락 없는 경로(`ReservationService.reserve`, `POST /api/reservations`)는 **그대로 보존**한다.
방어는 전략(`ReservationStrategy`) 구현체로 나란히 추가하고 `POST /api/reservations/{strategy}`로
골라 때린다. 그래야 같은 Gatling 부하로 방어별 before/after를 비교할 수 있다(⑦ 벤치마크).

| 경로 | 전략 | 동작 |
|---|---|---|
| `POST /api/reservations` | `ReservationService` (직접) | 1단계 baseline — 변경 없음, step1 Gatling 자산 보존 |
| `POST /api/reservations/baseline` | `ReservationService` (`ReservationStrategy` 직접 구현) | 같은 baseline 경로를 전략 키로도 노출(측정 기준선) |
| `POST /api/reservations/unique` | `UniqueConstraintReservationStrategy` | UNIQUE 위반을 도메인 예외로 번역 |

> `ReservationService`가 `ReservationStrategy`를 직접 구현하므로 별도 어댑터 클래스가 없다 —
> baseline·unique 전략이 모두 self-contained 구현체로 일관된다.

**예외 번역.** `save`를 `saveAndFlush`로 바꿔 UNIQUE 검사를 트랜잭션 커밋이 아니라 메서드 *안*에서
강제한다. 그래야 DB의 `DataIntegrityViolationException`을 잡아 `DuplicateReservationException`(HTTP 409)로
번역할 수 있다. 예외가 밖으로 나가면 트랜잭션 전체가 롤백되므로 `slot.decrease()`(좌석 감소)도
되돌아간다 — **거절된 중복 요청이 자리를 축내지 않는다.**

## 4. baseline 재현을 깨지 않는 이유

UNIQUE는 전역(V2)으로 걸어도 1단계 오버부킹 재현을 훼손하지 않는다.

- baseline 오버부킹은 **서로 다른** 지원자들이 한 슬롯의 마지막 자리를 두고 경쟁하는 것이다 —
  (지원자, 슬롯) 쌍이 서로 달라 UNIQUE에 걸리지 않는다.
- `BaselineOverbookingProbeTest`는 라운드마다 새 슬롯을 만들어 (지원자, 슬롯) 쌍이 늘 유일하다.
- Gatling 시나리오도 한 슬롯에 서로 다른 지원자 N명을 붙인다.

실제로 V2 적용 후 `BaselineOverbookingProbeTest`와 `ReservationApplicationTests`는 그대로 통과한다.

## 5. 검증

### 5-1. 통합 테스트 (`UniqueConstraintReservationTest`)

| 테스트 | 단언 | 의미 |
|---|---|---|
| 순차 중복 재요청 | 두 번째 호출이 `DuplicateReservationException`, 예약 행 == 1 | 코드가 재시도해도 DB가 1건으로 고정 |
| 동시 중복 20건 | 예약 행 **정확히 1건**, 성공 1 · 나머지 거절, `remaining == capacity-1` | 최악 동시성에서도 결정적으로 1건, 거절분의 좌석 감소는 롤백 |

baseline 오버부킹 프로브는 "간헐적으로만" 터져 관찰만 했지만, **중복은 DB가 매번 막으므로
"정확히 1건"을 강하게 단언할 수 있다.** 이 결정성 자체가 애플리케이션 방어와 DB 최후 방어선의 차이다.

### 5-2. HTTP 스모크 (bootRun + curl)

| 요청 | 결과 |
|---|---|
| `POST /api/reservations/unique` (첫 예약) | `201` |
| `POST /api/reservations/unique` (같은 쌍 재요청) | `409` — `이미 예약한 슬롯입니다: ...` (500 아님) |
| `POST /api/reservations/nope` (미지원 전략) | `404` — `알 수 없는 예약 전략입니다: nope (사용 가능: [unique, baseline])` |
| `POST /api/reservations` (레거시 baseline) | `201` — 동작 불변 |

> 위 `사용 가능` 목록은 **① 시점의 캡처**다. 등록된 전략에서 동적으로 만들어지는 메시지라 방어가
> 추가될수록 늘어난다(② 이후 `conditional` 포함).

> **운영 메모.** V2는 append-only 마이그레이션이라, 이미 중복 (지원자, 슬롯) 행이 있는 DB에서는
> `ALTER TABLE ... ADD UNIQUE`가 실패한다. 실제로 step1 부하 테스트 잔여 데이터가 남은 로컬 DB에서
> 이 마이그레이션이 `Duplicate entry` 로 실패했고, DB를 초기화해 재적용했다. 운영이라면 제약 추가 전
> 중복 행 정리(dedup)가 선행돼야 한다는 실전 교훈이다.

## 6. 경계선 — UNIQUE가 못 하는 것

- **오버부킹.** 서로 다른 지원자의 정원 경쟁은 (지원자, 슬롯) 쌍이 매번 달라 UNIQUE와 무관하다.
  `unique` 전략도 read→check→decrease가 락이 없어 baseline과 똑같이 오버부킹한다. → **②(조건부 UPDATE)**
- **중복 *요청* 자체의 우아한 처리.** UNIQUE는 두 번째 INSERT를 "거부"할 뿐, 같은 사용자의 재시도에
  기존 결과를 되돌려주지는 않는다 — 응답을 못 받고 재시도한 클라이언트는 실제로 예약에 성공했는데
  409를 받는다. 정확한 응답은 `200 + 기존 예약`이다.
  > **당초 이 빈틈은 ③(멱등성 키) 몫이었으나, ③은 생략하기로 판단했다(2026-07-17).** 이 도메인에서는
  > (지원자, 슬롯) 자연 키가 곧 멱등성 키라, 남는 것은 동시성 문제가 아니라 **API 응답 설계 문제**다.
  > 근거 전문: [브랜치 전략 ③](STEP2-3-BRANCH-STRATEGY.md#skip-idempotency-key).

다음: **② `step2/conditional-update`** — 스키마 변경 없이 원자적 조건부 UPDATE로 오버부킹을 없앤다.
