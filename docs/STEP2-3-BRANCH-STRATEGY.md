# 2·3단계 브랜치 전략 (선착순 예약 동시성 제어 포트폴리오)

> **새 세션에서 브랜치 하나를 작업할 때 이 문서를 정본으로 삼는다.**
> 시작 멘트 예시: `step2/unique-constraint 브랜치 작업 시작. docs/STEP2-3-BRANCH-STRATEGY.md의 ①을 따른다.`
> 각 브랜치의 세부 구현은 그 브랜치 진입 시점에 별도로 설계한다.

## Context

1단계(락 없는 baseline + 오버부킹/lost update/데드락 재현 + Gatling 기준선)는 완료됐다.
이제 `PROJECT_PLAN.md` 3장의 방어 계층을 **가벼운 것부터** 순서대로 도입하고, 1단계와
같은 Gatling 시나리오로 before/after를 비교한다. 순서 자체가 포트폴리오의 논지다
("가장 단순한 도구에서 시작해, 부족해지는 지점에서만 무거운 도구를 꺼냈다").

이 문서는 **구현 계획이 아니라 브랜치 전략**이다. 각 방어 수단을 어떤 브랜치에서 무슨
작업으로 다루는지, 어떤 순서로 master에 머지하는지를 정한다.

**결정 사항**
- 세분화: **방어 하나 = 브랜치 하나 = PR 하나** (각 PR이 도구 하나와 그것이 고친 문제를 보여줌)
- 범위: **2·3단계만.** 4단계(비동기/CI/배포)는 이번 계획에서 제외
- 관례: 기존과 동일 — `stepN/kebab-name` 브랜치를 master에서 분기 → 작고 의미 있는 커밋 →
  PR로 master 머지 (`커밋은 사용자 승인 후에만`)

## 가로지르는 설계 원칙 (모든 방어 브랜치 공통)

1. **baseline은 지우지 않는다 — 방어는 additive.**
   각 방어를 별도의 예약 경로로 구현해 baseline과 나란히 남긴다. 그래야 Gatling이 각 방식을
   따로 때려 before/after를 비교할 수 있다. 후속 브랜치가 앞선 구현을 덮어쓰지 않는다.
   - 권장: `ReservationStrategy` 인터페이스 + 구현체 여러 개, 요청 경로/파라미터로 선택
     (예: `POST /reservations/{strategy}`). 1단계 `ReservationService.reserve()`는 `baseline`
     전략으로 보존.
2. **1단계 재현 자산을 훼손하지 않는다.**
   `ReservationService`(락 없는 버전), `BaselineOverbookingProbeTest`, `V1` 마이그레이션,
   `docs/STEP1-*`는 그대로 둔다. CLAUDE.md의 1단계 불변식(락/synchronized/UNIQUE 추가 금지)은
   baseline 경로에 계속 적용된다.
   - ⚠️ **주의(① UNIQUE 관련):** baseline 재현은 *서로 다른 지원자*의 정원 경쟁(오버부킹)이라
     `UNIQUE(applicant_id, slot_id)`가 그걸 깨지 않는다(같은 쌍 중복이 아님). `BaselineOverbookingProbeTest`도
     라운드마다 새 슬롯이라 (지원자, 슬롯) 쌍이 항상 유일하다. 따라서 UNIQUE는 전역(V2)으로
     걸어도 baseline 재현과 충돌하지 않는다 — 브랜치 진입 시 재확인할 것.
3. **Flyway는 append-only.** 스키마 변경은 새 `V{n}__*.sql`로만 추가한다. V1을 수정하지 않는다.
4. **각 브랜치 산출물 = 코드 + 테스트 + 문서 한 조각.**
   방어를 넣을 때마다 해당 방어가 무슨 문제를 없앴는지 `docs/`에 근거를 남긴다(벤치마크 수치는
   `step2/benchmark`에서 종합).

## 브랜치 목록과 순서 (위 → 아래 순서로 진행)

### 2-1. 가벼운 방어 (순서 고정)

**① `step2/unique-constraint`** — 중복 예약 DB 최후 방어선
- `V2__add_unique_reservation.sql`: `reservation`에 `UNIQUE(applicant_id, slot_id)` 추가
- 위반 시 나는 `DataIntegrityViolationException`을 서비스에서 잡아 도메인 예외로 변환
- 테스트: 같은 (지원자, 슬롯) 중복 예약이 DB 레벨에서 차단됨을 단언
- 문서: 애플리케이션 버그가 있어도 DB가 막는다는 점 기록

**② `step2/conditional-update`** — 오버부킹 방지 (스키마 변경 없음)
- `InterviewSlotRepository`에 원자적 조건부 UPDATE 추가:
  `UPDATE interview_slot SET remaining = remaining - 1 WHERE id = ? AND remaining > 0`
- 새 예약 전략에서 이 쿼리 사용, 영향 행 수 0이면 만석 처리
- 테스트: 동시 경쟁에서 확정 예약 수 == capacity (오버부킹 0)
- 문서: 락/데드락 없이 원자적 연산 하나로 오버부킹이 사라짐을 baseline과 대비

