# 벤치마크 도구 단순화 구현 계획

> 상태: 구현 완료. 당시 설계·계획 기록이며, 현재 실행 결과의 정본은 [방어 전략 벤치마크](../../STEP2-DEFENSE-BENCHMARK.md)다.

> **에이전트 작업자용:** REQUIRED SUB-SKILL: 이 계획을 작업별로 구현할 때 superpowers:subagent-driven-development(권장) 또는 superpowers:executing-plans를 사용한다. 진행 상태는 체크박스(`- [ ]`)로 추적한다.

**Goal:** 방법론 v2의 균형 순서·정확한 TPS·완전성 검증은 유지하면서 정원 경쟁 벤치마크 도구를 한 실행기와 한 요약기로 줄인다.

**Architecture:** `benchmark_capacity.py`가 Williams 계획 생성, 환경 확인, Gatling·DB 실행, CSV 기록과 핵심 완전성 검증을 한곳에서 담당한다. `BaselineReservationSimulation`은 바이너리 로그 대신 종료 sentinel로 버스트 시작·종료를 공개하고, `summarize_benchmark.py`는 기존 39열 v2 CSV를 검증한 뒤 표와 차트를 만든다. 파일 게시 경쟁과 저수준 I/O 장애 방어, 실패 행 상태 머신, v1 렌더러는 제거한다.

**Tech Stack:** Python 3.9 표준 라이브러리, Bash가 아닌 `subprocess`, Java 21, Gatling Java DSL 3.13.5, Gradle, MySQL Docker CLI, Python `unittest`

**Spec:** `docs/internal/archive/2026-09-25-benchmark-tooling-simplification-design.md`

## Global Constraints

- 대상 작업 트리는 `/private/tmp/concurrency-control-benchmark-methodology-v2`이고 브랜치는 `step2/benchmark-methodology-v2`다.
- 로컬 `master`의 동일 요청 벤치마크 커밋은 이 브랜치로 복사·병합·재작성하지 않는다.
- 기존 `docs/benchmark/raw-runs-v2.csv`, 성공 캠페인 CSV, 환경 JSON, 결과 문서의 측정 수치는 수정하지 않는다.
- 5개 전략 × 2개 경합을 한 처리 집합으로 보고, 라운드 수는 양의 10의 배수만 허용한다.
- Phase A는 `optimisticMaxAttempts=5`, Phase B는 `optimisticMaxAttempts=20`이며 두 Phase 모두 10개 처리를 전부 측정한다.
- benchmark 앱과 Docker는 실행기가 자동으로 시작·종료하지 않는다.
- 새 외부 Python 패키지를 추가하지 않는다.
- 실패한 실행은 비정상 종료하고 부분 CSV를 남기되, 실패 행을 CSV에 만들지 않는다.
- 기존 파일을 덮어쓰지 않는다. Phase A는 새 CSV만 만들고 Phase B만 검증된 Phase A 뒤에 추가한다.
- 커밋은 자동 생성하지 않는다. 각 커밋 단계에서는 메시지 초안만 제시하고 사용자 승인을 기다린다.

## Review Focus

- Gatling 요청이 일부 완료되지 않은 경우 sentinel의 완료 수가 `contenders`와 다르면 성공 행을 쓰지 않고 실패해야 한다.
- Phase B에 Phase A와 다른 `campaign_id`, 라운드 수 또는 불완전한 처리 조합이 들어오면 앱에 접근하기 전에 거부해야 한다.
- `stats.js`의 요청 수·OK·KO와 sentinel의 요청 수가 다르면 TPS를 만들지 않아야 한다.
- 기존 39열 성공 CSV를 새 요약기가 읽을 때 수치가 부동소수점 변환으로 달라지거나 캠페인이 거부되면 안 된다.
- 측정 중 실패해 부분 CSV가 남아도 요약기는 완전한 Phase A+B로 오인하지 않아야 한다.

---

### Task 1: 단순 실행기의 순수 계획·파싱·검증 코어

**Files:**
- 생성: `scripts/benchmark_capacity.py`
- 생성: `scripts/tests/test_benchmark_capacity.py`

