# 2·3단계 브랜치 전략 (선착순 예약 동시성 제어 포트폴리오)

## 진행 상황 (이 표가 2·3단계 진행의 정본)

진행 상태의 정본은 이 표다. 상태를 갱신할 때도 이 표만 갱신한다.

| | 브랜치 | 상태 | 산출물 |
|---|---|---|---|
| ① | `step2/unique-constraint` | ✅ 완료 (2026-07-13, PR #2) | [UNIQUE](STEP2-UNIQUE-CONSTRAINT.md) · `V2` |
| ② | `step2/conditional-update` | ✅ 완료 (2026-07-13, PR #3) | [조건부 UPDATE](STEP2-CONDITIONAL-UPDATE.md) |
| ③ | ~~`step2/idempotency-key`~~ | ❌ **생략** (2026-07-17, [근거](#skip-idempotency-key)) | — |
| ④ | `step2/pessimistic-lock` | ✅ 완료 (2026-07-18, PR #4) | [비관적 락](STEP2-PESSIMISTIC-LOCK.md) |
| ⑤ | `step2/optimistic-lock` | ✅ 완료 (2026-07-18, 백오프 실측 2026-07-19) | [낙관적 락](STEP2-OPTIMISTIC-LOCK.md) · `V3` |
| ⑥ | ~~`step2/distributed-lock`~~ | ❌ **생략** (2026-09-04, [근거](#skip-distributed-lock)) | — |
| ⑦ | `step2/benchmark` | ✅ 완료 (통제 재측정 2026-09-24, 동일 요청 검증 2026-09-25) | [방어 전략 벤치마크](STEP2-DEFENSE-BENCHMARK.md) · [정원 경쟁 60행](benchmark/raw-runs.csv) · [동일 요청 25행](benchmark/duplicate-runs.csv) |
| ⑧ | `step3/tradeoff-analysis` | ✅ 완료 (2026-09-23) | [README 트레이드오프와 최종 선택](../README.md#트레이드오프와-최종-선택) |

## 실험의 기준

1단계는 락 없는 baseline으로 오버부킹·lost update·데드락을 재현한 증거물이다. 이후 방어는 가벼운
도구부터 추가했고, 목표는 모든 경로를 같은 부하에서 비교해 선택 근거를 남기는 것이었다.

### baseline은 보존하고 방어는 additive

`ReservationService`의 락 없는 baseline, `BaselineOverbookingProbeTest`, `V1`, `docs/STEP1-*`는
그대로 보존한다. 방어 전략은 별도 경로로 나란히 추가해 동일 Gatling 시나리오가 baseline과 각 방어를
모두 비교하게 한다. 따라서 후속 전략은 앞선 전략이나 baseline을 덮어쓰지 않는다. baseline 경로에는
[AGENTS.md](../AGENTS.md)의 1단계 불변식이 계속 적용된다.

`UNIQUE(applicant_id, slot_id)`는 서로 다른 지원자들의 정원 경쟁을 막지 않으므로 baseline의
오버부킹 증거와 충돌하지 않는다. 스키마 변경은 append-only Flyway migration으로만 남긴다.

<a id="skip-idempotency-key"></a>
## ③ (생략) 멱등성 키

**결정(2026-07-17): 구현하지 않는다.** 예약에서는 `(applicant_id, slot_id)` 자연 키가 요청의
정체성이고 ①의 UNIQUE가 같은 쌍을 결정적으로 막는다. 별도 키나 Redis `SETNX`는 같은 판정을
중복할 뿐이다. 응답 유실 뒤 재시도에 기존 결과를 반환하는 개선은 남지만, 이는 동시성 제어가 아니라
API 응답 계약의 문제다.

이 판단은 결제처럼 같은 요청을 자연 키로 식별할 수 없거나, 같은 지원자의 복수 예약을 허용할 때만
재검토한다.

<a id="lock-experiment-control"></a>
## 락 실험 통제 원칙

④·⑤는 조건부 UPDATE가 이미 정합성을 보장하는 동일한 슬롯 경쟁을 대조군으로 측정한다. 각 락에
유리한 장기 트랜잭션·비-DB 자원·다중 인스턴스 시나리오를 새로 만들지 않는다. 그래야 벤치마크의
각 행이 다른 실험이 아니라 같은 문제에 대한 비교가 된다.

경합 강도는 시나리오를 바꾸지 않고 `capacity`와 `contenders`만 스윕한다. 낙관적 락은 성공 쓰기
횟수가 많을수록 충돌하므로, `cap=100 cont=120`이 그 전략에는 더 불리하고 `cap=1 cont=200`은
더 쉽다는 해석을 함께 남긴다. 최신 결과와 실행 환경은 [방어 전략 벤치마크](STEP2-DEFENSE-BENCHMARK.md)에
있다.

<a id="skip-distributed-lock"></a>
## ⑥ (생략) 분산 락

**결정(2026-09-04): 구현하지 않는다.** 이 도메인의 임계 구역은 단일 MySQL 행의 조건부 UPDATE로
끝나며, 공유 상태도 DB 하나에만 있다. 인스턴스를 늘려도 그 원자성은 유지되므로 Redisson이 지킬
새 불변식이 없다. 같은 시나리오에 분산 락을 얹는 것은 Redis 왕복 비용만 더한 비교가 되어 실험
근거가 되지 않는다.

외부 결제·다른 저장소·정확히 한 번 보내야 하는 알림처럼 임계 구역이 DB 밖으로 확장되거나, 여러
인스턴스의 단일 실행 작업을 조율해야 할 때 이 결정을 재검토한다. 그때의 문제는 현재 예약 경로와
별도로 다룬다.

## 현재 결과와 후속 범위

②·④·⑤는 모두 오버부킹 0을 달성했다. 통제 재측정에서 ②와 ④의 낮은 경합 중앙값은 모두 136ms였고,
극단 경합은 ④가 5/5 빨랐다. ⑤는 상한 5에서 중앙값 13석·503 응답 107건, 상한 20에서 평균 응답 중앙값
520ms·TPS 137.6으로, 상한에 따라 가용성 또는 재시도 비용을 지불했다. 최종 선택과 원시 실행값은
[방어 전략 벤치마크](STEP2-DEFENSE-BENCHMARK.md), 최종 해석은 [README 트레이드오프와 최종 선택](../README.md#트레이드오프와-최종-선택)을 정본으로 한다.

후속 범위는 현재 한 행 산술 가드를 넘어서는 다중 행 불변식, 외부 자원, 취소·재예약 같은 도메인
확장이다. 그 변화가 실제로 생길 때 해당 도구의 전제와 측정을 새로 정의한다.
