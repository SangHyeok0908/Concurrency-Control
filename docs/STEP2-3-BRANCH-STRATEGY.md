# 2·3단계 브랜치 전략 (선착순 예약 동시성 제어 포트폴리오)

> **새 세션에서 브랜치 하나를 작업할 때 이 문서를 정본으로 삼는다.**
> 시작 멘트 예시: `step2/pessimistic-lock 브랜치 작업 시작. docs/STEP2-3-BRANCH-STRATEGY.md의 ④를 따른다.`
> 각 브랜치의 세부 구현은 그 브랜치 진입 시점에 별도로 설계한다.

## 진행 상황 (이 표가 2·3단계 진행의 정본)

다른 문서(README·PROJECT_PLAN·CLAUDE.md)는 여기를 가리키기만 한다. 브랜치를 머지할 때
**이 표만 갱신**한다.

| | 브랜치 | 상태 | 산출물 |
|---|---|---|---|
| ① | `step2/unique-constraint` | ✅ 완료 (2026-07-13, PR #2) | [STEP2-UNIQUE-CONSTRAINT.md](STEP2-UNIQUE-CONSTRAINT.md) · `V2` |
| ② | `step2/conditional-update` | ✅ 완료 (2026-07-13, PR #3) | [STEP2-CONDITIONAL-UPDATE.md](STEP2-CONDITIONAL-UPDATE.md) |
| ③ | ~~`step2/idempotency-key`~~ | ❌ **생략** (2026-07-17, [근거](#skip-idempotency-key)) | — |
| ④ | `step2/pessimistic-lock` | ✅ 완료 (2026-07-18, PR #4) | [STEP2-PESSIMISTIC-LOCK.md](STEP2-PESSIMISTIC-LOCK.md) |
| ⑤ | `step2/optimistic-lock` | ⏳ **다음 작업** | — |
| ⑥ | `step2/distributed-lock` | ⬜ 예정 | — |
| ⑦ | `step2/benchmark` | ⬜ 예정 | `docs/STEP2-DEFENSE-BENCHMARK.md` |
| ⑧ | `step3/tradeoff-analysis` | ⬜ 예정 | README 트레이드오프 섹션 |

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

<a id="skip-idempotency-key"></a>
#### ③ (생략) `step2/idempotency-key` — 왜 하지 않는가

**결정(2026-07-17): 구현하지 않는다.** 계획서 3장은 멱등성 키를 방어 3순위로 올려놨지만,
①을 끝내고 나서 다시 따져 보니 **이 도메인에서는 명분이 서지 않는다.** 근거는 두 가지다.

**1. 자연 키가 이미 멱등성 키다.** 멱등성 키가 실무에서 꼭 필요한 연산(결제·송금)은 요청 내용만으로
"같은 요청"인지 판별할 수 없다 — 5만원 송금 두 번은 정당한 두 건일 수 있으니, 클라이언트가 별도 키를
발급해 "이건 아까 그 요청"이라고 알려줘야 한다. 반면 예약은 **(지원자, 슬롯) 쌍 자체가 요청의 정체성**이다.
같은 쌍의 두 번째 요청은 정의상 언제나 중복이다. 별도 키를 발급받을 이유가 없고, 그 자연 키에는 이미
①에서 `UNIQUE(applicant_id, slot_id)`가 걸려 있다.

**2. 원래의 명분은 ①에서 이미 소진됐다.** 계획서가 내세운 논거는 "락 3종만으로는 중복 요청을 못 막는다"였다.
맞는 말이지만 그 공백을 메운 것은 멱등성 키가 아니라 **UNIQUE 제약**이었다 —
[STEP2-UNIQUE-CONSTRAINT.md](STEP2-UNIQUE-CONSTRAINT.md)의 "동시 중복 20건 → 예약 행 정확히 1건"이
그 증거다. 여기에 Redis `SETNX`를 더 얹으면 포트폴리오의 논지("가장 단순한 도구에서 시작해, 부족해지는
지점에서만 무거운 도구를 꺼냈다")를 **스스로 배반한다.** 자연 키로 충분한 자리에 인프라를 하나 더
끌어오는 셈이기 때문이다. 생략 자체가 그 논지의 실천이다.

**남는 빈틈과 그 크기.** UNIQUE는 두 번째 INSERT를 *거부*할 뿐, 응답을 못 받고 재시도한 클라이언트에게
원래 결과를 돌려주지 않는다. 그 사용자는 실제로 예약에 성공했는데 409를 받는다 — 엄밀히는 틀린 응답이고,
정확한 응답은 `200 + 기존 예약`이다. 다만 이건 **동시성 문제가 아니라 API 응답 설계 문제**이고,
`DataIntegrityViolationException`을 잡은 자리에서 기존 예약을 조회해 반환하면 끝나는 몇 줄짜리 개선이다.
멱등성 *키*를 도입할 근거는 못 된다. 필요해지면 ⑧(트레이드오프 분석)에서 다루거나 별도 브랜치로 뺀다.

**경계선(면접 대비).** 이 판단이 뒤집히는 조건은 명확하다 — 예약이 **결제를 동반**하거나(같은 쌍의 재요청이
카드 승인을 두 번 일으킴), 한 지원자가 **같은 슬롯을 여러 건 예약할 수 있게** 되면(자연 키가 사라짐)
그때는 클라이언트 발급 키 + `SETNX` + TTL이 필요해진다.

> 이 생략은 계획서 3장(방어 계층 표)과 6장(멱등성 면접 질문)의 전제를 바꾼다. 두 문서 모두
> "왜 도입했나"가 아니라 **"왜 도입하지 않기로 판단했나"**로 갱신돼 있다.

### 2-2. 락 3종 비교 (조건부 UPDATE만으로 부족해지는 지점을 보여주는 용도)

<a id="lock-experiment-control"></a>
#### 실험 통제 원칙 (2026-07-17 결정) — 락마다 유리한 시나리오를 만들지 않는다

락 3종은 각자 유리한 상황이 다르다. 그렇다고 **④⑤⑥에서 각 락이 이기는 상황(장기 트랜잭션,
다중 인스턴스, 비-DB 자원 등)을 새로 도입하지 않는다.** 그렇게 하면 ⑦ 벤치마크 표의 각 행이 서로 다른
실험 결과가 되어 비교 자체가 성립하지 않고, "각 도구가 이기도록 상황을 설계해 놓고 각 도구가 이겼다고
말하는" 카탈로그가 된다. 설계 원칙 1번(방어는 additive, **동일 시나리오**로 before/after 비교)과도 어긋난다.

**조건부 UPDATE만으로 충분한 그 상황 그대로 3종을 전부 구현한다.** "셋 다 이 상황에서는 이기지 못했다"는
결론이 있는 실험이고, ⑧에서 "그래서 조건부 UPDATE를 골랐다"를 데이터로 뒷받침한다. 각 락의 장점은 코드가
아니라 아래 두 축으로 드러낸다.

1. **경합 강도(파라미터 스윕).** 시나리오는 그대로 두고 `-Dcapacity` / `-Dcontenders`만 바꾼다.
   낮은 경합과 극단 경합 두 지점에서 재면 낙관적 락의 재시도 폭주와 비관적 락의 대기열 특성이 숫자로 갈린다.
   이건 상황 조작이 아니라 같은 실험의 정당한 파라미터 스윕이다. 측정은 ⑦에서 종합한다.
2. **경계선 문서화(못 재는 건 재지 않는다).** 이 프로젝트는 단일 MySQL이라 앱 인스턴스를 늘려도 조건부
   UPDATE가 여전히 이긴다 — Redisson의 명분(공유 상태가 DB 밖에 있음)은 여기서 실측할 수 없다.
   억지 시나리오 대신 [③의 "경계선(면접 대비)"](#skip-idempotency-key)과 같은 방식으로 전제를 명시한다.

**④⑤⑥ 각 문서에 넣을 고정 포맷 (세 문장).**
- 이 상황에서 이 락이 **왜 이기지 못하는가** — 측정값으로.
- 이 락이 **이기는 조건은 무엇인가** — 전제로.
- 그 조건이 **이 도메인에 왜 없는가** — 뒤집히는 지점까지 함께.

**④ `step2/pessimistic-lock`** — `@Lock(PESSIMISTIC_WRITE)` → `SELECT ... FOR UPDATE`
- 스키마 변경 없음. 슬롯 조회에 비관적 쓰기 락, 임계 구역 보호
- 테스트: 오버부킹 0. 문서: 다단계 트랜잭션에서의 명시적 락, 데드락/처리량 트레이드오프 관찰
- 위 고정 포맷 3문장 필수. 이기는 조건(경합이 높고 임계 구역이 다단계라 재시도 비용이 큰 경우)을
  명시하되, 이 도메인의 임계 구역은 UPDATE 한 방이라 그 조건이 성립하지 않음을 적는다
- **결과(2026-07-18):** 오버부킹 0 달성, 그러나 정합성이 ②와 동률이고 저경합에서 더 느려
  **채택하지 않는다.** 근거는 [STEP2-PESSIMISTIC-LOCK.md](STEP2-PESSIMISTIC-LOCK.md)

**⑤ `step2/optimistic-lock`** — `@Version` + 재시도(지수 백오프)
- `V3__add_version_to_slot.sql`: `interview_slot.version` 추가
  (③을 생략해 마이그레이션이 밀리지 않으므로 `V3`으로 확정)
- `OptimisticLockException` 시 지수 백오프 재시도. **재시도 횟수 상한 필수**
- 트러블슈팅 회고 소재: "무한 재시도 → 횟수 제한 + 백오프" (README 5장 3번 항목)
- 테스트: 오버부킹 0 + 재시도 상한 동작 확인
- 위 고정 포맷 3문장 필수. 이기는 조건(경합이 낮아 충돌이 드문 경우)은 ⑦의 낮은 경합 지점에서
  실측 가능하므로, 그 수치를 근거로 삼는다

**⑥ `step2/distributed-lock`** — Redis + Redisson `RLock`
- Redisson 의존성/설정 추가, 슬롯 키 기준 분산 락으로 임계 구역 보호
- TTL/watchdog 설계를 문서에 기록(면접 질문 6장 대비)
- 테스트: 오버부킹 0
- 위 고정 포맷 3문장 필수. **"다중 인스턴스"를 명분으로 쓰지 않는다** — 인스턴스를 늘려도 단일 DB의
  조건부 UPDATE가 원자성을 유지하므로 그건 명분이 못 된다. 진짜 조건(공유 상태가 DB 밖에 있거나 여러
  자원/DB에 걸쳐 원자성이 필요할 때)을 적고, 이 도메인에 그 조건이 없음을 함께 남긴다

### 2-3. 벤치마크 종합

**⑦ `step2/benchmark`** — before/after 정량화
- `BaselineReservationSimulation`을 재사용/파라미터화해 baseline·①~⑥ 각 전략에 동일 부하 인가
  - ④에서 이미 `-Dstrategy`(경로 선택)와 **요청 이름 이름표**(`reserve [전략 cap=N cont=M]`)를
    넣어 뒀다. 이름표는 `js/stats.js`에 실리므로 리포트를 기계적으로 파싱해 표를 만들 수 있다 —
    이게 없으면 쌓인 리포트가 어느 전략의 측정인지 사후에 알 수 없다(④에서 실제로 겪은 문제)
- 지표: TPS, 평균/최대 응답시간, 실패율, 데이터 정합성(오버부킹·중복 수)
- **②의 거절 경로 최적화 여지 확인 필요.** ④에서 쿼리 수를 재보니 거절 경로가 ② 3개 / ④ 2개로
  ②가 더 든다 — 만석과 없는 슬롯을 구분하려는 `existsById` 때문이다. 방어가 아니라 404/409 구분용
  조회라 제거 가능하며, `capacity=1`처럼 거절이 지배적인 지점의 수치를 왜곡할 수 있다.
  ②를 고치면 이미 머지된 브랜치의 측정 기준이 바뀌므로 **④에서는 손대지 않았다**([근거](STEP2-PESSIMISTIC-LOCK.md))
- **경합 강도 축 (2026-07-17 추가, [실험 통제 원칙](#lock-experiment-control)의 실측 근거).**
  시나리오는 그대로 두고 파라미터만 바꿔 **모든 전략을 두 지점에서** 측정한다. 락 3종이 서로 갈라지는
  유일한 실측 근거이므로 생략하지 않는다.
  - 낮은 경합: `-Dcapacity=100 -Dcontenders=120` — 낙관적 락이 유리해지는 지점
  - 극단 경합: `-Dcapacity=1 -Dcontenders=200` — 기존 최악 인터리빙 조건
  - 낙관적 락은 재시도 횟수 분포도 함께 남긴다(경합에 따른 폭주를 보여주는 지표)
- 산출물: `docs/STEP2-DEFENSE-BENCHMARK.md` — **(전략 × 경합 수준) 2차원 표** + 그래프,
  baseline 대비 각 방식 비교

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
 ├─ (③ step2/idempotency-key — 생략)
 ├─ step2/pessimistic-lock       (코드)        → PR → master
 ├─ step2/optimistic-lock        (V3)          → PR → master
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
   - 극단 경합: `-Dcapacity=1 -Dcontenders=200` (최악 인터리빙 강제)
   - 낮은 경합: `-Dcapacity=100 -Dcontenders=120` — ④⑤⑥에서는 이 지점도 재고 기록한다
     ([실험 통제 원칙](#lock-experiment-control))
4. baseline 대비 데이터 정합성(오버부킹·중복 수)과 성능 지표 변화를 캡처해 문서에 기록
5. `step2/benchmark`에서 전 전략을 두 경합 지점 모두에서 재측정해 종합 표/그래프 작성

## 범위 밖 (이번 계획 제외)

- 4단계: 비동기 알림(@Async→Kafka/RabbitMQ), CI/CD(GitHub Actions), 배포/데모 링크
  → 2·3단계 완료 후 `step4/*`로 별도 계획