**Interfaces:**
- Produces: `Treatment`, `PlanEntry`, `BurstMetrics`, `ReportMetrics`, `RetryMetrics` dataclass
- Produces: `build_plan(rounds: int) -> list[PlanEntry]`
- Produces: `parse_burst_sentinel(text: str, expected_requests: int) -> BurstMetrics`
- Produces: `load_report_stats(report_dir: Path, entry: PlanEntry, gatling_output: str) -> ReportMetrics`
- Produces: `parse_retry_snapshot(payload: object) -> RetryMetrics`
- Produces: `validate_environment(payload: object, expected_max_attempts: int) -> dict`
- Produces: `EnvironmentNotReady`, Hikari `totalConnections`만 아직 100이 아닐 때 사용하는 예외
- Produces: `validate_rows(rows: list[dict[str, str]], requirement: str, expected_rounds: int) -> None`
- Produces: `CSV_FIELDS: list[str]`, 기존 방법론 v2의 정확한 39열 순서

- [ ] **Step 1: Williams 순서와 파서 계약의 실패 테스트 작성**

`scripts/tests/test_benchmark_capacity.py`에 저장소 루트의 `scripts`를 `sys.path`에 추가하고 다음 계약을 작성한다.

```python
class PlanTest(unittest.TestCase):
    def test_ten_rounds_balance_every_treatment_at_every_position(self):
        plan = benchmark.build_plan(10)
        self.assertEqual(100, len(plan))
        positions = Counter((entry.treatment.id, entry.position) for entry in plan)
        self.assertEqual({1}, set(positions.values()))

    def test_rounds_must_be_a_positive_multiple_of_ten(self):
        for rounds in (0, -10, 1, 5, 11):
            with self.subTest(rounds=rounds):
                with self.assertRaisesRegex(ValueError, "positive multiple of 10"):
                    benchmark.build_plan(rounds)

class BurstParserTest(unittest.TestCase):
    def test_reads_exactly_one_complete_sentinel(self):
        metrics = benchmark.parse_burst_sentinel(
            'noise\nBENCHMARK_BURST_METRICS={"requests":200,'
            '"startEpochMs":1000,"endEpochMs":1250,"wallMs":250}\n',
            expected_requests=200,
        )
        self.assertEqual(250, metrics.wall_ms)
        self.assertEqual(800.0, metrics.tps)

    def test_rejects_missing_duplicate_or_inconsistent_sentinel(self):
        invalid = (
            "",
            'BENCHMARK_BURST_METRICS={"requests":199,"startEpochMs":1000,'
            '"endEpochMs":1250,"wallMs":250}',
            'BENCHMARK_BURST_METRICS={"requests":200,"startEpochMs":1000,'
            '"endEpochMs":1250,"wallMs":200}',
        )
        for text in invalid:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    benchmark.parse_burst_sentinel(text, 200)
```

- [ ] **Step 2: 새 테스트가 모듈 부재로 실패하는지 확인**

실행: `python3 -m unittest scripts.tests.test_benchmark_capacity -v`

예상: `benchmark_capacity`를 import할 수 없어 FAIL 또는 ERROR

- [ ] **Step 3: 계획과 metric dataclass, 기존 CSV 필드 상수 구현**

`scripts/benchmark_capacity.py`에 다음 공개 구조를 구현한다.

```python
SCHEDULE_VERSION = "williams-10-v1"
WILLIAMS_BASE = (0, 1, 9, 2, 8, 3, 7, 4, 6, 5)

@dataclass(frozen=True)
class Treatment:
    id: int
    strategy: str
    contention: str
    capacity: int
    contenders: int

@dataclass(frozen=True)
class PlanEntry:
    round: int
    position: int
    treatment: Treatment

@dataclass(frozen=True)
class BurstMetrics:
    requests: int
    start_epoch_ms: int
    end_epoch_ms: int
    wall_ms: int
    tps: float

@dataclass(frozen=True)
class ReportMetrics:
    requests: int
    ok: int
    ko: int
    mean_ms: int
    p95_ms: int
    max_ms: int
    burst: BurstMetrics

@dataclass(frozen=True)
class RetryMetrics:
    succeeded: int
    exhausted: int
    version_conflicts: int
    deadlocks: int
    mean_attempts: float

class EnvironmentNotReady(ValueError):
    pass
```

`build_plan`은 기존 `WILLIAMS_BASE`와 10개 처리 매핑을 그대로 사용한다. `parse_burst_sentinel`은 접두사로 시작하는 행이 정확히 하나인지, 키 집합이 정확한지, boolean이 아닌 음이 아닌 정수인지, `wallMs == endEpochMs - startEpochMs > 0`인지, 요청 수가 기대값과 같은지 검사한다.

