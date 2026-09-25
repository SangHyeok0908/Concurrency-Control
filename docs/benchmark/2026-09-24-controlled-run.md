# 2026-09-24 통제 재측정 실행 기록

이 파일은 서로 다른 지원자가 슬롯 정원을 경쟁한 [`raw-runs.csv`](raw-runs.csv) 60행과
[`optimistic-attempt-distribution-cap20.json`](optimistic-attempt-distribution-cap20.json)이 어떤
환경에서 만들어졌는지 고정한다. 이전 기본 프로필 측정은
[`archive/2026-09-04-manifest.md`](archive/2026-09-04-manifest.md)와 함께 별도 보존한다.

이 workload는 요청마다 다른 applicant를 사용한다. 따라서 원시 CSV의 `duplicates=0`은 동일
`(applicant_id, slot_id)` 재요청 방어를 검증한 값이 아니다. 그 질문은
[2026-09-25 동일 요청 실행 기록](2026-09-25-duplicate-request-run.md)과
[`duplicate-runs.csv`](duplicate-runs.csv)에서 별도로 검증한다.

## 실행 환경

| 항목 | 값 |
|---|---|
| 측정 시각 | 2026-09-24 16:35–16:41 KST |
| 측정 전 Git HEAD | `3068c0d4b7a59c77495f4c8d527488753415ae3a` + 이 변경의 미커밋 benchmark 프로필·검증 코드 |
| OS | macOS 26.6.2, arm64 (Darwin 25.6.0) |
| Java | Amazon Corretto 21.0.9 |
| Gradle | 8.14.5 (wrapper) |
| Spring Boot | 3.5.16 |
| Gatling | 3.13.5.4 |
| MySQL | 8.0.46, Docker `mysql:8.0`, `max_connections=151` |
| 측정 시작 DB | 슬롯 184행, 예약 6,863행 누적 |

## 애플리케이션 통제값

두 Phase 모두 `spring.profiles.active=benchmark` 하나만 활성화했다. 측정 스크립트는 부하를 넣기
전에 `/api/metrics/benchmark-environment` 응답을 검사했고, 아래 값이 하나라도 다르면 CSV를 만들기
전에 중단한다.

| 항목 | 통제값 |
|---|---|
| Hikari maximum / minimum idle / 준비된 연결 | 100 / 100 / 100 |
| Hibernate `show_sql` / `format_sql` / `use_sql_comments` | `false` / `false` / `false` |
| root / Hibernate SQL / bind logger | `OFF` / `OFF` / `OFF` |
| 백오프 | exponential jitter, base 10ms, max 200ms |
| Phase A 낙관적 최대 시도 | 5 |
| Phase B 낙관적 최대 시도 | 20 |

풀 100은 가장 큰 성공 경합 지점의 100개 쓰기가 DB까지 경쟁하도록 하되 MySQL 관리 연결 여유를
51개 남긴 값이다. minimum idle도 100으로 맞추고 실제 연결이 모두 생성된 뒤 측정해, 첫 전략에만
커넥션 생성 비용이 붙지 않게 했다. root logger까지 끈 이유는 락 없는 전략의 데드락 스택트레이스가
전략마다 다른 콘솔 I/O 비용으로 섞이는 것을 막기 위해서다.

## 실행 순서

```bash
./gradlew bootRun --args='--spring.profiles.active=benchmark'
scripts/benchmark.sh --phase a --rounds 5

# 첫 JVM과 8080 포트가 종료된 것을 확인한 뒤 새 JVM으로 실행
./gradlew bootRun --args='--spring.profiles.active=benchmark --reservation.optimistic.max-attempts=20'
scripts/benchmark.sh --phase b --rounds 5
```

Phase A는 5전략 × 2경합 × 5회 = 50행, Phase B는 낙관적 락 × 2경합 × 5회 = 10행이다.
60행 모두 서로 다른 지원자의 정원 경쟁이며 동일 요청 workload와 합산하지 않는다.
전략을 5회씩 몰아 실행하지 않고 라운드마다 한 번씩 배치했지만, 라운드 안의 전략 순서는
`baseline → unique → conditional → pessimistic → optimistic`으로 고정했다. 따라서 장기적인
워밍업 편향은 줄였어도 순서 효과를 완전히 무작위화한 실험은 아니다.

## 산출물 무결성

- `raw-runs.csv` SHA-256:
  `7824f10229b460e911a49f47c46bb8d6338587de1401709c09a8e919ab1ed3c3`
- `optimistic-attempt-distribution-cap20.json` SHA-256:
  `f193d7510ac6ce02934b99aa7c06232541f7917ba5639b9565a818d587535698`

CSV의 각 12개 `(전략, 재시도 상한, 경합)` 조합은 정확히 5행이다. 분포 JSON은 Phase B 마지막
`cap=100, contenders=120` 실행(슬롯 243)의 환경 카운터 원문이다.
