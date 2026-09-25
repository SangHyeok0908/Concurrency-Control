# Duplicate Request Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the vacuous “duplicates=0 in 60 distinct-applicant runs” evidence with a real, reproducible 25-run same-applicant/same-slot HTTP experiment.

**Architecture:** Keep the immutable capacity-contention CSV and simulation intact. Add a dedicated Gatling simulation, a dedicated orchestrating shell script, and a Python result contract that parses the simulation sentinel, validates HTTP/DB invariants, writes a separate CSV, and validates/summarizes the complete campaign.

**Tech Stack:** Java 21, Gatling Java DSL 3.13.5, Bash, Python 3 standard library, Spring Boot 3.5, MySQL 8, Gradle 8.14.5.

**Spec:** `docs/superpowers/specs/2026-09-25-duplicate-request-benchmark-design.md`

## Global Constraints

- Do not modify reservation strategy behavior, especially the intentionally unlocked baseline.
- Do not add an idempotency key, Redis `SETNX`, or Redisson.
- Do not modify `docs/benchmark/raw-runs.csv` or archived raw CSV files.
- Do not hide 500/503 responses from strategies that do not translate UNIQUE violations.
- Do not use duplicate-workload timings to rank strategies.
- Do not execute `git commit`; AGENTS.md requires explicit user approval. Record suggested commit boundaries only.
- Preserve untracked `.codex/` and `AGENTS.md` files.

## Review Focus

- A run with zero reservations and `duplicate_rows=0` must fail because `confirmed`, pair count, and seats consumed are not one.
- A status histogram that drops a timeout or unknown status must fail instead of silently summing to fewer than 200.
- `unique` must fail the campaign if any duplicate response is 500/503 instead of 409.
- A 25-row file with every strategy five times but fixed at one position must fail schedule validation.
- Adding a second Gatling simulation must not make the existing capacity runner select a simulation interactively.

---

### Task 1: Duplicate result contract

**Files:**
- Create: `scripts/duplicate_benchmark.py`
- Create: `scripts/tests/test_duplicate_benchmark.py`

**Interfaces:**
- Consumes: Gatling stdout containing one `DUPLICATE_HTTP_STATUS_COUNTS=<json>` line, one independent `DUPLICATE_GATLING_COUNTS=<json>` line, and scalar DB observations supplied as CLI flags.
- Produces: `parse_status_counts(text) -> dict[str, int]`, `parse_gatling_counts(text) -> dict[str, int]`, `validate_row(row) -> list[str]`, `validate_campaign(rows, rounds) -> list[str]`, and `record`/`summarize` CLI commands.

- [ ] **Step 1: Write failing unit tests for the status and row contract**

  Tests use literal histograms and assert that a valid 200-request row passes, while missing statuses, zero reservations, wrong remaining seats, duplicate rows, and a non-409 `unique` result each fail with a field-specific message.

- [ ] **Step 2: Run the focused test and verify RED**

  Run: `python3 -m unittest scripts.tests.test_duplicate_benchmark -v`

  Expected: import failure because `scripts/duplicate_benchmark.py` does not exist.

- [ ] **Step 3: Implement status parsing and row validation**

  Each parser must require exactly one corresponding sentinel and exactly these non-negative integer keys:

  ```python
  STATUS_KEYS = ("201", "409", "500", "503", "other", "no_response")
  SENTINEL = "DUPLICATE_HTTP_STATUS_COUNTS="
  GATLING_KEYS = ("ok", "ko")
  GATLING_SENTINEL = "DUPLICATE_GATLING_COUNTS="
  ```

  `validate_row` independently checks all equations in design section 4 and adds the strategy-specific `unique` checks.

- [ ] **Step 4: Add failing tests for CSV recording and campaign completeness**

  Tests invoke the CLI against temporary files. They require one header, atomic append, a recorded `invariant_pass=false` row on logical failure, five cyclic positions per strategy, exactly 25 successful rows for five rounds, and rejection of duplicates/missing rows.

- [ ] **Step 5: Run the expanded test and verify RED**

  Run: `python3 -m unittest scripts.tests.test_duplicate_benchmark -v`

  Expected: failures for the missing `record` and `summarize` behavior.

- [ ] **Step 6: Implement CSV recording and Markdown summary**

  `record` receives named scalar flags plus `--gatling-log` and writes the exact schema from the spec with `csv.DictWriter`. A logically invalid complete observation is written with `invariant_pass=false` and returns non-zero. `summarize` validates the full cyclic campaign before printing one row per strategy containing status totals and DB invariant results.

