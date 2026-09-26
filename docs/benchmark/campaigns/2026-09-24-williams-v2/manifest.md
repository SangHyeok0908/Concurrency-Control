# Benchmark Campaign 2026-09-24-williams-v2

## Campaign

- campaign_id: 2026-09-24-williams-v2
- rounds_per_phase: 10
- schedule_version: williams-10-v1
- predecessor_methodology: methodology-v1-fixed-order
- predecessor_raw_runs: [docs/benchmark/raw-runs.csv](../../raw-runs.csv)
- predecessor_execution_record: [docs/benchmark/2026-09-24-controlled-run.md](../../2026-09-24-controlled-run.md)
- created_at: 2026-09-25T00:58:08
- gatling_version: 3.13.5

## Phase A

- phase_a_invocation: scripts/benchmark.sh --campaign-id 2026-09-24-williams-v2 --phase a --rounds 10
- phase_a_status: failed
- phase_a_app_start_id: 2026-09-24-williams-v2-phase-a
- phase_a_environment_path: docs/benchmark/campaigns/2026-09-24-williams-v2/environment-phase-a.json
- phase_a_environment_sha256: 4472ebb50581d14bf201f2ba73c5b520dcbf5a171169a1e2a2c6ec8d92f2c863
- phase_a_expected_max_attempts: 5
- phase_a_git_head: ef3a660727f504629c3b7a1bc7f1b0106d9e3250
- phase_a_os: Darwin seosanghyeog-ui-MacBookAir.local 25.6.0 Darwin Kernel Version 25.6.0: Fri Jul 31 19:18:29 PDT 2026; root:xnu-12377.161.14~5/RELEASE_ARM64_T8122 arm64
- phase_a_java: openjdk version "21.0.9" 2025-10-21 LTS
- phase_a_gradle: Gradle 8.14.5
- phase_a_mysql: 8.0.46
- phase_a_max_connections: 151
- phase_a_initial_slot_count: 244
- phase_a_initial_reservation_count: 8557
- phase_a_started_at: 2026-09-25T00:58:08
- phase_a_completed_at: 2026-09-25T00:58:21
- phase_a_warmup_status: failed
- phase_a_measured_status: not_started
- phase_a_measured_rows: 0
- phase_a_measurement_started_at: pending
- phase_a_measurement_completed_at: pending
- phase_a_failure_reason: schedule row 1 did not contain ten treatments