- [ ] **Step 4: 환경·재시도·리포트 파서 실패 테스트 작성**

다음 경우를 같은 테스트 파일에 추가한다.

```python
def test_environment_requires_the_phase_retry_cap(self):
    payload = valid_environment(max_attempts=5)
    benchmark.validate_environment(payload, 5)
    with self.assertRaisesRegex(ValueError, "optimisticMaxAttempts"):
        benchmark.validate_environment(payload, 20)

def test_retry_distribution_must_match_success_count_and_mean(self):
    parsed = benchmark.parse_retry_snapshot({
        "succeededByAttempts": {"1": 1, "2": 1},
        "succeeded": 2,
        "retryExhausted": 3,
        "versionConflicts": 4,
        "deadlocks": 0,
        "meanAttemptsPerSuccess": 1.5,
    })
    self.assertEqual(2, parsed.succeeded)

def test_report_counts_must_match_the_burst_sentinel(self):
    report = write_stats_fixture(requests=200, ok=198, ko=2)
    output = burst_sentinel(requests=199, start=1000, end=1250)
    with self.assertRaisesRegex(ValueError, "request count"):
        benchmark.load_report_stats(report, plan_entry(), output)
```

- [ ] **Step 5: 환경·재시도·리포트 파서 최소 구현**

`validate_environment`은 현재 benchmark 환경의 다음 값만 검사하고 원본 dict를 반환한다.

```python
EXPECTED_ENVIRONMENT = {
    "activeProfiles": ["benchmark"],
    "maximumPoolSize": 100,
    "minimumIdle": 100,
    "totalConnections": 100,
    "showSql": False,
    "formatSql": False,
    "useSqlComments": False,
    "sqlLogLevel": "OFF",
    "bindLogLevel": "OFF",
    "rootLogLevel": "OFF",
    "backoffBaseMillis": 10,
    "backoffMaxMillis": 200,
    "backoffPolicy": "exponential-jitter",
}
```

`totalConnections`만 100보다 작으면 `EnvironmentNotReady`를 발생시키고, 나머지 설정 불일치는
`ValueError`로 즉시 실패한다. `parse_retry_snapshot`은 기존 재시도 분포 합계와 가중 평균 검사를 한
함수로 옮긴다. `load_report_stats`는 `stats.js`의 정확한 요청 이름을 읽고 평균·p95·최대·OK·KO를
구한 뒤 `parse_burst_sentinel` 결과와 요청 수를 대조한다.

- [ ] **Step 6: 캠페인 핵심 식과 완전성 실패 테스트 작성**

테스트 helper `complete_rows(rounds=10, phases=("a", "b"))`로 정상 행을 만든 뒤 다음 mutation을 각각 거부하는지 검사한다.

```python
mutations = {
    "missing row": lambda rows: rows.pop(),
    "duplicate slot": lambda rows: rows[1].update(slot_id=rows[0]["slot_id"]),
    "request equation": lambda rows: rows[0].update(ok="0", ko="0"),
    "overbooking equation": lambda rows: rows[0].update(overbooking="9"),
    "tps equation": lambda rows: rows[0].update(tps="999.9"),
    "failed row": lambda rows: rows[0].update(run_status="parse_failed"),
    "mixed environment": lambda rows: rows[1].update(environment_sha256="b" * 64),
}
```

Phase A 검증은 A만 정확히 100행, complete 검증은 A 100행 다음 B 100행을 요구한다. 각 Phase의 상한은 A=5, B=20이다.

- [ ] **Step 7: `validate_rows` 최소 구현 후 Task 1 테스트 통과 확인**

검증 순서는 다음으로 제한한다.

1. 정확한 39열 키와 하나의 `campaign_id`
2. 모든 행 `run_status=ok`, 빈 `error_reason`
3. Phase별 상한·환경 해시 하나
4. 연속된 라운드, 라운드별 10개 처리와 10개 위치
5. 10라운드 블록별 처리×위치 한 번
6. 양수이고 고유한 `slot_id`
7. 요청·오버부킹·버스트·TPS 식

실행: `python3 -m unittest scripts.tests.test_benchmark_capacity -v`

예상: PASS

- [ ] **Step 8: 커밋 승인 요청 준비**

커밋 메시지 초안: `refactor: 벤치마크 핵심 검증을 단일 모듈로 통합`