- [ ] **Step 7: Verify Task 1 GREEN**

  Run: `python3 -m unittest scripts.tests.test_duplicate_benchmark -v`

  Expected: all Task 1 tests pass.

- [ ] **Step 8: Record suggested commit boundary without committing**

  Suggested message: `test: define duplicate benchmark result contract`

### Task 2: Dedicated Gatling workload and safe runner selection

**Files:**
- Create: `src/gatling/java/com/interview/reservation/loadtest/DuplicateReservationSimulation.java`
- Modify: `scripts/benchmark.sh`

**Interfaces:**
- Consumes: `-DbaseUrl`, `-Dstrategy`, `-Dcapacity`, and `-Drequests`.
- Produces: request label `duplicate reserve [<strategy> cap=<capacity> req=<requests>]`, one HTTP status sentinel, and one independent Gatling OK/KO sentinel after the run.

- [ ] **Step 1: Verify the feature is absent (RED acceptance)**

  Run: `./gradlew gatlingRun --simulation com.interview.reservation.loadtest.DuplicateReservationSimulation -Dstrategy=unique -Dcapacity=200 -Drequests=200`

  Expected: non-zero because the simulation class does not exist. An application connection failure is not an acceptable RED; the class-selection failure must be observed.

- [ ] **Step 2: Implement the minimal dedicated simulation**

  Seed one slot and one applicant, store their IDs in process-local atomics, and send the same body from all virtual users. Save the response status before checking `status().in(201, 409)`, count missing responses separately, independently count the resulting Gatling session OK/KO state, and override `after()` to print both sentinels. Add a detail-level assertion that exactly `requests` reservation actions ran.

- [ ] **Step 3: Make the existing runner select its historical simulation explicitly**

  Change its Gradle invocation to:

  ```bash
  ./gradlew gatlingRun --console=plain -q \
    --simulation com.interview.reservation.loadtest.BaselineReservationSimulation \
    -DbaseUrl="$BASE_URL" -Dstrategy="$strategy" \
    -Dcapacity="$capacity" -Dcontenders="$contenders"
  ```

- [ ] **Step 4: Compile the Gatling source set**

  Run: `./gradlew gatlingClasses`

  Expected: build successful.

- [ ] **Step 5: Verify the existing script remains syntactically valid**

  Run: `bash -n scripts/benchmark.sh`

  Expected: exit 0.

- [ ] **Step 6: Record suggested commit boundary without committing**

  Suggested message: `feat: add same-request Gatling workload`

### Task 3: Duplicate benchmark orchestrator

**Files:**
- Create: `scripts/benchmark-duplicates.sh`
- Create: `scripts/tests/test_benchmark_duplicates_runner.py`

**Interfaces:**
- Consumes: a running benchmark-profile app, MySQL Docker container, `duplicate_benchmark.py record`, and the duplicate HTTP/Gatling sentinels.
- Produces: `docs/benchmark/duplicate-runs.csv`; `--print-plan` emits the 25-row cyclic plan without touching HTTP, DB, or files.

- [ ] **Step 1: Write the failing runner plan test**

  The test invokes `bash scripts/benchmark-duplicates.sh --print-plan`, checks 25 data lines, and verifies every strategy occupies each position exactly once.

- [ ] **Step 2: Run the focused test and verify RED**

  Run: `python3 -m unittest scripts.tests.test_benchmark_duplicates_runner -v`

  Expected: non-zero because the runner does not exist.

- [ ] **Step 3: Implement argument validation and dry plan output**

  Defaults are capacity 200, requests 200, rounds 5, all five strategies, and `docs/benchmark/duplicate-runs.csv`. Reject non-positive values, `capacity < requests`, unknown strategies, an existing output file, and public full runs with a non-five-round schedule. `--rounds 1 --strategies unique` remains available for a smoke run to a temporary output path.

- [ ] **Step 4: Verify plan test GREEN**

  Run: `python3 -m unittest scripts.tests.test_benchmark_duplicates_runner -v`

  Expected: all runner plan tests pass.

- [ ] **Step 5: Implement live orchestration**

  Reuse `/api/metrics/benchmark-environment` plus `validate_benchmark_environment.py`, capture before/after slot and applicant IDs, run the simulation by FQN, query `confirmed`, `remaining`, pair count and duplicate rows, and pass the complete observation to `duplicate_benchmark.py record`. Stop immediately after any non-zero stage.

- [ ] **Step 6: Check shell syntax and all script tests**

  Run: `bash -n scripts/benchmark-duplicates.sh && ./gradlew benchmarkScriptTest`

  Expected: build successful and all Python tests pass.

