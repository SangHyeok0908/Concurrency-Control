# 선착순 면접 예약 시스템 — 동시성 제어 실험

> 단순 예약 기능이 아니라, 동시성 문제를 의도적으로 재현하고 여러 방어 수단을 같은 조건에서 비교해 최종 선택의 근거를 남기는 실험형 프로젝트입니다.

학부 동아리 지원자 관리 서비스의 선착순 면접 예약 기능에서 출발했습니다. 당시에는 동시성 문제가 드러나지 않았지만, 검증되지 않았을 뿐 언제든 정원 초과와 중복 예약이 생길 수 있는 구조였습니다. 이 프로젝트는 그 문제를 다시 만들고 측정 가능한 근거로 해법을 고르는 포트폴리오입니다.

기획과 판단 기준은 [`PROJECT_PLAN.md`](PROJECT_PLAN.md), 브랜치별 진행 현황은 [`docs/STEP2-3-BRANCH-STRATEGY.md`](docs/STEP2-3-BRANCH-STRATEGY.md), 데이터 모델은 [`docs/ERD.md`](docs/ERD.md)에 있습니다.

## 다루는 문제

| 문제 | 원인 | 방어 |
|---|---|---|
| 같은 사용자의 중복 요청 | 더블클릭·네트워크 재시도 | 자연 키 `UNIQUE(applicant_id, slot_id)` |
| 다른 사용자의 마지막 자리 경쟁 | 데이터 레이스 | 조건부 UPDATE 또는 락 |