실행하지 말 것. 사용자 승인 후에만 다음 명령을 사용한다.

```bash
git add scripts/benchmark_capacity.py scripts/tests/test_benchmark_capacity.py
git commit -m "refactor: 벤치마크 핵심 검증을 단일 모듈로 통합"
```

### Task 2: Gatling 버스트 sentinel

**Files:**
- 수정: `src/gatling/java/com/interview/reservation/loadtest/BaselineReservationSimulation.java`
- 수정: `scripts/tests/test_benchmark_capacity.py`

**Interfaces:**
- Consumes: `BENCHMARK_BURST_METRICS=` 형식을 해석하는 `parse_burst_sentinel`
- Produces: Gatling stdout의 정확히 한 개 `BENCHMARK_BURST_METRICS=<JSON>` 행

- [ ] **Step 1: sentinel과 동일한 실제 출력 계약 테스트 보강**

Python 테스트에서 JSON 키를 `requests`, `startEpochMs`, `endEpochMs`, `wallMs`로 고정하고, 추가 키·누락 키·두 sentinel을 거부하도록 한다. 테스트를 실행해 아직 추가 계약이 실패하는지 확인한다.

실행: `python3 -m unittest scripts.tests.test_benchmark_capacity.BurstParserTest -v`

예상: 추가 키 또는 중복 sentinel 사례가 FAIL

- [ ] **Step 2: Simulation에 시작·종료 계측 구현**

`BaselineReservationSimulation`에 다음 상태를 추가한다.

```java
private static final AtomicLong BURST_START_EPOCH_MS = new AtomicLong(Long.MAX_VALUE);
private static final AtomicLong BURST_END_EPOCH_MS = new AtomicLong(Long.MIN_VALUE);
private static final AtomicInteger BURST_COMPLETED = new AtomicInteger();
```

예약 HTTP action 바로 앞에서 현재 시각의 최솟값을 기록하고 바로 뒤에서 최댓값과 완료 수를 기록한다. `before()`는 세 값을 초기화한다. `after()`는 완료 수·시작·종료·차이를 JSON 한 줄로 출력한다.

```java
System.out.printf(
        "BENCHMARK_BURST_METRICS={\"requests\":%d,\"startEpochMs\":%d,"
                + "\"endEpochMs\":%d,\"wallMs\":%d}%n",
        completed, start, end, end - start);
```

- [ ] **Step 3: Python parser를 정확한 키 집합에 맞추고 컴파일 확인**

실행:

```bash
python3 -m unittest scripts.tests.test_benchmark_capacity.BurstParserTest -v
./gradlew gatlingClasses --console=plain
```

예상: 모두 PASS 또는 `BUILD SUCCESSFUL`

- [ ] **Step 4: 커밋 승인 요청 준비**

커밋 메시지 초안: `refactor: Gatling 버스트 시간을 sentinel로 노출`

사용자 승인 전에는 커밋하지 않는다.

### Task 3: Phase 실행 오케스트레이션과 CLI

**Files:**
- 수정: `scripts/benchmark_capacity.py`
- 수정: `scripts/tests/test_benchmark_capacity.py`

**Interfaces:**
- Consumes: Task 1의 계획·파서·검증 함수와 Task 2의 Gatling sentinel
- Produces: `environment_path(output: Path, phase: str) -> Path`
- Produces: `read_rows(path: Path) -> list[dict[str, str]]`
- Produces: `run_phase(args: argparse.Namespace) -> None`
- Produces: CLI `--campaign-id`, `--phase`, `--rounds`, `--out`, `--print-plan`

- [ ] **Step 1: 파일 경계와 plan-only CLI 실패 테스트 작성**

다음 계약을 추가한다.

```python
def test_print_plan_has_header_and_one_hundred_rows_without_external_access(self):
    result = run_cli("--campaign-id", "plan", "--phase", "a",
                     "--rounds", "10", "--print-plan")
    self.assertEqual(0, result.returncode, result.stderr)
    self.assertEqual(101, len(result.stdout.splitlines()))

def test_phase_a_refuses_existing_output(self):
    output.write_text("existing")
    with self.assertRaisesRegex(ValueError, "already exists"):
        benchmark.prepare_output(output, "a", "campaign", 10)

def test_phase_b_rejects_incomplete_phase_a(self):
    write_rows(output, complete_rows(phases=("a",))[:-1])
    with self.assertRaisesRegex(ValueError, "complete Phase A"):
        benchmark.prepare_output(output, "b", "campaign", 10)

def test_phase_b_rejects_a_different_campaign_or_round_count(self):
    write_rows(output, complete_rows(phases=("a",), campaign_id="original"))
    with self.assertRaisesRegex(ValueError, "campaign"):
        benchmark.prepare_output(output, "b", "different", 10)
    with self.assertRaisesRegex(ValueError, "round"):
        benchmark.prepare_output(output, "b", "original", 20)
```

