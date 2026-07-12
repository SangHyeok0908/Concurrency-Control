# 1단계 baseline — Gatling HTTP 부하 테스트

> 서비스-계층 프로브([STEP1-BASELINE-OVERBOOKING.md](STEP1-BASELINE-OVERBOOKING.md))가 `reserve()`를
> 직접 호출해 오버부킹을 재현했다면, 이 문서는 **실제 HTTP 엔드포인트 `POST /api/reservations`에
> Gatling으로 부하를 걸어** 같은 버그를 재현하고, 동시에 2단계 before/after 비교의 기준선이 될
> **처리량·응답시간·실패율**을 측정한 기록이다. 전체 계획은 `PROJECT_PLAN.md` 4·5장이 정본이다.

관련 코드: [`BaselineReservationSimulation`](../src/gatling/java/com/interview/reservation/loadtest/BaselineReservationSimulation.java) ·
[`SeedState`](../src/gatling/java/com/interview/reservation/loadtest/SeedState.java) ·
[`ReservationController`](../src/main/java/com/interview/reservation/web/ReservationController.java)

---

## 1. 한 줄 요약

정원 100짜리 슬롯 하나에 서로 다른 지원자 500명이 HTTP로 동시에 예약을 시도하면, 락 없는 baseline은
**정원을 넘겨 확정(오버부킹)** 하고, `remaining` 카운터가 **lost update로 망가지며**, 요청의 다수가
**데드락(HTTP 500)** 으로 죽는다. 서비스-계층 프로브에서 간헐적이던 버그가, HTTP 지연이 경쟁 창을
넓히자 **한 번의 실행에서 세 가지 실패 모드가 한꺼번에** 드러났다.

---

## 2. 실험 설계

| 항목 | 값 | 이유 |
|---|---|---|
| 대상 | `POST /api/reservations` (실 HTTP, 앱 `:8080`) | 서비스-계층이 아닌 실제 엔드포인트 처리량·실패율 측정 |
| `capacity` | 100 | 마지막 한 자리(정원 1)보다 완만하지만, 500 경쟁이면 충분히 초과가 난다 |
| `contenders` | 500 | `PROJECT_PLAN` 1단계의 "동시 100~500". 서로 다른 지원자 500명 |
| 주입 방식 | `atOnceUsers(500)` 버스트 | 마지막 자리 경쟁을 최대화 |
| 시드 | 슬롯 1개 + 지원자 500명 선(先)생성 후 `andThen` | 라운드 오염 없이 순수 정원 경쟁 관찰 |

**설계 판단 — 프로브와 같은 철학.** 오버부킹은 Gatling 리포트의 pass/fail이 아니라 **DB 확정 행 수**로
증명한다. 그래서 시뮬레이션에 assertion을 걸지 않는다. 또 `status().in(201, 409)`를 OK로 간주해
**409(정원 마감)는 정상 거절로 실패에서 제외**하고, 500(데드락)·타임아웃만 KO로 남겨 실패율이 의미를
갖게 했다.

시딩·부하 시나리오 간 식별자(슬롯 id·지원자 id)는 Gatling 세션 밖 프로세스 내 홀더([`SeedState`](../src/gatling/java/com/interview/reservation/loadtest/SeedState.java))로
공유한다 — Gatling이 단일 JVM에서 돌고 부하 단계가 `andThen`으로 시드 뒤에 실행되기 때문에 안전하다.

---

## 3. 관찰 결과 — 대표 실행 (수치는 실행마다 다르다)

### 3-1. Gatling 리포트

```
---- Requests --------------------------------|---Total---|-----OK----|----KO----
> Global                                      |     1,001 |       614 |       387
> create slot                                 |         1 |         1 |         0
> create applicant                            |       500 |       500 |         0
> reserve                                     |       500 |       113 |       387   <-- 부하 대상
---- Errors ----------------------------------------------------------------------
> status.find.in(201,409), found 500                                387   (100%)

---- Global Information ----------------------|---Total---|-----OK----|----KO----
> mean throughput (rps)                       |     200.2 |     122.8 |      77.4
> mean response time (ms)                      |       391 |       135 |       799
> response time p95 (ms)                      |     1,126 |       941 |     1,163
> response time p99 (ms)                      |     1,172 |     1,166 |     1,198
> max response time (ms)                      |     1,215 |     1,215 |     1,209
```

- **`reserve` 500건 → OK 113, KO 387.** KO 387건은 **전부 HTTP 500**(`status.find.in(201,409), found 500`).
  이 500은 [STEP1-BASELINE §5](STEP1-BASELINE-OVERBOOKING.md)의 **FK S→X 승격 데드락**이 미처리
  예외로 올라온 것이다. 서비스-계층에서 관찰되던 데드락이 500 동시성에서 **응답의 77%(387/500)** 로 폭증했다.
- KO 응답시간(평균 799ms, p95 1.16s)이 OK보다 훨씬 길다 — 데드락 **탐지까지 대기**한 시간이다.
- 전역 지표는 시드의 빠른 지원자 500건(단일 사용자·순차·수 ms)과 느린 reserve 버스트가 섞인 값이다.