락은 정원 경쟁을 막아도 중복 요청을 해결하지 못합니다. 이 도메인에서는 `(applicant, slot)` 자체가 요청의 정체성이므로 별도 멱등성 키를 도입하지 않았습니다. 자세한 판단은 [브랜치 전략 ③](docs/STEP2-3-BRANCH-STRATEGY.md#skip-idempotency-key)를 봅니다.

## 기술 스택

| 구분 | 기술 |
|---|---|
| 언어 / 프레임워크 | Java 21, Spring Boot 3.5.16 |
| ORM / DB | Spring Data JPA, MySQL 8.0 |
| 캐시 | Redis 7 (컨테이너만 기동) |
| 부하 테스트 / 빌드 | Gatling, Gradle wrapper |

## 로컬 실행 방법

**요구사항**: JDK 21, Docker.

```bash
docker compose up -d
docker compose ps
./gradlew build
./gradlew bootRun
```

기본 접속값은 `application.yml`에 맞춰져 있습니다. 필요하면 Spring 표준 환경 변수
`SPRING_DATASOURCE_URL`, `SPRING_DATASOURCE_USERNAME`, `SPRING_DATASOURCE_PASSWORD`,
`SPRING_DATA_REDIS_HOST`, `SPRING_DATA_REDIS_PORT`로 덮어쓸 수 있습니다. 실험 환경 초기화에는
`docker compose down -v`를 사용합니다.

## 스키마

스키마는 Hibernate가 아닌 Flyway로 버전 관리합니다. 방어 수단이 들어오는 이력 자체가 실험의 증거이기 때문입니다.

| 버전 | 내용 | 단계 |
|---|---|---|
| `V1__baseline_schema_without_guards.sql` | applicant / interview_slot / reservation, 방어 제약 없음 | 1단계 |
| `V2__add_unique_reservation.sql` | `UNIQUE(applicant_id, slot_id)` | 2-1단계 ① |
| `V3__add_version_to_slot.sql` | `interview_slot.version` | 2-2단계 ⑤ |

조건부 UPDATE는 스키마 변경이 없습니다. 테이블 정의와 근거는 [`docs/ERD.md`](docs/ERD.md)에 있습니다.

## 실험 결과

**1단계 baseline, 2단계 벤치마크, 3단계 최종 선택이 완료되었습니다.** 전용 `benchmark` 프로필로 풀 100개와 로그 OFF를 고정해 서로 다른 지원자의 정원 경쟁을 60회 측정했고, 동일 `(지원자, 슬롯)` 요청 200건을 5개 경로에서 각 5회 검증했습니다. 측정 조건·원시 데이터·해석은 [`docs/STEP2-DEFENSE-BENCHMARK.md`](docs/STEP2-DEFENSE-BENCHMARK.md)에 있습니다.

### 1단계 — 방어 없는 baseline

락 없는 `POST /api/reservations`에 정원 100 슬롯과 서로 다른 지원자 500명을 동시에 요청했을 때 다음이 한 실행에서 나타났습니다.

- 오버부킹: 예약 113건으로 정원보다 13건 초과
- lost update: `remaining`은 40인데 예약은 113건
- 데드락: 500건 중 387건(77%)이 HTTP 500
- 409는 0건: lost update로 카운터가 0에 닿지 않아 만석을 인식하지 못함

서비스 계층에서는 간헐적이었지만 HTTP 부하가 경쟁 창을 넓히자 상시로 드러났습니다. 근거는 [서비스 계층 재현](docs/STEP1-BASELINE-OVERBOOKING.md)과 [Gatling HTTP 부하](docs/STEP1-GATLING-LOADTEST.md)에 있습니다.

### Before / After 부하 테스트

동일 시나리오·부하·애플리케이션 설정으로 각 5회 측정했습니다. 응답시간은 중앙값이며 괄호는 min–max입니다.

**낮은 경합 — `capacity=100`, `contenders=120`**

| 방식 | TPS | 응답시간 (중앙값 / p95) | KO | 데이터 정합성 |
|---|---|---|---|---|
| 방어 없음 | 857.1 | 92ms <sub>(74–336)</sub> / 131ms | **111/120** | ⚠️ 중앙값 9석만 확정 |
| ① UNIQUE | 1081.1 | 85ms <sub>(72–90)</sub> / 108ms | **113/120** | ⚠️ 중앙값 7석만 확정 |
| ② 조건부 UPDATE | **582.5** | **136ms** <sub>(109–233)</sub> / 194ms | 0 | ✅ 100석 전부 |
| ④ 비관적 락 | 560.7 | **136ms** <sub>(125–161)</sub> / 203ms | 0 | ✅ 동일 |
| ⑤ 낙관적 락 (상한 5) | 364.7 | 262ms <sub>(240–296)</sub> / 316ms | **107/120** | ⚠️ 중앙값 13석 |
| ⑤ 낙관적 락 (상한 20) | **137.6** | **520ms** <sub>(375–575)</sub> / 777ms | 0 | ✅ 100석 전부 |

**극단 경합 — `capacity=1`, `contenders=200`**

| 방식 | TPS | 응답시간 (중앙값 / p95) | KO | 데이터 정합성 |
|---|---|---|---|---|
| 방어 없음 | 1652.9 | 74ms <sub>(61–285)</sub> / 112ms | 10 | ❌ 최대 오버부킹 +2 |
| ① UNIQUE | 1503.8 | 70ms <sub>(67–124)</sub> / 106ms | 10 | ❌ 최대 오버부킹 +2 |
| ② 조건부 UPDATE | 947.9 | 132ms <sub>(102–221)</sub> / 194ms | 0 | ✅ 오버부킹 0 |
| ④ 비관적 락 | 1470.6 | 86ms <sub>(72–90)</sub> / 120ms | 0 | ✅ 동일 |
| ⑤ 낙관적 락 (상한 5·20) | 1639.3 / 1639.3 | 72 / 74ms | 0 | ✅ 동일 |

**동일 요청 검증 — `capacity=200`, 같은 `(applicantId, slotId)` 200건**

| 경로 | 실행 | 201 | 409 | 500 | DB 결과 |
|---|---:|---:|---:|---:|---|
| baseline | 5 | 5 | 0 | 995 | ✅ 예약 1건·좌석 1개 |
| ① UNIQUE | 5 | 5 | **995** | 0 | ✅ 예약 1건·좌석 1개 |
| ② 조건부 UPDATE | 5 | 5 | 0 | 995 | ✅ 예약 1건·좌석 1개 |
| ④ 비관적 락 | 5 | 5 | 0 | 995 | ✅ 예약 1건·좌석 1개 |
| ⑤ 낙관적 락 | 5 | 5 | 0 | 995 | ✅ 예약 1건·좌석 1개 |

전역 V2 UNIQUE는 모든 경로에서 중복 행과 추가 좌석 소모를 막았습니다. `/unique`만 제약 위반을 409로 번역합니다. 이는 성능 순위가 아니라 DB 안전성과 HTTP 응답 의미를 분리한 검증입니다.

### 전략별 관찰

| 전략 | 결과와 판단 | 상세 근거 |
|---|---|---|
| ① UNIQUE | 동일 요청의 DB 중복을 막지만 서로 다른 지원자의 정원 경쟁은 막지 못함 | [문서](docs/STEP2-UNIQUE-CONSTRAINT.md) |
| ② 조건부 UPDATE | 두 경합 지점에서 오버부킹·KO 0; 현재 불변식을 한 DML로 표현 | [문서](docs/STEP2-CONDITIONAL-UPDATE.md) |
| ④ 비관적 락 | 정합성은 동일하고 극단 경합에서는 더 빨랐으나, 현재 문제에는 명시적 잠금 구간이 불필요 | [문서](docs/STEP2-PESSIMISTIC-LOCK.md) |
| ⑤ 낙관적 락 | 오버부킹은 막지만 충돌·재시도 비용 때문에 상한 5는 가용성, 상한 20은 처리량을 잃음 | [문서](docs/STEP2-OPTIMISTIC-LOCK.md) |
| ③ 멱등성 키 / ⑥ 분산 락 | 현재 도메인의 자연 키·단일 DB 경계에서는 중복 도입이므로 생략 | [③](docs/STEP2-3-BRANCH-STRATEGY.md#skip-idempotency-key) · [⑥](docs/STEP2-3-BRANCH-STRATEGY.md#skip-distributed-lock) |

## 아키텍처

1단계의 락 없는 경로는 재현 자산으로 보존하고, 2단계 방어는 `ReservationStrategy` 구현체로 나란히 추가했습니다. `POST /api/reservations/{strategy}`로 같은 요청을 각 전략에 보내 방어 수단만 바꾼 비교가 가능합니다.

```mermaid
sequenceDiagram
    actor Client
    participant Controller as ReservationController
    participant Resolver as ReservationStrategyResolver
    participant Strategy as ReservationStrategy
    participant Slot as interview_slot
    participant Reservation as reservation

    Client->>Controller: POST /api/reservations/{strategy}
    Controller->>Resolver: resolve(strategy)
    Resolver-->>Controller: baseline / unique / conditional / pessimistic / optimistic
    Controller->>Strategy: reserve(applicantId, slotId)
    alt conditional
        Strategy->>Slot: UPDATE ... WHERE remaining > 0
        Strategy->>Reservation: INSERT (갱신 1행)
    else pessimistic
        Strategy->>Slot: SELECT ... FOR UPDATE
        Strategy->>Reservation: INSERT
    else optimistic
        Strategy->>Slot: SELECT + version UPDATE
        Strategy->>Strategy: 충돌 시 백오프 재시도
        Strategy->>Reservation: INSERT
    else baseline / unique
        Strategy->>Slot: read-check-decrease
        Strategy->>Reservation: INSERT
    end
    Note over Reservation: V2 UNIQUE(applicant_id, slot_id)는 모든 INSERT의 최후 방어선
```

## 트레이드오프와 최종 선택

**최종 선택은 `UNIQUE` + 조건부 UPDATE입니다.** 두 장치는 경쟁하지 않습니다. UNIQUE는 동일 `(applicant_id, slot_id)` 중복을, 조건부 UPDATE는 마지막 자리 경쟁의 정원 불변식을 맡습니다.

조건부 UPDATE는 성능 1위라서가 아니라, **단일 슬롯 행·단순 산술 차감·`remaining > 0` 가드**를 한 문장으로 표현하는 가장 작은 메커니즘이어서 선택했습니다. 통제 측정에서 비관적 락은 낮은 경합에서 동률, 극단 경합에서는 더 빨랐습니다. 따라서 비관적 락을 느리다는 이유로 배제하지 않으며, 다중 행·다단계 트랜잭션에서는 다시 후보가 됩니다. 낙관적 락은 실제 충돌이 드물고 재시도가 싼 경우에, 분산 조율은 외부 결제·다른 저장소·정확히 한 번 알림처럼 임계 구역이 DB 밖으로 확장될 때 검토합니다.

트래픽이 늘어도 공유 상태가 단일 MySQL에 남는 한 조건부 UPDATE와 UNIQUE의 원자성은 유지됩니다. 먼저 인기 슬롯 핫스폿과 DB 쓰기 한계를 측정하고, 애플리케이션 확장·읽기 분리·DB 용량을 검토합니다. 한 인기 슬롯의 마지막 자리는 어떤 방식이든 직렬화해야 하므로, 과부하는 admission control이나 대기열로 흡수합니다.

남은 API 과제는 두 가지입니다. 조건부 UPDATE의 만석 거절에서 404/409 구분 조회를 유지할지 별도 A/B로 확인하고, 중복 재시도에는 UNIQUE 위에서 기존 예약을 조회해 `200 + 기존 예약`을 반환하는 응답 계약을 설계합니다. 둘 다 별도 멱등성 키가 필요한 정합성 결함은 아닙니다.