- [ ] **Step 2: 인자·출력 경계와 `--print-plan` 구현**

`--campaign-id`, `--phase`, `--out`은 필수다. `--rounds` 기본은 10이고 양의 10의 배수만 허용한다. `--print-plan`은 `--out`을 요구하지 않도록 parser에서 별도 경로로 처리하거나, 출력 인자를 선택값으로 받은 뒤 실행 모드에서만 요구한다. plan 출력은 다음 TSV 헤더를 사용한다.

```text
round\tposition\ttreatment_id\tstrategy\tcontention\tcapacity\tcontenders
```

- [ ] **Step 3: 외부 경계 helper 구현**

다음 함수는 성공 결과만 반환하고 실패 시 한 줄 `RuntimeError`를 발생시킨다.

```python
def fetch_json(url: str, method: str = "GET") -> object:
    request = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))

def run_command(arguments: list[str], cwd: Path) -> str:
    result = subprocess.run(arguments, cwd=str(cwd), text=True,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, check=False)
    if result.returncode != 0:
        raise RuntimeError("command failed: " + " ".join(arguments))
    return result.stdout

def mysql_query(container: str, sql: str) -> str:
    return run_command(
        ["docker", "exec", container, "mysql", "-uroot", "-N", "-B",
         "-e", sql, "reservation"],
        PROJECT_ROOT,
    ).strip()

def snapshot_report_names(root: Path) -> set[str]:
    return {path.name for path in root.iterdir() if path.is_dir()}

def select_new_report(root: Path, before: set[str]) -> Path:
    created = [path for path in root.iterdir()
               if path.is_dir() and path.name not in before]
    if len(created) != 1 or not (created[0] / "js" / "stats.js").is_file():
        raise RuntimeError("expected exactly one complete Gatling report")
    return created[0]
```

`urllib.request`와 `subprocess.run(check=False, text=True)`만 사용한다. 실제 오류에는 명령의 마지막
출력 행을 붙이되 줄바꿈은 공백으로 정규화한다.

- [ ] **Step 4: 환경 스냅샷과 워밍업 구현**

환경 파일은 `Path(str(out) + ".phase-%s-environment.json" % phase)`로 계산한다. 환경 API를 최대 `ENVIRONMENT_WAIT_SECONDS` 동안 확인하되, 풀만 준비되지 않은 경우에만 1초 간격으로 다시 읽는다. 검증된 JSON은 `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"`로 `open("x")`에 한 번 저장하고 SHA-256을 계산한다.

워밍업은 plan 첫 라운드의 10개 entry를 실행한다. 각 실행은 새 슬롯과 새 Gatling 리포트가 정확히 하나 생긴 것만 확인하며 CSV에 쓰지 않는다.

- [ ] **Step 5: 한 측정과 CSV 기록 구현**

측정 순서는 다음으로 고정한다.

1. 재시도 metric DELETE
2. 실행 전 최대 슬롯 ID
3. 기존 Gatling 리포트 이름 snapshot
4. `./gradlew gatlingRun` 실행과 stdout/stderr 파일 수집
5. 실행 후 새 슬롯과 새 리포트 하나 확인
6. `load_report_stats`
7. 한 SQL tuple로 remaining·confirmed·duplicates 조회
8. 재시도 metric GET과 `parse_retry_snapshot`
9. 기존 39열 dict 생성, `run_status=ok`, `error_reason=""`
10. `csv.DictWriter.writerow`

Phase A 첫 행 전 `open("x")`로 header를 만들고, Phase B는 `open("a")`로 추가한다. 각 행 뒤 `flush()`까지만 수행하고 별도 임시 파일 교체·`fsync`·정본 승격은 하지 않는다.

- [ ] **Step 6: 전체 Phase 완료 검증과 오류 출력 구현**