### 3-2. DB 정합성 — 부하 후 조회

```sql
SELECT s.id, s.capacity, s.remaining, COUNT(r.id) AS confirmed
FROM interview_slot s
LEFT JOIN reservation r ON r.slot_id = s.id AND r.status = 'CONFIRMED'
GROUP BY s.id, s.capacity, s.remaining;
```

| slot_id | capacity | remaining | confirmed | 판정 |
|---|---|---|---|---|
| 2 | 100 | **40** | **113** | **오버부킹 +13 & lost update** |

여기서 **두 가지가 동시에** 깨져 있다.

1. **오버부킹.** 확정 예약 113건 > 정원 100 → 13건 초과.
2. **카운터 lost update.** `remaining=40`은 감소가 **60번만** 반영됐다는 뜻인데, 확정 행은 113건이다.
   서로 다른 트랜잭션이 같은 `remaining`을 읽고 `remaining-1`을 덮어써 **갱신이 유실**됐다. 예약 행 수와
   카운터가 **서로 다른 값으로 둘 다 틀렸다.**

### 3-3. 가장 날카로운 관찰 — 409가 한 건도 없었다

`reserve` OK 113건은 **전부 201**이고 **409(정원 마감)는 0건**이었다. lost update로 `remaining`이 40에
갇힌 채 **한 번도 0에 닿지 못했기 때문**에, 애플리케이션은 슬롯이 꽉 찼다는 사실 자체를 **인지하지
못했다.** 모든 요청은 "확정" 아니면 "데드락"으로 갈렸을 뿐, 정상적인 "마감 거절"은 존재하지 않았다.
카운터가 진실을 말하지 못하는 상태 — 이것이 락 없는 baseline의 본질적 위험이다.

---

## 4. 서비스-계층 프로브와의 차이

| | 서비스-계층 프로브 (JUnit) | HTTP 부하 (Gatling) |
|---|---|---|
| 경쟁 창 | 트랜잭션 <1ms → **간헐** 재현 | HTTP·직렬화 지연으로 **창이 넓어져** 상시 재현 |
| 관찰된 것 | 오버부킹(간헐) + 데드락 | 오버부킹 + **lost update 카운터 붕괴** + 데드락 폭증 |
| 지표 | 확정 행 수(불변식) | + TPS·응답시간·실패율(2단계 기준선) |

[STEP1-BASELINE §7](STEP1-BASELINE-OVERBOOKING.md)이 예고한 "HTTP로 옮기면 창이 넓어져 더 잘
재현된다"가 그대로 확인됐다.

---

## 5. 재현 방법

```bash
docker compose up -d          # MySQL:3306 / Redis:6379
./gradlew bootRun             # 별도 터미널 — 앱이 :8080에 떠 있어야 HTTP 부하가 걸린다
./gradlew gatlingRun          # 기본: capacity=100, contenders=500
# 오버라이드(시스템 프로퍼티는 포크된 Gatling JVM으로 전달됨):
#   ./gradlew gatlingRun -Dcapacity=1 -Dcontenders=200
```

리포트: `build/reports/gatling/<sim>-<timestamp>/index.html` (TPS·응답시간 분포·상태코드).
오버부킹 증거는 위 3-2 SQL로 확인한다(`confirmed > capacity` 또는 `remaining < 0`).

> **빌드 메모.** Gatling은 **3.14부터 Netty 4.2**로 올라가는데, Spring Boot 3.5.16 BOM은 Netty를
> **4.1**로 못박아 충돌한다(`gatlingRun`이 `NoClassDefFoundError`로 죽음). 그래서 Gatling을 **아직
> Netty 4.1인 3.13 라인(3.13.5.4)** 으로 고정했다 — BOM과 그대로 정렬돼 의존성 조정 코드가 전혀
> 필요 없다(Java 21·Java DSL 모두 지원). 최신 3.14/3.15를 쓰려면 gatling configuration의 Netty만
> 4.2로 되돌리는 `resolutionStrategy`가 필요한데, 그 하드코딩을 피하려고 3.13을 택했다
> ([build.gradle](../build.gradle) 참고).

---

## 6. 한계와 다음 단계

- **수치는 하드웨어·타이밍 의존적**이며 실행마다 다르다. 요점은 절대치가 아니라 "세 실패 모드가 실재하고,
  HTTP 부하에서 상시로 드러난다"는 성질이다.
- **데드락은 baseline의 부작용이지 방어가 아니다.** 오히려 정상 거절(409)조차 못 만드는 카운터 붕괴가
  본질 문제다.
- **2단계 방어**는 `PROJECT_PLAN.md` 3장 순서(UNIQUE → 조건부 UPDATE → 멱등성 키 → 락)로 도입하고,
  **이 시뮬레이션을 그대로 재사용**해 동일 부하에서 before/after를 비교한다. 조건부
  UPDATE(`SET remaining = remaining - 1 WHERE remaining > 0`)는 읽기·쓰기를 원자적 한 방으로 합쳐
  오버부킹·lost update·S→X 데드락을 동시에 없앤다.