**③ `step2/idempotency-key`** — 같은 사용자의 중복 요청(더블클릭/재시도)
- 권장: Redis `SETNX` + TTL (Redis 이미 스택에 있음 → 마이그레이션 불필요).
  대안으로 요청 ID 저장 테이블(`V3`)도 가능 — 채택 시 이후 마이그레이션 번호가 밀림
- 동일 키 재요청 시 기존 결과 반환, 첫 요청 처리 중 재요청 처리 방식 결정
- 테스트: 같은 멱등성 키로 두 번 요청해도 예약은 1건
- 문서: **멱등성 키(요청 중복) ≠ 조건부 UPDATE/락(데이터 레이스)** 구분을 명시 —
  락은 중복 요청을 못 막는다는 점이 포트폴리오 핵심 논거

### 2-2. 락 3종 비교 (조건부 UPDATE만으로 부족해지는 지점을 보여주는 용도)

**④ `step2/pessimistic-lock`** — `@Lock(PESSIMISTIC_WRITE)` → `SELECT ... FOR UPDATE`
- 스키마 변경 없음. 슬롯 조회에 비관적 쓰기 락, 임계 구역 보호
- 테스트: 오버부킹 0. 문서: 다단계 트랜잭션에서의 명시적 락, 데드락/처리량 트레이드오프 관찰

**⑤ `step2/optimistic-lock`** — `@Version` + 재시도(지수 백오프)
- `V3__add_version_to_slot.sql`(또는 ③에서 테이블 썼다면 `V4`): `interview_slot.version` 추가
- `OptimisticLockException` 시 지수 백오프 재시도. **재시도 횟수 상한 필수**
- 트러블슈팅 회고 소재: "무한 재시도 → 횟수 제한 + 백오프" (README 5장 3번 항목)
- 테스트: 오버부킹 0 + 재시도 상한 동작 확인

**⑥ `step2/distributed-lock`** — Redis + Redisson `RLock`
- Redisson 의존성/설정 추가, 슬롯 키 기준 분산 락으로 임계 구역 보호
- 다중 인스턴스 환경 명분. TTL/watchdog 설계를 문서에 기록(면접 질문 6장 대비)
- 테스트: 오버부킹 0

### 2-3. 벤치마크 종합

**⑦ `step2/benchmark`** — before/after 정량화
- `BaselineReservationSimulation`을 재사용/파라미터화해 baseline·①~⑥ 각 전략에 동일 부하 인가
- 지표: TPS, 평균/최대 응답시간, 실패율, 데이터 정합성(오버부킹·중복 수)
- 산출물: `docs/STEP2-DEFENSE-BENCHMARK.md` — 표 + 그래프, baseline 대비 각 방식 비교

### 3단계. 트레이드오프 분석 및 최종 선택

**⑧ `step3/tradeoff-analysis`** — 문서 중심 (코드 변경 최소)
- 각 방식 장단점·적합 상황 정리, 이 프로젝트 규모(수십~수백 명)의 현실적 선택 결정
- "트래픽 100배" 확장 시나리오, 선택 근거를 README에 문서화 (PROJECT_PLAN 3·5장 반영)
- `PROJECT_PLAN.md` 7장 체크박스 및 진행 상황 갱신

## 병합 순서 요약

```
master
 ├─ step2/unique-constraint      (V2)         → PR → master
 ├─ step2/conditional-update     (코드)        → PR → master
 ├─ step2/idempotency-key        (Redis/or V3) → PR → master
 ├─ step2/pessimistic-lock       (코드)        → PR → master
 ├─ step2/optimistic-lock        (V3/V4)       → PR → master
 ├─ step2/distributed-lock       (Redisson)    → PR → master
 ├─ step2/benchmark              (docs)        → PR → master
 └─ step3/tradeoff-analysis      (docs/README) → PR → master
```
각 브랜치는 **직전 브랜치가 master에 머지된 뒤** master에서 새로 분기한다(방어가 누적돼야
벤치마크에서 나란히 비교 가능). 마이그레이션 번호는 앞 브랜치 머지 순서에 의존하므로,
분기 직전 master 기준으로 다음 `V{n}`을 확정한다.

## Verification (각 방어 브랜치 공통 절차)

1. `docker compose up -d` (MySQL:3306 / Redis:6379)
2. `./gradlew test` — 해당 브랜치의 동시성 통합 테스트 통과(오버부킹/중복 0 단언)
3. `./gradlew bootRun` 후 `./gradlew gatlingRun`으로 해당 전략에 부하 인가
   (`-Dcapacity=1 -Dcontenders=200` 등으로 최악 인터리빙 강제)
4. baseline 대비 데이터 정합성(오버부킹·중복 수)과 성능 지표 변화를 캡처해 문서에 기록
5. `step2/benchmark`에서 전 전략을 동일 조건으로 재측정해 종합 표/그래프 작성

## 범위 밖 (이번 계획 제외)

- 4단계: 비동기 알림(@Async→Kafka/RabbitMQ), CI/CD(GitHub Actions), 배포/데모 링크
  → 2·3단계 완료 후 `step4/*`로 별도 계획