모든 plan entry가 끝나면 CSV를 다시 읽고 Phase A는
`validate_rows(read_rows(output), "phase-a", rounds)`, Phase B는
`validate_rows(read_rows(output), "complete", rounds)`로 검사한다. `main()`은 `ValueError`,
`RuntimeError`, `OSError`, `csv.Error`, `json.JSONDecodeError`를 잡아 줄바꿈을 공백으로 바꾼 한 줄
오류를 stderr에 출력하고 1을 반환한다.

- [ ] **Step 7: Task 3 테스트와 정적 문법 검사**

실행:

```bash
python3 -m unittest scripts.tests.test_benchmark_capacity -v
python3 -m py_compile scripts/benchmark_capacity.py
```

예상: PASS

- [ ] **Step 8: 커밋 승인 요청 준비**

커밋 메시지 초안: `refactor: 정원 경쟁 벤치마크 실행기를 단일화`

사용자 승인 전에는 커밋하지 않는다.

### Task 4: 검증된 v2 전용 요약기 단순화

**Files:**
- 수정: `scripts/summarize_benchmark.py`
- 대체: `scripts/tests/test_summarize_benchmark.py`

**Interfaces:**
- Consumes: `benchmark_capacity.CSV_FIELDS`, `read_rows`, `validate_rows`
- Produces: `render(rows: list[dict[str, str]]) -> str`
- Produces: CLI `python3 scripts/summarize_benchmark.py CSV_PATH`

- [ ] **Step 1: 기존 200행 정본과 핵심 출력 테스트 작성**

기존 복합 테스트를 다음 사용자 관점 계약으로 대체한다.

```python
def test_checked_in_campaign_is_complete_and_renders_phase_tables(self):
    result = run_summary(ROOT / "docs/benchmark/raw-runs-v2.csv")
    self.assertEqual(0, result.returncode, result.stderr)
    self.assertIn("Phase A", result.stdout)
    self.assertIn("Phase B", result.stdout)
    self.assertIn("② vs ④", result.stdout)
    self.assertIn("⑤ 낙관적 락", result.stdout)
    self.assertIn("| n |", result.stdout)

def test_incomplete_campaign_is_rejected(self):
    rows = benchmark.read_rows(ROOT / "docs/benchmark/raw-runs-v2.csv")[:-1]
    result = summarize_temp(rows)
    self.assertNotEqual(0, result.returncode)
    self.assertNotIn("Phase A", result.stdout)
```

추가로 한 셀의 두 값이 같은 경우 min–max가 같은 수로 표시되고, 조건부·비관적 위치와 차이가 표에 포함되는지 검사한다.

- [ ] **Step 2: 기존 테스트가 새 간소화 계약 전에는 실패하는지 확인**

실행: `python3 -m unittest scripts.tests.test_summarize_benchmark -v`

예상: 기존 `--legacy-v1` 인터페이스 또는 출력 차이 때문에 최소 한 테스트 FAIL

- [ ] **Step 3: v2 전용 요약기 구현**

기존 `render_legacy`, 다중 캠페인 선택, 정본 승격 표현을 제거한다. `main`은 정확한 하나의 CSV를 읽고 `validate_rows(rows, "complete", inferred_rounds)`를 호출한다. 라운드 수는 Phase A의 `measured_round` 최댓값으로 구하되 양의 10의 배수인지 검증기가 확인한다.

표는 현재 v2 문서와 같은 의미를 유지한다.

- Phase별·경합별 5전략 표
- confirmed 중앙값
- overbooking·duplicates 최댓값
- KO, mean, p95, TPS의 중앙값과 min–max
- 같은 Phase·라운드·경합의 조건부/비관적 위치와 평균 차이
- 낙관적 retry 지표
- Phase·경합별 Mermaid 평균 응답 중앙값 차트

실제 값 범위가 작으므로 `statistics.median`을 사용하고 `.0`을 제거하는 `format_number` 하나만 둔다.

- [ ] **Step 4: 요약기 테스트와 기존 정본 실행 확인**

실행:

```bash
python3 -m unittest scripts.tests.test_summarize_benchmark -v
python3 scripts/summarize_benchmark.py docs/benchmark/raw-runs-v2.csv > /tmp/benchmark-summary.md
rg -n '^## Phase A|^## Phase B|^## ② vs ④|^## ⑤' /tmp/benchmark-summary.md
```

예상: 테스트 PASS, 네 섹션 모두 검색됨

- [ ] **Step 5: 커밋 승인 요청 준비**

커밋 메시지 초안: `refactor: 벤치마크 요약기를 v2 핵심 지표로 축소`