- [ ] **Step 7: Record suggested commit boundary without committing**

  Suggested message: `feat: automate duplicate request verification campaign`

### Task 4: Remove the misleading capacity-workload claim

**Files:**
- Modify: `scripts/summarize_benchmark.py`
- Modify: `scripts/tests/test_summarize_benchmark.py`
- Modify: `README.md`
- Modify: `PROJECT_PLAN.md`
- Modify: `docs/STEP2-DEFENSE-BENCHMARK.md`
- Modify: `docs/STEP2-3-BRANCH-STRATEGY.md`
- Modify: `docs/benchmark/2026-09-24-controlled-run.md`

**Interfaces:**
- Consumes: the immutable 60-row capacity CSV and the new duplicate campaign summary.
- Produces: documentation where each conclusion links to the workload that actually exercises it.

- [ ] **Step 1: Write a failing summarizer regression test**

  Feed a valid legacy row with `duplicates=0` and assert that the generated capacity table has no `중복` column and explicitly labels the workload as distinct-applicant capacity contention.

- [ ] **Step 2: Run the test and verify RED**

  Run: `python3 -m unittest scripts.tests.test_summarize_benchmark -v`

  Expected: failure because the current output still renders `| 중복 |`.

- [ ] **Step 3: Remove duplicate evidence from the capacity summary**

  Keep reading the historical CSV field for compatibility, but do not aggregate or print it. Add one sentence directing identical-request evidence to `duplicate-runs.csv`.

- [ ] **Step 4: Verify summarizer test GREEN**

  Run: `python3 -m unittest scripts.tests.test_summarize_benchmark -v`

  Expected: all summarizer tests pass.

- [ ] **Step 5: Correct prose and tables**

  Rename the 60 runs as distinct-applicant capacity contention, remove duplicate columns from its tables, correct the Phase 1 scope statement, and add a separate duplicate-request section. Preserve historical hashes and explicitly state that the raw `duplicates` field was observational, not exercised evidence.

- [ ] **Step 6: Record suggested commit boundary without committing**

  Suggested message: `docs: separate capacity and duplicate request evidence`

### Task 5: Produce and verify real evidence

**Files:**
- Create: `docs/benchmark/duplicate-runs.csv`
- Create: `docs/benchmark/2026-09-25-duplicate-request-run.md`
- Modify: documentation files from Task 4 with measured totals.

**Interfaces:**
- Consumes: Docker MySQL, benchmark-profile Spring app, `scripts/benchmark-duplicates.sh`.
- Produces: 25 verified raw rows, a reproducibility manifest, and measured documentation tables.

- [ ] **Step 1: Start infrastructure and the controlled app**

  Run: `docker compose up -d`, then start `./gradlew bootRun --args='--spring.profiles.active=benchmark'` in a managed terminal session.

- [ ] **Step 2: Run one unique smoke experiment**

  Run with a temporary output path: `scripts/benchmark-duplicates.sh --rounds 1 --strategies unique --out <temporary.csv>`.

  Expected: one row, `http_201=1`, `http_409=199`, `confirmed=1`, `pair_reservations=1`, `remaining=199`, `invariant_pass=true`.

- [ ] **Step 3: Run the full 25-run campaign**

  Run: `scripts/benchmark-duplicates.sh`.

  Expected: 25 successful rows and a zero exit status.

- [ ] **Step 4: Validate and summarize the campaign**

  Run: `python3 scripts/duplicate_benchmark.py summarize docs/benchmark/duplicate-runs.csv --rounds 5`.

  Expected: campaign validation succeeds; output contains all five strategies and no performance ranking.

- [ ] **Step 5: Record the environment and immutable hashes**

  Create the run manifest with timestamp, Git HEAD plus uncommitted implementation note, OS/Java/Gradle/Gatling/MySQL versions, exact commands, row count, environment controls, CSV SHA-256, and the unchanged SHA-256 of `raw-runs.csv`.

- [ ] **Step 6: Run the full verification suite**

  Run: `./gradlew check`

  Expected: all Java and script tests pass.

- [ ] **Step 7: Audit the diff and historical artifacts**

  Run: `git diff --check`, `git status --short`, and SHA-256 comparison against `docs/benchmark/2026-09-24-controlled-run.md`.

  Expected: no whitespace errors, only intended files changed, historical raw CSV hash unchanged.

- [ ] **Step 8: Record suggested commit boundary without committing**

  Suggested message: `docs: publish duplicate request benchmark evidence`
