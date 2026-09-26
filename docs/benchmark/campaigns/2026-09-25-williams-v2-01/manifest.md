# Benchmark Campaign 2026-09-25-williams-v2-01

> 당시 실행 환경과 명령의 기록이다. 현재 실행 명령은 [재현 절차](../../../STEP2-DEFENSE-BENCHMARK.md#9-재현), 원시 결과는 [200회 CSV](../../raw-runs-v2.csv)를 따른다.

## Campaign

- campaign_id: 2026-09-25-williams-v2-01
- rounds_per_phase: 10
- schedule_version: williams-10-v1
- predecessor_methodology: methodology-v1-fixed-order
- predecessor_raw_runs: [docs/benchmark/archive/2026-09-24/raw-runs.csv](../../archive/2026-09-24/raw-runs.csv)
- predecessor_execution_record: [docs/benchmark/archive/2026-09-24/2026-09-24-controlled-run.md](../../archive/2026-09-24/2026-09-24-controlled-run.md)
- created_at: 2026-09-25T01:10:26
- gatling_version: 3.13.5

## Phase A

- phase_a_invocation: scripts/benchmark.sh --campaign-id 2026-09-25-williams-v2-01 --phase a --rounds 10
- phase_a_status: complete
- phase_a_app_start_id: 2026-09-25-williams-v2-01-phase-a
- phase_a_environment_path: docs/benchmark/campaigns/2026-09-25-williams-v2-01/environment-phase-a.json
- phase_a_environment_sha256: 4472ebb50581d14bf201f2ba73c5b520dcbf5a171169a1e2a2c6ec8d92f2c863
- phase_a_expected_max_attempts: 5
- phase_a_git_head: fee296c216e42cf60c06f6acd9390ae3a16608ed
- phase_a_os: Darwin seosanghyeog-ui-MacBookAir.local 25.6.0 Darwin Kernel Version 25.6.0: Fri Jul 31 19:18:29 PDT 2026; root:xnu-12377.161.14~5/RELEASE_ARM64_T8122 arm64
- phase_a_java: openjdk version "21.0.9" 2025-10-21 LTS
- phase_a_gradle: Gradle 8.14.5
- phase_a_mysql: 8.0.46
- phase_a_max_connections: 151
- phase_a_initial_slot_count: 245
- phase_a_initial_reservation_count: 8578
- phase_a_started_at: 2026-09-25T01:10:26
- phase_a_completed_at: 2026-09-25T01:20:03
- phase_a_warmup_status: complete
- phase_a_measured_status: complete
- phase_a_measured_rows: 100
- phase_a_measurement_started_at: 2026-09-25T01:11:25
- phase_a_measurement_completed_at: 2026-09-25T01:20:03
- phase_a_failure_reason: none

## Phase B

- phase_b_attempted: true
- phase_b_status: complete
- phase_b_invocation: scripts/benchmark.sh --campaign-id 2026-09-25-williams-v2-01 --phase b --rounds 10
- phase_b_app_start_id: 2026-09-25-williams-v2-01-phase-b
- phase_b_environment_path: docs/benchmark/campaigns/2026-09-25-williams-v2-01/environment-phase-b.json
- phase_b_environment_sha256: 98150f591c03da90cd76ed7660c4820109bfb1ef38f874a68f1fd6db5cebad76
- phase_b_expected_max_attempts: 20
- phase_b_git_head: fee296c216e42cf60c06f6acd9390ae3a16608ed
- phase_b_os: Darwin seosanghyeog-ui-MacBookAir.local 25.6.0 Darwin Kernel Version 25.6.0: Fri Jul 31 19:18:29 PDT 2026; root:xnu-12377.161.14~5/RELEASE_ARM64_T8122 arm64
- phase_b_java: openjdk version "21.0.9" 2025-10-21 LTS
- phase_b_gradle: Gradle 8.14.5
- phase_b_mysql: 8.0.46
- phase_b_max_connections: 151
- phase_b_initial_slot_count: 355
- phase_b_initial_reservation_count: 11212
- phase_b_started_at: 2026-09-25T01:21:12
- phase_b_completed_at: 2026-09-25T01:31:16
- phase_b_warmup_status: complete
- phase_b_measured_status: complete
- phase_b_measured_rows: 100
- phase_b_measurement_started_at: 2026-09-25T01:22:09
- phase_b_measurement_completed_at: 2026-09-25T01:31:16
- phase_b_failure_reason: none