사용자 승인 전에는 커밋하지 않는다.

### Task 5: 과설계 모듈 제거와 활성 문서 갱신

**Files:**
- 삭제: `scripts/benchmark.sh`
- 삭제: `scripts/benchmark_schedule.py`
- 삭제: `scripts/benchmark_schema.py`
- 삭제: `scripts/gatling_binary_log.py`
- 삭제: `scripts/parse_gatling_report.py`
- 삭제: `scripts/parse_optimistic_metrics.py`
- 삭제: `scripts/promote_benchmark_campaign.py`
- 삭제: `scripts/validate_benchmark_campaign.py`
- 삭제: `scripts/validate_benchmark_environment.py`
- 삭제: `scripts/validate_phase_b_input.py`
- 삭제: `scripts/write_benchmark_row.py`
- 삭제: `scripts/tests/test_benchmark_schedule.py`
- 삭제: `scripts/tests/test_benchmark_script.py`
- 삭제: `scripts/tests/test_gatling_binary_log.py`
- 삭제: `scripts/tests/test_parse_gatling_report.py`
- 삭제: `scripts/tests/test_parse_optimistic_metrics.py`
- 삭제: `scripts/tests/test_promote_benchmark_campaign.py`
- 삭제: `scripts/tests/test_validate_benchmark_campaign.py`
- 삭제: `scripts/tests/test_validate_benchmark_environment.py`
- 삭제: `scripts/tests/test_validate_phase_b_input.py`
- 삭제: `scripts/tests/test_write_benchmark_row.py`
- 삭제: `scripts/tests/fixtures/gatling-3.13.5-baseline-cap1-cont200/js/stats.js`
- 삭제: `scripts/tests/fixtures/gatling-3.13.5-baseline-cap1-cont200/simulation.log`
- 삭제: `docs/superpowers/specs/2026-09-24-benchmark-order-and-throughput-design.md`
- 삭제: `docs/superpowers/plans/2026-09-24-benchmark-order-and-throughput.md`
- 수정: `docs/STEP2-DEFENSE-BENCHMARK.md`
- 수정: `docs/STEP2-3-BRANCH-STRATEGY.md`
- 수정: `README.md`
- 수정: `PROJECT_PLAN.md`

**Interfaces:**
- Consumes: 새 `benchmark_capacity.py`와 `summarize_benchmark.py` CLI
- Produces: 활성 문서에서 삭제된 스크립트를 실행하라고 안내하는 참조가 없음

- [ ] **Step 1: 삭제 전 활성 참조 목록 캡처**

실행:

```bash
rg -n 'benchmark\.sh|benchmark_schedule|benchmark_schema|gatling_binary_log|parse_gatling_report|parse_optimistic_metrics|promote_benchmark_campaign|validate_benchmark_campaign|validate_benchmark_environment|validate_phase_b_input|write_benchmark_row' README.md PROJECT_PLAN.md docs --glob '!docs/benchmark/campaigns/**' --glob '!docs/benchmark/2026-09-24-controlled-run.md'
```

예상: 활성 문서와 이전 spec·plan의 참조가 출력됨

- [ ] **Step 2: `apply_patch`로 제거 대상 소스·테스트·fixture 삭제**

`rm`을 사용하지 않고 각 추적 파일을 `apply_patch` 삭제로 제거한다. `scripts/__pycache__`는 추적 파일이 아니므로 변경 대상으로 삼지 않는다.

- [ ] **Step 3: 재현 명령과 도구 설명 갱신**

활성 문서의 실행 명령을 다음 형태로 통일한다.

```bash
python3 scripts/benchmark_capacity.py \
  --campaign-id "$BENCHMARK_REPRO_ID" --phase a --rounds 10 \
  --out "$BENCHMARK_REPRO_CSV"

python3 scripts/benchmark_capacity.py \
  --campaign-id "$BENCHMARK_REPRO_ID" --phase b --rounds 10 \
  --out "$BENCHMARK_REPRO_CSV"

python3 scripts/summarize_benchmark.py "$BENCHMARK_REPRO_CSV"
```

정본 승격, `--campaign-root`, `--canonical-out`, 별도 validator 명령은 삭제한다. 기존 캠페인 manifest와 2026-09-24 역사 실행 기록 안의 과거 명령은 당시 증거이므로 수정하지 않는다.

