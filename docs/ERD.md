# ERD — 선착순 면접 예약 시스템

> 이 프로젝트의 스키마는 **한 번에 완성되지 않는다.** 1단계는 의도적으로 방어 장치가 없는 상태로 출발해
> 동시성 버그를 재현하고, 방어 수단은 `PROJECT_PLAN.md` 3장의 순서대로 스키마에 하나씩 추가된다.
> 따라서 이 문서는 **최종 스키마**와 **각 요소가 도입되는 단계**를 함께 표기한다.

---

## 1. 전체 관계도

```mermaid
erDiagram
    APPLICANT ||--o{ RESERVATION : "예약한다"
    INTERVIEW_SLOT ||--o{ RESERVATION : "예약된다"

    APPLICANT {
        bigint id PK
        varchar(50) name
        varchar(255) email UK
        datetime created_at
    }

    INTERVIEW_SLOT {
        bigint id PK
        datetime start_at
        datetime end_at
        int capacity
        int remaining "레이스 발생 지점"
        bigint version "2-2단계: 낙관적 락"
        datetime created_at
    }

    RESERVATION {
        bigint id PK
        bigint applicant_id FK
        bigint slot_id FK
        varchar(20) status
        datetime created_at
    }
```

> **`IDEMPOTENCY_KEY` 테이블은 관계도에서 뺐다(2026-07-17).** 최초 설계에는 있었으나 **도입하지 않기로
> 판단**했다 — 이 도메인은 (지원자, 슬롯) 자연 키가 요청의 정체성을 이미 고정한다. 설계 자체는 미채택
> 기록으로 [2.4](#idempotency-key-table)에 남겨 뒀다.
> 판단 근거: [브랜치 전략 ③](STEP2-3-BRANCH-STRATEGY.md#skip-idempotency-key).

관계 요약

| 관계 | 카디널리티 | 설명 |
|---|---|---|
| Applicant : Reservation | 1 : N | 한 지원자는 여러 슬롯에 예약할 수 있다 |
| InterviewSlot : Reservation | 1 : N | 한 슬롯은 `capacity`명까지 예약을 받는다 |
| Applicant : InterviewSlot | N : M | `Reservation`이 교차 엔티티. 단, **같은 조합은 1건뿐**이어야 한다 (→ 2-1단계 UNIQUE) |

그 UNIQUE 조합 `(applicant_id, slot_id)`는 이 스키마의 **자연 키**이기도 하다. 같은 쌍의 두 번째 요청은
정의상 언제나 중복이므로, 이 키가 멱등성 키 역할을 겸한다 — 별도의 `idempotency_key` 테이블을
두지 않기로 한 이유다.

---

## 2. 테이블 상세

### 2.1 `applicant` — 지원자

| 컬럼 | 타입 | 제약 | 도입 |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | 1단계 |
| `name` | VARCHAR(50) | NOT NULL | 1단계 |
| `email` | VARCHAR(255) | NOT NULL, UNIQUE | 1단계 |
| `created_at` | DATETIME(6) | NOT NULL | 1단계 |

`email`의 UNIQUE는 1단계부터 넣는다. 이 프로젝트가 재현하려는 레이스는 **예약 경로**에서 발생하고,
지원자 등록은 그 경로 밖이다. 방어 수단 도입 순서를 어기는 것이 아니다.

### 2.2 `interview_slot` — 면접 슬롯

| 컬럼 | 타입 | 제약 | 도입 |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | 1단계 |
| `start_at` | DATETIME(6) | NOT NULL | 1단계 |
| `end_at` | DATETIME(6) | NOT NULL | 1단계 |
| `capacity` | INT | NOT NULL | 1단계 |
| `remaining` | INT | NOT NULL | 1단계 |
| `version` | BIGINT | NOT NULL DEFAULT 0 | **2-2단계** (`@Version`) |
| `created_at` | DATETIME(6) | NOT NULL | 1단계 |

**`remaining`이 이 프로젝트 전체의 진앙지다.** 1단계 구현은 이 값을 읽고(`SELECT`),
애플리케이션 메모리에서 검사하고(`if (remaining > 0)`), 다시 쓴다(`UPDATE ... SET remaining = ?`).
읽기와 쓰기 사이에 다른 트랜잭션이 끼어들 수 있는 **check-then-act** 구조이며,
정원 초과(오버부킹)는 여기서 발생한다.

`capacity`를 따로 두는 이유: 부하 테스트 후 `capacity`와 실제 `reservation` 행 수를 비교해야
**정원이 몇 명 초과됐는지 정량적으로 증명**할 수 있다. `remaining`만으로는 음수 여부밖에 모른다.

> `remaining`을 두지 않고 매번 `COUNT(*)`로 세는 설계도 가능하다. 그러나 그 역시 check-then-act이며
> (세는 시점과 INSERT 시점 사이에 레이스), 카운터 컬럼 쪽이 2-1단계의 조건부 UPDATE
> (`SET remaining = remaining - 1 WHERE remaining > 0`)로 자연스럽게 이어진다.

### 2.3 `reservation` — 예약

| 컬럼 | 타입 | 제약 | 도입 |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | 1단계 |
| `applicant_id` | BIGINT | NOT NULL, FK → `applicant.id` | 1단계 |
| `slot_id` | BIGINT | NOT NULL, FK → `interview_slot.id` | 1단계 |
| `status` | VARCHAR(20) | NOT NULL (`CONFIRMED` / `CANCELED`) | 1단계 |
| `created_at` | DATETIME(6) | NOT NULL | 1단계 |

| 인덱스 / 제약 | 도입 |
|---|---|
| `INDEX idx_reservation_slot (slot_id)` | 1단계 (검증 쿼리용) |
| `UNIQUE KEY uk_reservation_applicant_slot (applicant_id, slot_id)` | **2-1단계** |

1단계에는 UNIQUE가 **없다.** 같은 지원자가 같은 슬롯에 두 행을 만드는 중복 예약을 재현해야 하기 때문이다.
FK는 유지한다 — FK는 참조 무결성 제약이지 동시성 방어 수단이 아니고, 없으면 오히려 실험 데이터가 오염된다.

<a id="idempotency-key-table"></a>
### 2.4 `idempotency_key` — 멱등성 키 (**미채택**)

> **이 테이블은 만들지 않는다.** 아래는 최초 설계 그대로의 기록이며, 마이그레이션도 엔티티도 없다.
> 왜 접었는지는 이 절 끝의 "미채택 사유"에 있다.

| 컬럼 | 타입 | 제약 |
|---|---|---|
| `idempotency_key` | VARCHAR(64) | PK (클라이언트가 생성한 UUID) |
| `applicant_id` | BIGINT | NOT NULL, FK → `applicant.id` |
| `request_hash` | VARCHAR(64) | NOT NULL (요청 본문 해시) |
| `status` | VARCHAR(20) | NOT NULL (`IN_PROGRESS` / `COMPLETED`) |
| `reservation_id` | BIGINT | NULL, FK → `reservation.id` |
| `created_at` | DATETIME(6) | NOT NULL |
| `expires_at` | DATETIME(6) | NOT NULL |

동작: 요청이 들어오면 먼저 이 테이블에 `IN_PROGRESS`로 INSERT를 시도한다.
**PK 충돌 자체가 "중복 요청"의 판정**이다 — 별도 조회가 필요 없고, 조회-후-삽입의 레이스도 없다.
충돌 시 기존 행의 `status`를 본다. `COMPLETED`면 `reservation_id`로 이전 결과를 그대로 반환하고,
`IN_PROGRESS`면 첫 요청이 아직 처리 중이므로 409를 반환한다.

`request_hash`는 같은 키로 **다른 내용**의 요청이 오는 경우(클라이언트 버그)를 잡기 위한 것이다.
`expires_at`은 TTL — 이 테이블은 무한히 자라면 안 되고, 배치로 만료 행을 지운다.

> Redis `SETNX` + TTL로 대체 가능하다. 테이블 방식은 예약과 **같은 트랜잭션에 묶을 수 있다**는 것이
> 결정적 장점이고(Redis는 DB 트랜잭션 롤백과 함께 되돌아가지 않는다), 대신 DB 부하를 더 준다.

**미채택 사유 (2026-07-17).** 위 설계의 핵심은 "PK 충돌 자체가 중복 요청의 판정"이다. 그런데 이 스키마에는
**이미 그 역할을 하는 키가 있다** — `UNIQUE(applicant_id, slot_id)`. 클라이언트가 UUID를 발급해 요청의
정체성을 알려줘야 하는 것은 결제·송금처럼 요청 내용만으로 중복 여부를 판별할 수 없는 연산이지만, 예약은
(지원자, 슬롯) 쌍이 곧 그 요청이다. 같은 판정을 두 번 구현하는 셈이라 테이블도 Redis도 도입하지 않았다.
`request_hash`·`expires_at`·만료 배치가 통째로 불필요해진다.

남는 빈틈은 하나뿐이고 작다: `COMPLETED` 행에서 이전 결과를 되돌려주는 동작이 없으므로, 응답을 못 받고
재시도한 클라이언트는 `200 + 기존 예약`이 아니라 `409`를 받는다. 이건 스키마 문제가 아니라 API 응답 설계
문제다. 전체 근거: [브랜치 전략 ③](STEP2-3-BRANCH-STRATEGY.md#skip-idempotency-key).

---

## 3. 단계별 스키마 변화

| 단계 | 스키마 변경 | 막는 문제 |
|---|---|---|
| **1** | 위 3개 테이블, 방어 제약 없음 | — (버그 재현이 목적) |
| **2-1** | `UNIQUE(applicant_id, slot_id)` 추가 (`V2`) | 중복 예약 |
| **2-1** | (스키마 변경 없음 — 조건부 UPDATE는 쿼리 변경) | 정원 초과 |
| ~~2-1~~ | ~~`idempotency_key` 테이블 신설~~ — **미채택** | 같은 사용자의 중복 요청 → 위 UNIQUE 자연 키가 대신한다 |
| **2-2** | `interview_slot.version` 추가 (`V3`) | (낙관적 락 실험용) |

비관적 락(`SELECT ... FOR UPDATE`)과 분산 락(Redisson)은 **스키마를 바꾸지 않는다.**
전자는 쿼리 힌트, 후자는 DB 바깥이다. 이 사실 자체가 "락은 스키마에 흔적을 남기지 않으므로
DB가 최후 방어선이 되어주지 못한다"는 논증의 재료가 된다.

---

## 4. 열린 설계 질문

곧바로 답을 내지 않고, 해당 단계에서 근거를 갖고 결정한다.

**`UNIQUE(applicant_id, slot_id)`와 예약 취소가 충돌한다.**
취소 후 재예약을 허용하면, `CANCELED` 행이 남은 상태에서 UNIQUE가 새 `CONFIRMED` 행을 막는다.
선택지는 (a) 취소 시 행을 삭제, (b) UNIQUE에 `canceled_at`을 포함(MySQL은 NULL 중복을 허용하므로
활성 예약만 유일해진다), (c) MVP에서 취소를 제외. **1·2단계는 (c)로 단순화한다** — 취소는 이 프로젝트가
증명하려는 논지와 무관하고, 스코프만 키운다.

**`reservation`이 아니라 `interview_slot`에 카운터를 두면 슬롯 행이 핫스팟이 된다.**
같은 슬롯에 몰린 모든 요청이 한 행을 두고 경쟁한다. 2-2단계 벤치마크에서 락 방식별 처리량 차이가
가장 크게 벌어지는 지점이 여기이며, 실제로 그렇게 측정되는지 확인한다.