- [ ] **Step 4: 삭제된 활성 참조가 없는지 확인**

실행:

```bash
rg -n 'benchmark\.sh|benchmark_schedule|benchmark_schema|gatling_binary_log|parse_gatling_report|parse_optimistic_metrics|promote_benchmark_campaign|validate_benchmark_campaign|validate_benchmark_environment|validate_phase_b_input|write_benchmark_row' README.md PROJECT_PLAN.md docs --glob '!docs/benchmark/campaigns/**' --glob '!docs/benchmark/2026-09-24-controlled-run.md' --glob '!docs/internal/archive/2026-09-25-benchmark-tooling-simplification-design.md' --glob '!docs/superpowers/plans/2026-09-25-benchmark-tooling-simplification.md'
```

예상: 출력 없음

- [ ] **Step 5: 커밋 승인 요청 준비**

커밋 메시지 초안: `refactor: 과도한 벤치마크 게시 계층을 제거`

사용자 승인 전에는 커밋하지 않는다.

### Task 6: 전체 검증과 변경량 확인

**Files:**
- 검증: 전체 변경 파일
- 수정 없음: `docs/benchmark/raw-runs-v2.csv`
- 수정 없음: `docs/benchmark/campaigns/2026-09-25-williams-v2-01/raw-runs.csv`
- 수정 없음: `docs/benchmark/campaigns/2026-09-25-williams-v2-01/environment-phase-a.json`
- 수정 없음: `docs/benchmark/campaigns/2026-09-25-williams-v2-01/environment-phase-b.json`

**Interfaces:**
- Consumes: Task 1~5의 완성된 코드와 문서
- Produces: 빌드·테스트·데이터 불변성 증거와 커밋 초안

- [ ] **Step 1: Python 테스트 전체 실행**

실행:

```bash
python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v
```

예상: 새 `test_benchmark_capacity.py`와 단순화된 `test_summarize_benchmark.py` 모두 PASS

- [ ] **Step 2: Java·Gatling·스크립트 전체 빌드**

실행:

```bash
./gradlew gatlingClasses --console=plain
./gradlew build --console=plain
```

예상: Gatling Java DSL 컴파일, `benchmarkScriptTest`, Java 테스트를 포함해 두 명령 모두
`BUILD SUCCESSFUL`

- [ ] **Step 3: 기존 성공 캠페인 요약 재생성**

실행:

```bash
python3 scripts/summarize_benchmark.py docs/benchmark/raw-runs-v2.csv \
  > /tmp/benchmark-summary-simplified.md
rg -n 'baseline \(방어 없음\)|② 조건부 UPDATE|④ 비관적 락|⑤ 낙관적 락' \
  /tmp/benchmark-summary-simplified.md
```

예상: 네 전략 표현이 모두 존재하고 명령이 0으로 종료

- [ ] **Step 4: 기존 측정 증거가 바뀌지 않았는지 확인**

실행:

```bash
git diff --exit-code -- \
  docs/benchmark/raw-runs-v2.csv \
  docs/benchmark/campaigns/2026-09-25-williams-v2-01/raw-runs.csv \
  docs/benchmark/campaigns/2026-09-25-williams-v2-01/environment-phase-a.json \
  docs/benchmark/campaigns/2026-09-25-williams-v2-01/environment-phase-b.json
```

예상: 출력 없이 종료 코드 0

- [ ] **Step 5: 복잡도 감소 결과 확인**

실행:

```bash
find scripts -maxdepth 1 -type f -print | sort
find scripts/tests -maxdepth 1 -type f -name 'test_*.py' -print | sort
find scripts -maxdepth 1 -type f -print0 | xargs -0 wc -l
find scripts/tests -maxdepth 1 -type f -name 'test_*.py' -print0 | xargs -0 wc -l
git diff --stat
git status --short
```

예상: 활성 정원 경쟁 실행 파일은 `benchmark_capacity.py`, `summarize_benchmark.py` 두 개이고 테스트도 두 개다. 기존 대비 실행 코드와 테스트 코드가 모두 감소한다.

- [ ] **Step 6: 최종 커밋 승인 요청**

커밋하지 않은 전체 diff, 테스트 결과, 삭제·생성 파일, 다음 커밋 메시지 후보를 사용자에게 제시한다.

```text
refactor: 벤치마크 도구를 핵심 실험 흐름으로 단순화
```

사용자가 명시적으로 승인한 뒤에만 커밋한다.
