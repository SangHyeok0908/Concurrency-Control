# 벤치마크 방법론 v2 구현 계획

> **에이전트 작업자용:** **필수 하위 스킬:** 이 계획을 작업 단위로 구현할 때 `superpowers:subagent-driven-development`(권장) 또는 `superpowers:executing-plans`를 사용한다. 진행 상태는 체크박스(`- [ ]`) 문법으로 추적한다.

**목표:** 예약 전략을 변경하지 않으면서 고정 순서 벤치마크와 응답 시간 기반의 대체 TPS를, 재현 가능한 10개 처리의 Williams 스케줄·요청 타임스탬프에서 계산한 버스트 TPS·검증 및 추적 가능한 방법론 v2 캠페인으로 교체한다.

**아키텍처:** `scripts/benchmark.sh`는 Bash 3.2 호환 오케스트레이터로 유지하고, 결정적 계획 생성·Gatling 바이너리 디코딩·행 직렬화·캠페인 검증·승격·집계를 Python 3.9 표준 라이브러리 모듈로 옮긴다. 애플리케이션을 시작할 때마다 검증된 환경 스냅샷 하나를 보존하고, 측정한 처리마다 v2 행 하나를 생성하며, 완전하고 균형 잡힌 Phase A + B 캠페인만 정식 v2 데이터셋으로 승격하거나 공개용 요약을 생성할 수 있다.

**기술 스택:** Bash 3.2, Python 3.9 표준 라이브러리, Java 21, Spring Boot 3.5.16, Gatling 3.13.5, Gradle 래퍼, JUnit 5, Testcontainers/MySQL.

## 전역 제약

- 의도적으로 락을 사용하지 않는 baseline과 어떤 방어 전략의 SQL·트랜잭션 경계·재시도 정책·endpoint·동작도 변경하지 않는다.
- baseline에 분산 락, Redis 멱등성 키, 스키마 가드, 재시도를 도입하지 않는다.
- 구현 전 과정에서 다음 방법론 v1 파일을 바이트 단위로 보존한다.
  - `docs/benchmark/raw-runs.csv` — SHA-256 `7824f10229b460e911a49f47c46bb8d6338587de1401709c09a8e919ab1ed3c3`
  - `docs/benchmark/2026-09-24-controlled-run.md` — SHA-256 `6413ce6c1c1dc6d02b55d62a049cc21703e4caa1cff8a3d482b862648cc55814`
  - `docs/benchmark/optimistic-attempt-distribution-cap20.json` — SHA-256 `f193d7510ac6ce02934b99aa7c06232541f7917ba5639b9565a818d587535698`
- `scripts/validate_phase_b_input.py`와 `scripts/tests/test_validate_phase_b_input.py`는 v1 호환 경로로 유지한다. 방법론 v2에서는 이들을 호출하지 않는다.
- Python 3.9 호환 문법만 사용한다. `T | None` 대신 `Optional[T]`를 사용하고 `match`는 사용하지 않는다.
- Bash 3.2 호환 문법만 사용한다. 연관 배열, `mapfile`, `readarray`, Bash 4 전용 확장을 사용하지 않는다. `jq`나 다른 runtime dependency를 추가하지 않는다.
- 캠페인과 `--print-plan`은 완전한 Williams 블록을 유지하는, 10의 양의 배수인 measured round 수를 허용한다. 현재 정식 v2 공개 데이터는 승인된 기본값인 phase당 10 round로 고정한다. 즉 A 100행, B 100행, 총 200행이다. 더 긴 유효 캠페인은 `docs/benchmark/campaigns/<campaign_id>/` 아래에 유지하고 요약할 수 있지만, 별도의 공개 계약이 승인되기 전에는 `docs/benchmark/raw-runs-v2.csv`로 승격하지 않는다.
- warmup은 CSV 행으로 기록하지 않는다. 애플리케이션을 시작할 때마다 첫 Williams 행 전체를 한 번 실행하고, 그 결과는 캠페인 manifest에 기록한다.
- 측정 실패 시 타입이 지정된 실패 행을 최대 하나만 기록한 뒤 중단한다. Phase A의 환경/warmup 실패는 CSV를 생성하지 않는다. Phase B에서 같은 유형의 실패가 나면 기존 Phase A CSV를 바이트 단위로 동일하게 보존한다. 두 경우 모두 manifest와 stderr에만 기록한다.
- 모든 CSV 직렬화는 `csv.DictWriter`를 거친다. Bash에서 v2 CSV 행을 직접 이어 붙이지 않는다.
- 모든 구현 작업은 red-green-refactor를 따른다. 실제 코드를 작성하기 전에 실패한 테스트 출력을 기록하고, 좁은 범위의 테스트와 관련 회귀 테스트 묶음을 다시 실행한다.
- 정확한 staged diff와 커밋 메시지 초안을 사용자에게 보여주고 해당 커밋에 대한 명시적 승인을 받기 전에는 이 계획에서 `git commit`을 실행하지 않는다.

## 검토 중점

1. Gatling 문자열과 레코드: Java compact-string coder 처리, 모든 레코드 유형이 공유하는 전역 cached-string 테이블, 지원하지 않는 헤더, 각 레코드 형태의 중간에서 발생하는 EOF를 검증한다. 담당 테스트는 작업 2에 있다.
2. 행 의미론: 빈 값과 숫자 0의 구분, 단계별 부분 필드, `error_reason`의 CR/LF 거부, 정확한 헤더 검사, 쓰기 실패 후 롤백을 검증한다. 담당 테스트는 작업 3에 있다.
3. Phase 경계: 잘못된 캠페인, 잘못된 환경 cap/hash, 미완료 Phase A, 이미 실패한 캠페인으로 Phase B를 실행하면 Gatling 시작 전에 거부한다. 담당 테스트는 작업 5와 작업 6에 있다.
4. 공개 안전성: 실패했거나 미완료인 캠페인은 승격할 수 없고, 기존 정식 대상은 절대 교체하지 않으며, 승격 실패 시 캠페인 원본을 보존한다. 담당 테스트는 작업 5에 있다.
5. 이식성과 불변성: Bash 4나 `jq` 없이 실행기를 실행하고, `--print-plan`에 애플리케이션이 필요 없음을 증명하며, Phase A 측정 전 실패 시 CSV가 생성되지 않고 Phase B 측정 전 실패 시 A가 보존됨을 증명하고, v1 해시 3개를 모두 다시 확인한다. 담당 테스트는 작업 6과 작업 8에 있다.

## 파일 구조

```text
scripts/
├── benchmark.sh                         # v2 오케스트레이션 전용
├── benchmark_schedule.py                # 순수 Williams 스케줄 + TSV CLI
├── benchmark_schema.py                  # v2 field/status/value 불변식
├── gatling_binary_log.py                # Gatling 3.13.5 바이너리 디코더
├── parse_gatling_report.py              # stats.js + 바이너리 교차 검증
├── promote_benchmark_campaign.py        # 검증된 atomic no-clobber 공개
├── summarize_benchmark.py               # 검증된 v2 및 명시적 v1 출력
├── validate_benchmark_campaign.py       # 균형/완전성 검증
├── validate_benchmark_environment.py    # 검증 + 정식 스냅샷
├── validate_phase_b_input.py            # 유지, v1 전용
├── write_benchmark_row.py               # atomic csv.DictWriter 경계
└── tests/
    ├── fixtures/gatling-3.13.5-baseline-cap1-cont200/
    │   ├── simulation.log
    │   └── js/stats.js
    ├── test_benchmark_schedule.py
    ├── test_benchmark_script.py
    ├── test_gatling_binary_log.py
    ├── test_parse_gatling_report.py
    ├── test_promote_benchmark_campaign.py
    ├── test_summarize_benchmark.py
    ├── test_validate_benchmark_campaign.py
    ├── test_validate_benchmark_environment.py
    └── test_write_benchmark_row.py
src/test/java/com/interview/reservation/config/
└── BenchmarkEnvironmentMaxAttempts20EndpointTest.java
docs/benchmark/
├── raw-runs.csv                          # 변경 불가 방법론 v1
├── raw-runs-v2.csv                       # 승격된 방법론 v2
└── campaigns/2026-09-24-williams-v2/
    ├── environment-phase-a.json
    ├── environment-phase-b.json
    ├── manifest.md
    └── raw-runs.csv
```

`benchmarkScriptTest`가 이미 모든 `scripts/tests/test_*.py` 파일을 발견하고 `check`가 해당 Task에 의존하므로 `build.gradle`은 수정할 필요가 없다.

---

### 작업 1: 결정적 Williams 스케줄러 구축

**파일:**

- 생성: `scripts/benchmark_schedule.py`
- 생성: `scripts/tests/test_benchmark_schedule.py`

**인터페이스:**

```python
from dataclasses import dataclass
from typing import List, Sequence, TextIO

SCHEDULE_VERSION = "williams-10-v1"
WILLIAMS_BASE = (0, 1, 9, 2, 8, 3, 7, 4, 6, 5)

@dataclass(frozen=True)
class Treatment:
    treatment_id: int
    strategy: str
    contention: str
    capacity: int
    contenders: int

@dataclass(frozen=True)
class ScheduleEntry:
    schedule_version: str
    schedule_cycle: int
    schedule_row: int
    measured_round: int
    position_in_round: int
    treatment: Treatment

def build_schedule(rounds: int) -> List[ScheduleEntry]:
    """결정적 스케줄을 반환하고, 양수가 아니거나 10의 배수가 아닌 round는 거부한다."""

def write_tsv(entries: Sequence[ScheduleEntry], output: TextIO) -> None:
    """DictWriter(delimiter='\t')로 안정적인 LF 전용 header와 행을 기록한다."""
```

treatment ID mapping은 명세의 ID 0–9와 정확히 일치한다. CLI는 `--rounds N`을 허용하며, stdout에는 다음 header를 정확히 사용하는 결정적 TSV를 출력한다.

```text
schedule_version\tschedule_cycle\tschedule_row\tmeasured_round\tposition_in_round\ttreatment_id\tstrategy\tcontention\tcapacity\tcontenders
```

- [ ] `test_first_row_matches_the_approved_snapshot`을 추가해 treatment ID `0,1,9,2,8,3,7,4,6,5`와 대응하는 strategy/contention 이름을 단언한다.
- [ ] `test_ten_rounds_cover_every_treatment_and_position_once`, `test_every_directed_within_round_adjacent_pair_occurs_once`, `test_strategy_and_contention_position_balance`를 추가한다.
- [ ] `test_twenty_rounds_form_two_identical_cycles_with_distinct_cycle_numbers`와 `test_tsv_is_byte_deterministic`를 추가한다.
- [ ] TSV writer가 `lineterminator="\n"`을 사용하고 carriage return을 포함하지 않으며 정확히 하나의 LF로 끝나는지 단언한다. 이 검사는 `\r`이 Bash의 마지막 `contenders` field로 유입되는 것을 막는다.
- [ ] `0`, `-10`, `1`, `9`, `11` round를 exit code 2와 빈 stdout으로 거부하는 subtest를 추가한다.
- [ ] `python3 scripts/tests/test_benchmark_schedule.py -v`를 실행한다. 모듈이 없어서 새 테스트가 실패하는지 확인하고, 실패 출력을 작업 기록에 보존한다.
- [ ] dataclass, 10-treatment constant, Williams 연산, validation, TSV writer, `argparse` CLI를 구현한다. 이 모듈에는 HTTP, DB, filesystem state, randomness를 포함하지 않는다.
- [ ] `python3 scripts/tests/test_benchmark_schedule.py -v`를 실행해 모든 scheduler 테스트가 통과하는지 확인한다.
- [ ] `python3 -m py_compile scripts/benchmark_schedule.py`를 실행하고 두 번 생성한 `--rounds 10` 출력을 `cmp`로 비교한다.
- [ ] 나열한 두 파일 외의 변경이 있는지 diff를 검토한다. 커밋 초안 `feat: add balanced benchmark schedule`을 제시하고, 사용자가 명시적으로 승인한 뒤에만 커밋한다.

### 작업 2: Gatling 요청 타임스탬프 디코딩 및 HTML 통계 교차 검증

**파일:**

- 생성: `scripts/gatling_binary_log.py`
- 생성: `scripts/tests/test_gatling_binary_log.py`
- 생성: `scripts/tests/test_parse_gatling_report.py`
- 생성: `scripts/tests/fixtures/gatling-3.13.5-baseline-cap1-cont200/simulation.log`
- 생성: `scripts/tests/fixtures/gatling-3.13.5-baseline-cap1-cont200/js/stats.js`
- 수정: `scripts/parse_gatling_report.py`

**인터페이스:**

```python
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

@dataclass(frozen=True)
class RunHeader:
    version: str
    simulation_class: str
    run_start_epoch_ms: int
    description: str

@dataclass(frozen=True)
class RequestEvent:
    name: str
    start_epoch_ms: int
    end_epoch_ms: int
    ok: bool
    message: str

@dataclass(frozen=True)
class BurstMetrics:
    requests: int
    ok: int
    ko: int
    burst_start_epoch_ms: int
    burst_end_epoch_ms: int
    burst_wall_ms: int
    tps: float

def read_request_events(log_path: Path) -> Tuple[RunHeader, List[RequestEvent]]:
    """Gatling 3.13.5 record를 디코딩하고 손상됐거나 지원하지 않는 log를 거부한다."""

def burst_metrics(
    log_path: Path,
    exact_label: str,
    expected_requests: Optional[int] = None,
) -> BurstMetrics:
    """일치하는 OK/KO 요청을 첫 start와 마지막 end를 사용해 집계한다."""
```

`BurstMetrics.tps`는 `round(requests * 1000.0 / burst_wall_ms, 1)`이다. 공개한 rate를 독립적으로 다시 계산할 수 있도록 원본 start, end, wall 값은 정수로 유지한다.

decoder 계약은 Gatling 3.13.5로 고정한다.

- scalar integer는 big-endian이다. signed i32는 `>i`, signed i64는 `>q`를 사용한다.
- record tag는 Run=0, Request=1, User=2, Group=3, Error=4이다.
- boolean은 1 byte이다. `1`은 true/OK/start, `0`은 false/KO/end이며, 다른 값은 실패 처리한다.
- string은 i32 byte length로 시작한다. 0이면 coder byte가 없는 빈 문자열이다. 양수 길이 뒤에는 해당 byte와 coder byte 하나가 온다. coder 0은 Latin-1, coder 1은 `sys.byteorder`에 따른 UTF-16이며, 알 수 없는 coder는 실패 처리한다.
- cached string은 i32 index이다. 0 이상의 index 뒤에는 문자열이 오며 전역의 해당 index에 저장한다. 음수 index는 `-index`에 있는 이전 전역 entry를 참조한다. 누락된 참조와 중복 선언은 실패 처리한다.
- 첫 레코드는 반드시 `Run`이어야 한다. 버전, 시뮬레이션 클래스, 실행 시작 i64, 설명, 시나리오 문자열, assertion 원시 바이트 블록을 디코딩한다. 요청 타임스탬프는 `run_start_epoch_ms + offset`이다.
- `Request`는 그룹 수 i32, 그 수만큼의 캐시된 그룹 문자열, 캐시된 요청 이름, 시작 offset i32, 종료 offset i32, OK boolean, 캐시된 메시지로 구성된다.
- `User`는 시나리오 index i32, 시작/종료 boolean, 타임스탬프 offset i32로 구성된다.
- `Group`은 그룹 수 i32, 캐시된 그룹 문자열, 시작 offset i32, 종료 offset i32, 누적 응답 시간 i32, OK boolean로 구성된다.
- `Error`는 캐시된 메시지와 타임스탬프 offset i32로 구성된다.
- 뒤의 `Request` 참조가 유효하도록 `User`, `Group`, `Error` 레코드를 그 캐시 문자열까지 모두 소비한다. 두 번째 `Run` 레코드는 유효하지 않다.
- 레코드 경계의 EOF는 정상 종료다. 태그나 필드가 시작된 뒤의 EOF는 손상된 레코드 오류다.

`parse_gatling_report.py`는 요청 수, OK/KO, 평균, p95, 최댓값을 계속 `stats.js`에서 읽는다. 여기에 `simulation.log`를 추가해 `burst_metrics(log_path, exact_label, expected_requests=contenders)`를 호출하고, 두 출처의 요청 수와 OK/KO가 동일해야 통과시키며, Bash 실행기에 다음 고정 CSV 필드 순서로 출력한다.

```text
requests,ok,ko,mean_ms,p95_ms,max_ms,burst_start_epoch_ms,burst_end_epoch_ms,burst_wall_ms,tps
```

- [ ] 기존 실제 fixture를 `build/reports/gatling/baselinereservationsimulation-20260904093201464/`에서 fixture directory로 정확한 binary/text copy로 복사한다. staging 전에 `simulation.log`의 SHA-256이 `0fef6eacb29a7c0a926af4eeaddee38a6d73a6733c6dcadcc64753dcdec62fb3`, `stats.js`의 SHA-256이 `7662d2495510d03bb7577354afcf4e1021a88980c50b4a438ff9218e0e7a4249`인지 확인한다.
- [ ] 다섯 record type, 전역 cached-string 선언/참조, coder 0, `sys.byteorder`를 사용한 coder 1, OK/KO가 섞인 요청, 서로 다른 start, 무관한 request name을 출력하는 작은 binary-fixture builder를 `test_gatling_binary_log.py` 안에 추가한다.
- [ ] 실제 fixture 테스트를 추가한다. version `3.13.5`, run start `1788514321464`, 정확한 label `reserve [baseline cap=1 cont=200]`, 요청 200개, OK 191개, KO 9개, start `1788514323074`, end `1788514323238`, wall `164`, 소수점 첫째 자리 반올림 TPS `1219.5`를 검증한다.
- [ ] 지원하지 않는 버전·레코드 태그·coder, 유효하지 않은 boolean, 음수 문자열 길이, 누락된 cached-string 참조, 중복 cached-string 선언, 시작보다 이른 종료, 0인 벽시계 구간, 요청 수 불일치, 각 레코드 태그/필드 계열 뒤의 잘림을 다루는 손상 subtest를 추가한다.
- [ ] `stats.js`의 count/OK/KO mismatch가 거부됨을 증명하고, 예전 `requests / max_ms` 값을 사용하지 않음을 증명하는 report integration test를 추가한다.
- [ ] `python3 scripts/tests/test_gatling_binary_log.py -v`와 `python3 scripts/tests/test_parse_gatling_report.py -v`를 실행해 production code 작성 전에 실패하는지 확인한다.
- [ ] `gatling_binary_log.py`에 bounds-checking reader를 구현하고 `parse_gatling_report.py`에 통합한다. parser error는 한 줄짜리 stderr message로 출력하고 0이 아닌 코드로 종료한다. 알 수 없는 record를 조용히 건너뛰거나 TPS 0을 반환하지 않는다.
- [ ] 기존 파서 docstring의 “모든 요청이 t=0에서 시작한다”는 설명과 `requests/max_ms` 주장을 타임스탬프 기반 정의로 교체한다.
- [ ] 새 테스트 파일 두 개, `python3 scripts/tests/test_parse_optimistic_metrics.py -v`, `python3 -m py_compile scripts/gatling_binary_log.py scripts/parse_gatling_report.py`를 실행한다.
- [ ] 실제 fixture에 CLI를 실행해 stdout이 정확히 `200,191,9,86,132,136,1788514323074,1788514323238,164,1219.5`인지 단언한다.
- [ ] binary fixture hash와 전체 diff를 검토한다. 커밋 초안 `fix: derive benchmark TPS from request timestamps`를 제시하고, 사용자가 명시적으로 승인한 뒤에만 커밋한다.

### 작업 3: 방법론 v2 행 정의 및 원자적 쓰기

**파일:**

- 생성: `scripts/benchmark_schema.py`
- 생성: `scripts/write_benchmark_row.py`
- 생성: `scripts/tests/test_write_benchmark_row.py`

**인터페이스:**

```python
from pathlib import Path
from typing import Dict, List

SCHEMA_VERSION = "2"
SCHEDULE_VERSION = "williams-10-v1"
FIELDS = [
    "schema_version", "campaign_id", "phase", "app_start_id",
    "environment_sha256", "schedule_version", "schedule_cycle",
    "schedule_row", "measured_round", "position_in_round", "treatment_id",
    "ts", "strategy", "optimistic_max_attempts", "contention", "capacity",
    "contenders", "slot_id", "requests", "ok", "ko", "mean_ms", "p95_ms",
    "max_ms", "burst_start_epoch_ms", "burst_end_epoch_ms", "burst_wall_ms",
    "tps", "remaining", "confirmed", "overbooking", "duplicates",
    "retry_succeeded", "retry_exhausted", "version_conflicts", "deadlocks",
    "mean_attempts", "run_status", "error_reason",
]
RUN_STATUSES = (
    "ok", "seed_failed", "gatling_failed", "parse_failed", "db_read_failed",
    "retry_metrics_failed",
)

def normalize_and_validate_row(payload: object) -> Dict[str, str]:
    """JSON null을 빈 값으로 변환하고 모든 FIELDS를 CSV 문자열로 반환한다."""

def validate_csv_row(row: Dict[str, str]) -> Dict[str, str]:
    """JSON-null 변환 없이 이미 디코딩한 CSV 행을 검증한다."""

def write_new(path: Path, row: Dict[str, str]) -> None:
    """부분 출력 없이 header와 한 행을 담은 새 파일을 배타적으로 생성한다."""

def append(path: Path, row: Dict[str, str]) -> None:
    """정확한 header 검증 후 같은 directory의 임시 파일 교체를 통해 행을 추가한다."""
```

CLI 계약:

```text
python3 scripts/write_benchmark_row.py (--create | --append) PATH
```

stdin에서 JSON object를 정확히 하나만 읽고 뒤따르는 JSON value는 거부한다. `schema_version`부터 `contenders`까지의 plan/environment field와 `ts`, `run_status`는 모든 행에서 필수다. `ok`는 모든 measurement field와 빈 `error_reason`을 요구한다. 실패 행은 비어 있지 않은 한 줄짜리 `error_reason`을 요구하고, 실패 단계 전에 얻은 field를 보존하며, 다음 단계 field는 아래 stage boundary에 따라 비워 둬야 한다.

| status | 허용되는 measurement 형태 |
|---|---|
| `seed_failed` | 모든 measurement field가 비어 있음 |
| `gatling_failed` | 모든 measurement field가 비어 있음 |
| `parse_failed` | `slot_id` 필수, report·DB·retry field는 비어 있음 |
| `db_read_failed` | Gatling 전후 slot-query 실패이면 모든 measurement field가 비어 있거나, invariant-query 실패이면 `slot_id`와 모든 report field가 있고 DB/retry field는 비어 있음 |
| `retry_metrics_failed` | reset 실패이면 모든 measurement field가 비어 있거나, snapshot/parse 실패이면 `slot_id`와 모든 report·DB field가 있고 retry field는 비어 있음 |

허용되는 failure 형태는 이것뿐이다. 특히 일부만 채워진 report group이나 DB group은 유효하지 않다. `ok`는 `slot_id`, 완전한 report group(`requests`부터 `tps`), 완전한 DB group(`remaining`부터 `duplicates`), 완전한 retry group(`retry_succeeded`부터 `mean_attempts`)을 요구한다.

`0`은 문자열 `0`으로 유지하고 JSON `null`만 빈 CSV 필드로 바꾼다. 숫자·열거형·캠페인 ID·phase/cap·처리 매핑·파생된 schedule-cycle/row 불변식은 `benchmark_schema.py`에서 중앙 검증한다. 64자의 소문자 16진수 `environment_sha256`, `app_start_id == <campaign_id>-phase-<phase>`, `ts`의 ISO-8601 현지 타임스탬프 문자열, 양수인 capacity/contenders, 값이 존재할 때 0 이상인 count/latency 지표를 요구한다. 고장 난 전략의 원본 DB 상태도 증거 작성기가 거부하지 않고 기록해야 하므로 `remaining`만 부호 있는 정수로 허용한다.

- [ ] 완전하고 유효한 행 fixture를 추가하고, 정확한 39-column header/order, JSON `null`의 빈 값 변환, 숫자 0 보존, error reason 안의 comma/quote, exclusive create 동작을 테스트한다.
- [ ] 허용되는 접두 필드와 반드시 비어야 하는 접미 필드를 검증하도록 실패 상태마다 테스트 하나를 추가한다. 유효한 `db_read_failed` 형태 두 가지와 `retry_metrics_failed` 형태 두 가지를 모두 포함한다. 일부만 채워진 모든 report/DB/retry 그룹, 누락된 계획 필드, 초과 필드, 알 수 없는 상태, 리터럴 문자열 `null`, 유효하지 않은 `[A-Za-z0-9._-]+` 캠페인 ID, phase와 일치하지 않는 cap, strategy/contention과 일치하지 않는 처리, 잘못 파생된 schedule number의 거부 사례도 추가한다.
- [ ] CR, LF, CRLF가 포함된 `error_reason` 거부 subtest를 추가한다.
- [ ] create 시점의 writer, flush, fsync, link-publication 실패를 각각 모사하고 destination이 계속 존재하지 않으며 temporary file도 남지 않는지 단언한다. append 시점의 writer, flush, fsync, replacement 실패를 각각 모사하고 원본 byte가 변경되지 않으며 temporary file도 남지 않는지 단언한다.
- [ ] link 직전에 destination을 생성하는 late-create race test를 추가해 `write_new`가 destination을 덮어쓰지 않고 dangling symlink나 실제 symlink를 따라가거나 교체하지도 않음을 증명한다.
- [ ] `python3 scripts/tests/test_write_benchmark_row.py -v`를 실행해 구현 전에 실패하는지 확인한다.
- [ ] 먼저 constant와 순수 normalization/validation을 구현한다. exclusive creation은 같은 directory의 temporary file에 쓰고 fsync한 뒤 `os.link(temp, path)`로 공개해 기존 파일을 교체할 수 없게 한다. append는 검증된 원본과 새 행 하나를 같은 directory의 temporary file로 복사하고 flush/fsync한 뒤, 전체 replacement의 durability가 확보된 후에만 `os.replace`를 사용한다. `finally` block에서 temporary path를 정리한다.
- [ ] `--create`가 모든 기존 path를 거부하고 `--append`가 누락된 path나 정확하지 않은 header를 거부하는지 확인한다. 중복되거나 순서가 바뀐 header를 허용하지 않는다.
- [ ] `python3 scripts/tests/test_write_benchmark_row.py -v`와 `python3 -m py_compile scripts/benchmark_schema.py scripts/write_benchmark_row.py`를 실행한다.
- [ ] diff를 검토한다. 커밋 초안 `feat: add benchmark v2 row schema`를 제시하고, 사용자가 명시적으로 승인한 뒤에만 커밋한다.

### 작업 4: 애플리케이션 시작별 정식 환경 스냅샷 보존

**파일:**

- 수정: `scripts/validate_benchmark_environment.py`
- 수정: `scripts/tests/test_validate_benchmark_environment.py`
- 생성: `src/test/java/com/interview/reservation/config/BenchmarkEnvironmentMaxAttempts20EndpointTest.java`

**인터페이스:**

다음 optional path를 추가한다.

```text
python3 scripts/validate_benchmark_environment.py \
  --expected-max-attempts 20 \
  --canonical-output docs/benchmark/campaigns/<campaign_id>/environment-phase-b.json
```

validation에 성공하면 전체 API object를 UTF-8 JSON으로 기록한다. `sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False`와 마지막 newline 하나를 사용한다. 같은 directory의 temporary file에 쓰고 fsync한 뒤 atomic no-clobber link로 공개한다. 기존 snapshot은 error이며 절대 교체하지 않는다. 기존 human-readable verification line과 두 번째 machine-readable line `environment_sha256=<64 lowercase hex>`를 출력한다. mismatch 또는 pool-not-ready response에서는 output을 생성하거나 변경하면 안 된다.

새 Spring test는 기존 endpoint test와 같은 Testcontainers base와 profile에 다음 설정을 추가한다.

```java
@SpringBootTest(properties = "reservation.optimistic.max-attempts=20")
```

Hikari connection 100개를 기다리고 `$.optimisticMaxAttempts`가 `20`인지 단언한다. override와 무관한 assertion은 중복하지 않는다.

- [ ] byte-stable sorted compact JSON, 마지막 newline, 출력된 SHA와 file byte의 일치, validation 실패 시 output 없음, 실패 시 기존 file 보존, output이 이미 있을 때 validation 성공 후에도 no-clobber임을 검증하는 Python test를 추가한다.
- [ ] max-attempts-20 endpoint test를 추가하고 먼저 `./gradlew test --tests 'com.interview.reservation.config.BenchmarkEnvironmentMaxAttempts20EndpointTest' --console=plain`을 실행한다. class가 존재하기 전에 characterization test가 실패하는지 확인한다.
- [ ] 새 Python test를 실행해 처음에는 `--canonical-output`을 인식하지 못하는지 확인한다.
- [ ] 기존 환경 검사나 종료 코드의 의미(`1` 불일치/유효하지 않음, `2` pool 준비 안 됨)를 약화하지 않고 canonical-output 쓰기를 구현한다.
- [ ] `python3 scripts/tests/test_validate_benchmark_environment.py -v`를 실행한다.
- [ ] 두 Spring test를 실행한다. `./gradlew test --tests 'com.interview.reservation.config.BenchmarkEnvironmentEndpointTest' --tests 'com.interview.reservation.config.BenchmarkEnvironmentMaxAttempts20EndpointTest' --console=plain`
- [ ] diff를 검토한다. 커밋 초안 `feat: persist verified benchmark environments`를 제시하고, 사용자가 명시적으로 승인한 뒤에만 커밋한다.

### 작업 5: 캠페인 검증 및 완전한 증거만 승격

**파일:**

- 생성: `scripts/validate_benchmark_campaign.py`
- 생성: `scripts/promote_benchmark_campaign.py`
- 생성: `scripts/tests/test_validate_benchmark_campaign.py`
- 생성: `scripts/tests/test_promote_benchmark_campaign.py`

**인터페이스:**

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

@dataclass(frozen=True)
class CampaignSummary:
    campaign_id: str
    phases: List[str]
    measured_rounds: int
    rows_per_phase: Dict[str, int]
    environment_sha256_by_phase: Dict[str, str]

def validate_campaign(
    csv_path: Path,
    requirement: str,
    campaign_id: Optional[str] = None,
    expected_rounds: Optional[int] = None,
) -> CampaignSummary:
    """완전한 10-round block 단위로 phase-a 또는 complete 증거를 검증한다."""

def validate_rows(
    rows: List[Dict[str, str]],
    requirement: str,
    campaign_id: Optional[str] = None,
    expected_rounds: Optional[int] = None,
) -> CampaignSummary:
    """memory에서 선택한 캠페인에 동일한 캠페인 불변식을 적용한다."""
```

validator CLI:

```text
python3 scripts/validate_benchmark_campaign.py PATH --require phase-a
python3 scripts/validate_benchmark_campaign.py PATH --require complete
python3 scripts/validate_benchmark_campaign.py PATH --require complete --rounds 20
```

`benchmark_schema.py`의 row validation을 재사용한 뒤 다음을 확인한다. 하나의 캠페인, 모두 `ok`, 서로 다른 양수 slot ID, 정확한 phase/cap/app-start/environment-hash 일관성, 개수가 10의 양의 배수인 연속된 round, round당 10개 treatment, 10-round block마다 treatment×position 한 번씩, block마다 방향성이 있는 round 내 인접 pair 90개가 모두 한 번씩, `requests == contenders == ok + ko`, `overbooking == max(confirmed - capacity, 0)`, `burst_wall_ms == burst_end_epoch_ms - burst_start_epoch_ms > 0`, `tps == round(requests * 1000.0 / burst_wall_ms, 1)`. `expected_rounds`/`--rounds`가 주어지면 추론한 round 수가 이 값과 같아야 한다. `phase-a`는 모든 B 행을 거부하고 `rounds * 10`개의 A 행을 요구한다. `complete`는 file 순서상 A 다음 B, 두 phase에서 같은 round 수, 총 `rounds * 20`개 행을 요구한다.

promoter CLI:

```text
python3 scripts/promote_benchmark_campaign.py SOURCE --to DESTINATION
```

먼저 source를 `destination.parent`에 생성한 전용 temporary file로 복사해 fsync하고, 공개할 정확한 200개 evidence 행에 `validate_campaign(temp, requirement="complete", expected_rounds=10)`을 호출한다. 검증된 이 temporary file만 atomic no-clobber operation(`os.link(temp, destination)` 후 temp unlink)으로 공개한다. 기존 destination을 거부하고, source는 절대 삭제하지 않으며, 모든 실패에서 temp를 정리한다. 이 순서는 cross-filesystem link와 한 source version을 검증하면서 다른 version을 공개하는 문제를 모두 방지한다.

- [ ] CSV string을 수작업으로 유지하지 않고 scheduler와 schema helper를 사용해 유효한 100행 Phase A, 200행 complete, 400행/20-round complete fixture를 만든다.
- [ ] 10-round 및 20-round `phase-a`/`complete` 캠페인과 예상 round 불일치 acceptance test를 추가한다. 이어서 실패 상태, 누락/중복 처리, 중복 위치, 잘못된 캠페인, 두 캠페인 ID, 잘못된 A/B cap, 잘못된 app-start ID, 여러 phase 해시, 요청 수 불일치, 중복 slot ID, 잘못된 TPS, 불완전 블록, 깨진 인접 쌍 균형, A보다 앞선 B, `phase-a` 모드에 존재하는 B를 각각 mutation으로 검증한다.
- [ ] 다른 campaign ID, 잘못된 cap 20, B 안에서 변경된 hash, incomplete A, failure row가 complete result가 생기기 전에 거부됨을 보여주는 명시적 Phase B boundary test를 추가한다.
- [ ] promoter test를 추가해 성공 시 바이트가 동일한 200행 공개, 명시적인 20-round/400행 거부, incomplete/failed 거부, destination parent와 다른 filesystem/path의 source, 기존 destination no-clobber, source 보존, link publication 실패를 모사했을 때 temp 정리, 나중에 변경된 source path가 아니라 복사한 temporary byte 검증, link 직전에 생긴 destination을 확인한다.
- [ ] 새 테스트 파일 두 개를 실행해 구현 전에 실패하는지 확인한다.
- [ ] 간결한 한 줄 stderr와 0이 아닌 exit를 제공하는 재사용 가능 library/CLI로 validator를 구현한 뒤 promoter를 구현한다. v1 Phase-B validator를 import하거나 수정하지 않는다.
- [ ] `python3 scripts/tests/test_validate_benchmark_campaign.py -v`, `python3 scripts/tests/test_promote_benchmark_campaign.py -v`, `python3 scripts/tests/test_validate_phase_b_input.py -v`를 실행한다.
- [ ] diff를 검토한다. 커밋 초안 `feat: validate complete benchmark campaigns`를 제시하고, 사용자가 명시적으로 승인한 뒤에만 커밋한다.

### 작업 6: 고정 반복문을 2단계 캠페인 실행기로 교체

**파일:**

- 수정: `scripts/benchmark.sh`
- 생성: `scripts/tests/test_benchmark_script.py`

**CLI 계약:**

```text
scripts/benchmark.sh --campaign-id ID --phase a [--rounds 10]
scripts/benchmark.sh --campaign-id ID --phase b [--rounds 10]
scripts/benchmark.sh --campaign-id ID --phase a --rounds 20 --print-plan
```

optional test/diagnostic path는 명시적으로만 지정하며 v1 canonical file을 가리킬 수 없다.

```text
--campaign-root PATH
--canonical-out PATH
```

기본값은 `docs/benchmark/campaigns`와 `docs/benchmark/raw-runs-v2.csv`이다. v2 runner에서 방법론 v1의 `--out`, publish-path `--strategies`, `ATTEMPT_DISTRIBUTION_JSON` 동작을 제거한다. 임시 one-strategy smoke test는 `./gradlew gatlingRun`을 직접 사용하며, 공개 가능한 캠페인이 아니다.

testability를 위해 모든 Gradle 호출은 기본값이 `./gradlew`인 `BENCHMARK_GRADLEW`를 거친다. production과 문서화한 측정 명령에서는 설정하지 않는다. 테스트에서는 absolute executable shim을 가리킨다. PATH에만 있는 shim은 slash를 포함한 `./gradlew` 명령을 가로챌 수 없다.

실제 캠페인과 `--print-plan`은 모두 10의 양의 배수를 허용한다. 요청한 count를 모든 phase/completion validator에 전달한다. 완전한 10-round Phase B 뒤에는 canonical file을 승격한다. 더 긴 Phase B가 완료되면 manifest에 `complete_noncanonical`을 기록하고 campaign path를 출력한 뒤 성공으로 반환하며 promoter는 호출하지 않는다.

**실행 순서:**

1. option을 parse하고 완전히 검증한다. `--print-plan`은 scheduler를 즉시 exec하고 campaign/app/DB path는 건드리지 않는다.
2. `<campaign-root>/<campaign-id>`, CSV, manifest, phase 환경 JSON, `app_start_id=<campaign-id>-phase-<phase>`를 결정한다.
3. Phase A는 캠페인 디렉터리 전체가 존재하지 않아야 한다. 실패한 캠페인은 같은 ID로 다시 시작하지 않는다. Phase B는 디렉터리, manifest, Phase A 환경 JSON, CSV를 요구한다. 현재 애플리케이션 환경에 접근하기 전에 환경 파일 해시를 모든 A 행과 대조하고 v2 검증기를 `--require phase-a --rounds <requested>`로 호출한다. 또한 Phase B 환경 파일, manifest 시도/상태, CSV 행이 없어야 하므로 실패한 Phase B 시도를 같은 ID로 재개할 수 없다.
4. environment를 가져와 검증하고 canonical JSON으로 보존한 뒤 출력된 SHA만 추출한다. Phase A에서 실패하면 manifest/stderr에 기록하고 CSV 없이 종료한다. Phase B environment 실패 시 기존 100행 Phase A CSV를 바이트 단위로 동일하게 유지하고, B 행을 추가하거나 승격하지 않는다.
5. manifest 전제 조건으로 Git HEAD, OS, `java -version`, `./gradlew --version`, Gatling 3.13.5, MySQL 버전, `max_connections`, 초기 slot/reservation 수, 환경 경로/해시, 정확한 명령을 기록한다. 고정 제목 `# Benchmark Campaign <campaign_id>`, `## Campaign`, `## Phase A`, `## Phase B`를 사용한다. 각 phase 아래에는 `app_start_id`, 환경 경로/해시, 예상 재시도 cap, 실행 전 개수, warmup 상태, 측정 상태/행 수, 시작/종료 타임스탬프, 실패 이유를 기록한다. Phase A 증거를 다시 쓰지 않고 Phase B를 추가한다.
6. schedule row 1의 모든 treatment를 unmeasured warmup으로 한 번씩 실행한다. 측정과 같은 seed/Gatling/new-report/new-slot check를 사용한다. Phase A warmup 실패 시 CSV 없이 종료한다. Phase B warmup 실패 시 Phase A CSV를 바이트 단위로 동일하게 유지하고 B 행을 추가하거나 승격하지 않는다. 두 경우 모두 manifest/stderr에 기록하며, 다시 시도하려면 새 campaign ID를 요구한다.
7. 각 measured entry에 대해 retry metric을 reset하고, 이전 max slot을 query하고, report directory를 snapshot하고, Gatling을 실행한다. exit가 0이면 먼저 새 slot을 query하고 검증한 뒤에만 새 report를 식별하고 parse한다. 하나의 SQL statement와 엄격한 three-column parser로 `remaining`, `confirmed`, `duplicates`를 얻어 DB field group 전체가 atomic하게 성공하거나 실패하게 한다. retry metric을 fetch/parse하고, Python `json.dumps`로 JSON payload 하나를 생성해 첫 A 행에는 `--create`, 이후에는 `--append`로 row writer에 pipe한다.
8. 측정 시작 후 첫 실패가 발생하면 planning/environment prefix와 해당 단계까지 정당하게 얻은 field를 직렬화하고, 일치하는 `run_status`를 지정해 한 번만 append하고, manifest event를 기록한 뒤 0이 아닌 코드로 종료한다.
9. Phase A 뒤에는 `--require phase-a --rounds <requested>`로 검증한다. Phase B 뒤에는 `--require complete --rounds <requested>`로 검증한다. 10 round일 때만 no-clobber promoter를 호출한다. 승격 실패 시 완전한 campaign file을 그대로 남기고 0이 아닌 코드로 종료한다. 더 긴 유효 캠페인은 canonical promotion 없이 성공한다.

report parser는 이제 10개 값을 반환한다.

```bash
IFS=',' read -r requests ok ko mean_ms p95_ms max_ms \
  burst_start_epoch_ms burst_end_epoch_ms burst_wall_ms tps
```

Python JSON encoder와 positional argument로 JSON payload를 구성한다. JSON 또는 CSV object를 수작업으로 보간하지 않는다. temporary TSV plan file을 사용하고 Bash 3.2로 `schedule_version`, `schedule_cycle`, `schedule_row`, `measured_round`, `position_in_round`, `treatment_id`, `strategy`, `contention`, `capacity`, `contenders` 열 10개를 읽는다. header를 소비한 뒤 loop에서 `while IFS=$'\t' read -r schedule_version schedule_cycle schedule_row measured_round position_in_round treatment_id strategy contention capacity contenders; do`를 사용한다.

각 Gatling command 전에 기존 `build/reports/gatling/*` directory 집합을 snapshot한다. exit가 0이면 post-run max slot을 먼저 query한다. query 실패는 `db_read_failed`, 새 slot 없음은 `seed_failed`이다. 유효한 `slot_id`가 있을 때만 runner는 `simulation.log`와 `js/stats.js`를 모두 포함하는 새 directory가 정확히 하나 있기를 요구하고, 해당 directory를 parse에 사용한다. stale report가 이후 round와 같은 treatment label을 가질 수 있으므로 timestamp로 가장 최신 directory를 선택하지 않는다. 0이 아닌 Gatling exit는 `gatling_failed`이다. slot 성공 후 새 directory가 0개 또는 여러 개이거나 report file이 누락되거나 새 file이 손상/불일치하면 `parse_failed`이다.

- [ ] 의도적으로 접근할 수 없는 `BASE_URL`과 누락된 `docker`/`gradlew` shim을 사용한 `--print-plan` 테스트를 추가한다. 성공, 10 round의 TSV 101줄, 결정적 byte, campaign directory 없음에 대해 단언한다.
- [ ] 누락되거나 유효하지 않은 캠페인 ID, 알 수 없는 phase, round 0/9/11, Phase A 재사용, complete이고 round가 일치하는 A가 없는 Phase B가 파일 생성 전에 실패함을 증명하는 option test를 추가한다. `--print-plan --rounds 20`이 TSV 201줄로 성공함을 증명한다.
- [ ] temporary `PATH`에 `curl`, `docker` command shim을 만들고 `BENCHMARK_GRADLEW`은 absolute Gradle shim으로 설정한다. Gatling이 호출되지 않고 CSV가 없으며 manifest/stderr에 실패가 기록됨을 단언하는 Phase A environment-mismatch test를 추가한다.
- [ ] complete Phase A CSV byte를 snapshot한 뒤 B append 없음, promotion 없음, byte 불변, 0이 아닌 exit, manifest/stderr evidence를 단언하는 Phase B environment/warmup-failure test를 추가한다. CSV가 없음을 단언하는 동등한 Phase A warmup test도 추가한다.
- [ ] 이전 premeasurement failure에서 environment/manifest가 남은 Phase A ID는 CSV가 없어도 재사용할 수 없음을 증명하는 preflight test를 추가한다.
- [ ] 실패한 premeasurement run의 기존 Phase B environment file이나 manifest attempt가 있으면 새 campaign ID를 강제하고 Phase A byte는 건드리지 않음을 증명하는 Phase B reuse test를 추가한다.
- [ ] stale same-label report를 남겨 둔 채 현재 호출에서 새 report 하나를 만들고 새 directory만 parse함을 증명하는 report-selection test를 추가한다. 새 directory 0개와 2개 거부 사례도 추가한다.
- [ ] no-slot/no-new-report 결합 사례를 추가하고 slot validation이 report validation보다 앞서므로 `seed_failed`가 우선하는지 단언한다. 모든 measurement field가 비어 있는 `db_read_failed`를 단언하는 post-run slot-query failure 사례도 추가한다.
- [ ] 잘못된 캠페인, 불완전한 A, 실패 행, 잘못된 A 환경 metadata를 다루는 Phase B 사전 검사 테스트를 추가한다. 환경 요청이나 Gatling 호출이 없음을 단언한다.
- [ ] 재시도 통계 초기화, 실행 전 slot 조회, seed, Gatling, report parsing, 원자적 3열 불변식 조회/parser, 재시도 snapshot parsing을 둘러싼 단일 항목 통제 실패 행렬을 추가한다. 정확히 한 행, 올바른 `run_status`, 작업 3에서 허용한 형태 중 하나, 0이 아닌 종료 코드, 후속 처리 없음에 대해 단언한다.
- [ ] 첫 행 `--create`와 후속 행 `--append`의 serialization/write failure test를 추가한다. manifest/stderr evidence, writer에 관한 failure row를 쓰려는 재귀적 시도 없음, 다음 treatment나 promotion 없음, create 실패 시 destination 없음, append 실패 시 이전 CSV가 바이트 단위로 동일함을 단언한다.
- [ ] shim을 사용한 happy-path Phase A + B run을 추가해 warmup 호출 20회, measured 호출 200회, Phase B에서 다섯 전략을 모두 재실행, 각 저장 environment JSON의 SHA-256과 해당 phase의 모든 행·manifest hash 일치, snapshot의 cap이 각각 5/20, app-start ID 일치, validation 성공, promotion 1회를 단언한다.
- [ ] 400행을 검증하고 `complete_noncanonical`을 기록한 뒤 성공을 반환하며 promoter를 호출하거나 기존 canonical file을 변경하지 않는 shim 기반 20-round complete 캠페인을 추가한다.
- [ ] `declare -A`, `mapfile`, `readarray`, `jq`, 예전 고정 중첩 반복문, shell CSV `printf`, v1 정식 경로 참조를 거부하는 소스 검사 테스트를 추가한다.
- [ ] `--canonical-out docs/benchmark/raw-runs.csv`와 v1 canonical file로 resolve되는 모든 path가 캠페인을 만들기 전에 거부됨을 증명하는 path-guard test를 추가한다.
- [ ] 구현 전에 `python3 scripts/tests/test_benchmark_script.py -v`를 실행하고 예상된 실패를 보존한다.
- [ ] 스크립트를 CLI/사전 검사, 계획 streaming, 환경/manifest, warmup, 측정 실행 하나, 타입 지정 실패 행, phase 검증/승격 순서의 작은 단계로 refactor한다. 각 단계 뒤에 관련 테스트 메서드를 다시 실행한다.
- [ ] `bash -n scripts/benchmark.sh`, 전체 실행기·스케줄러·환경·행 작성기·캠페인 검증기 테스트를 실행한다.
- [ ] 전역 제약에 있는 v1 해시 3개를 다시 계산해 변경이 없음을 단언한다.
- [ ] diff를 검토한다. 커밋 초안 `feat: run counterbalanced benchmark campaigns`를 제시하고, 사용자가 명시적으로 승인한 뒤에만 커밋한다.

### 작업 7: 검증된 v2 입력 요구 및 정확한 지표 단위 보고

**파일:**

- 수정: `scripts/summarize_benchmark.py`
- 수정: `scripts/tests/test_summarize_benchmark.py`

**CLI 계약:**

```text
python3 scripts/summarize_benchmark.py docs/benchmark/raw-runs-v2.csv
python3 scripts/summarize_benchmark.py merged-diagnostic.csv --campaign-id ID
python3 scripts/summarize_benchmark.py docs/benchmark/raw-runs.csv --legacy-v1
```

기본 mode는 v2이며 stdout을 생성하기 전에 `validate_campaign(input_path, requirement="complete")`를 호출하고, 완전하면서 10의 양의 배수인 모든 round 수를 허용한다. 수동으로 결합한 diagnostic file에 여러 campaign이 있으면 `--campaign-id`를 요구하고, 정확히 해당 campaign만 in-memory row로 filter한 뒤 `validate_rows(selected_rows, requirement="complete", campaign_id=args.campaign_id)`를 호출한다. `--legacy-v1`은 현재 25-field rendering을 유지하고 v2 header를 거부한다. v2 mode는 v1 header를 거부한다. 한 table에 v1과 v2를 병합하지 않는다.

v2 output은 다섯 전략과 두 contention point를 모두 포함하는 별도의 Phase A 및 Phase B section을 갖는다. 각 aggregate cell은 다음 label과 값을 표시한다.

- 실행별 `mean_ms`에서 계산한 `실행별 평균 응답의 중앙값 (min–max)`
- 실행별 `p95_ms`에서 계산한 `실행별 p95의 중앙값 (min–max)`
- 타임스탬프 기반의 실행별 `tps`에서 계산한 `버스트 TPS의 중앙값 (min–max)`

②/④ pair table은 `(campaign_id, phase, measured_round, contention)`에만 join하고 두 `position_in_round` 값을 모두 출력한다. 두 실행이 같은 machine state를 공유했다고 주장하면 안 된다. optimistic cap 5와 cap 20은 각자의 phase table에 표시하며, 두 값의 between-app 비교는 paired comparison이 아니라 sensitivity analysis라고 표기한다.

각 phase와 contention point별로 Mermaid mean-response chart를 분리해 렌더링한다. 서로 다른 app start의 값을 하나의 bar group에 넣지 않는다. 방법론 v1 optimistic attempt-distribution JSON은 명시적으로 historical v1이라고 표시한 section 안에서만 보여줄 수 있다. v2 output에서 이를 v2 campaign artifact로 제시하면 안 된다.

- [ ] 기존 tie test를 `--legacy-v1` 아래에서 보존하고, 예전 25-field sample이 계속 같은 score를 렌더링하는지 golden assertion을 추가한다.
- [ ] 분리된 phase section, phase별 다섯 전략 모두, 세 metric range 모두, pair position, 예전 “거의 같은 머신 상태” 주장 부재를 검증하는 v2 test를 추가한다.
- [ ] incomplete/failed/unbalanced campaign, `--legacy-v1` 없는 v1, `--legacy-v1`을 사용한 v2, 선택하지 않은 multiple campaign, 알 수 없는 selected campaign에 대한 failure test를 추가한다.
- [ ] `--campaign-id`가 값을 섞지 않고 campaign 하나를 선택함을 증명하는 two-campaign diagnostic test를 추가한다.
- [ ] 더 긴 complete campaign도 요약을 공개할 수 있지만 canonical promotion을 뜻하지는 않음을 증명하는 유효한 20-round v2 summary test를 추가한다.
- [ ] `python3 scripts/tests/test_summarize_benchmark.py -v`를 실행해 구현 전에 v2 test가 실패하는지 확인한다.
- [ ] argument parsing을 refactor하고 v1 rendering을 named function으로 유지한다. validation에 성공한 뒤에만 v2 rendering을 추가한다.
- [ ] summarizer test와 validator test를 실행한다. `docs/benchmark/raw-runs.csv`에 legacy mode를 실행하고 비교용 output은 `/tmp`에만 저장한다. v1 artifact는 수정하지 않는다.
- [ ] diff를 검토한다. 커밋 초안 `feat: summarize benchmark methodology v2`를 제시하고, 사용자가 명시적으로 승인한 뒤에만 커밋한다.

### 작업 8: 증거 수집 전 측정 시스템 검증

**파일:**

- production file 변경은 예상하지 않는다.
- 이 검사에서 발견한 실패 구현/테스트만 해당 파일을 소유한 작업 범위 안에서 수정한다.

- [ ] 모든 script test를 실행한다. `./gradlew benchmarkScriptTest --console=plain`
- [ ] Java test와 static lifecycle check를 실행한다. `./gradlew test --console=plain`
- [ ] 깨끗한 Gradle 작업 실행으로 전체 프로젝트 검사를 수행한다. `./gradlew check --rerun-tasks --console=plain`
- [ ] `bash -n scripts/benchmark.sh`와 `python3 -m py_compile scripts/*.py`를 실행한다.
- [ ] `--print-plan`으로 Phase A와 B plan을 각각 두 번 생성하고 byte를 비교하며 phase당 100행인지 독립적으로 센다.
- [ ] 의도적으로 incomplete, failed, unbalanced하게 만든 temporary fixture에 `python3 scripts/validate_benchmark_campaign.py`를 실행해 세 경우 모두 summary output이나 promotion 없이 실패하는지 확인한다.
- [ ] 전역 제약의 v1 SHA-256 값 3개를 모두 다시 계산해 비교한다.
- [ ] `git diff --check`, `git status --short`, `git diff --stat`을 검사한다. `AGENTS.md`는 관련 없는 untracked 상태로 남아 있어야 한다.
- [ ] 검사가 하나라도 실패하면 검증기나 테스트를 약화하지 말고 해당 파일을 소유한 작업의 red-green 반복으로 돌아간다. 변경이 없는 검증 작업에는 커밋이 없다.

### 작업 9: 균형 잡힌 200행 캠페인 실행 및 공개

**파일:**

- 생성: `docs/benchmark/campaigns/2026-09-24-williams-v2/environment-phase-a.json`
- 생성: `docs/benchmark/campaigns/2026-09-24-williams-v2/environment-phase-b.json`
- 생성: `docs/benchmark/campaigns/2026-09-24-williams-v2/manifest.md`
- 생성: `docs/benchmark/campaigns/2026-09-24-williams-v2/raw-runs.csv`
- 생성: `docs/benchmark/raw-runs-v2.csv`

해당 directory에 측정 state가 하나라도 이미 있으면 새 campaign ID를 사용한다. 실패한 campaign을 삭제하거나 재사용하지 않는다.

- [ ] Docker를 사용할 수 있는지 확인한 뒤 `docker compose up -d`를 실행하고 기존 data를 삭제하지 않은 채 MySQL/Redis health를 검증한다.
- [ ] 실행 전 Git status와 v1 hash 3개를 캡처한다. 예상하지 못한 dirty implementation worktree에서는 측정을 거부한다. 관련 없는 untracked `AGENTS.md`는 허용한다.
- [ ] 전용 terminal/session에서 `./gradlew bootRun --args='--spring.profiles.active=benchmark'`로 Phase A를 시작하고 애플리케이션이 준비될 때까지 기다린다.
- [ ] `scripts/benchmark.sh --campaign-id 2026-09-24-williams-v2 --phase a --rounds 10`을 실행한다. 명령 완료와 100행 Phase A validator 통과 후 Phase A 애플리케이션을 중지한다.
- [ ] `./gradlew bootRun --args='--spring.profiles.active=benchmark --reservation.optimistic.max-attempts=20'`으로 새 Phase B 애플리케이션을 시작하고 준비될 때까지 기다린다.
- [ ] `scripts/benchmark.sh --campaign-id 2026-09-24-williams-v2 --phase b --rounds 10`을 실행한다. complete validation과 canonical promotion 후 Phase B 애플리케이션을 중지한다.
- [ ] campaign CSV가 header를 포함한 physical line 201개, 정확히 A `ok` 100행과 B `ok` 100행, round마다 서로 다른 treatment 10개, phase마다 environment hash 하나, phase별 cap 5/20, 서로 다른 양수 slot ID 200개, failure row 0개인지 검증한다.
- [ ] 각 environment JSON의 hash를 계산하고 해당 phase의 모든 행 및 대응하는 manifest entry와 비교한다. 두 `app_start_id` 값과 기록해야 하는 모든 precondition이 있는지 확인한다.
- [ ] `cmp`에서 campaign CSV와 `docs/benchmark/raw-runs-v2.csv`가 바이트 단위로 동일하다고 보고하는지 검증한다. canonical file에 complete validator와 summarizer를 다시 실행한다.
- [ ] 200행 모두에 대해 `tps = requests * 1000 / (burst_end_epoch_ms - burst_start_epoch_ms)`를 독립적으로 다시 계산하고 저장된 precision으로 비교한다.
- [ ] v1 hash 3개를 모두 다시 계산해 변경되지 않았는지 확인한다.
- [ ] staging 전에 manifest와 환경 파일에 secret, 특정 머신의 절대 home 경로, 원시 access token, credential이 있는지 검사한다.
- [ ] 행 수, 검증기 출력, 해시, staging한 전체 산출물 diff를 제시한다. 커밋 초안 `test: record balanced 200-run benchmark campaign`을 제시하고, 사용자가 명시적으로 승인한 뒤에만 커밋한다.

### 작업 10: 공개 결론을 v2 기반 주장으로 교체

**파일:**

- 수정: `docs/STEP2-DEFENSE-BENCHMARK.md`
- 수정: `README.md`
- 수정: `PROJECT_PLAN.md`
- 수정: `docs/STEP2-3-BRANCH-STRATEGY.md`
- 수정: `docs/STEP2-CONDITIONAL-UPDATE.md`
- 수정: `docs/STEP2-PESSIMISTIC-LOCK.md`
- 수정: `docs/STEP2-OPTIMISTIC-LOCK.md`
- 수정: `src/main/java/com/interview/reservation/service/strategy/OptimisticLockReservationStrategy.java`
- 대체된 benchmark number나 fixed-order conclusion을 인용하는 경우에만 수정: `src/main/java/com/interview/reservation/service/strategy/PessimisticLockReservationStrategy.java`

- [ ] complete v2 summary를 temporary file로 생성하고 current table과 prose의 유일한 numeric source로 삼는다. terminal memory에서 옮겨 적지 않는다.
- [ ] 벤치마크 문서의 방법론 절을 업데이트한다. 10개 원자적 처리, Williams 균형 범위, 두 번의 애플리케이션 시작, 전체 warmup 행, phase 내부 비교, 환경 해시, 실제 버스트 TPS, 처리/phase당 10회 실행, 공개 검증을 포함한다.
- [ ] 나열한 docs/comment에서 current benchmark라고 주장하는 부분의 현재 v1 number와 conclusion만 교체한다. historical preliminary/v1 section은 보존하고 `methodology-v1-fixed-order`라고 label한 뒤 변경 불가한 2026-09-24 v1 manifest를 연결한다.
- [ ] mean, p95, TPS cell에 정확한 per-run aggregation unit과 range를 표시한다. OK/KO와 data-integrity caveat가 보이게 유지한다. KO가 많은 throughput을 successful work throughput이라고 부르지 않는다.
- [ ] 검증한 phase-local pair를 바탕으로 ②/④ discussion을 업데이트하고 position을 포함한다. 관측한 차이를 오직 locking 때문이라고 돌리지 않는다.
- [ ] optimistic cap 5/20 discussion을 between-app sensitivity analysis로 업데이트한다. 각 cap을 same-phase control과 비교하고 cap 5 대 cap 20을 same-process pair라고 설명하지 않는다.
- [ ] 최종 architectural choice의 근거를 보편적인 성능 우위 주장이 아니라 single-row conditional transition에 둔다. Redisson과 별도의 idempotency key를 생략한 문서화된 이유를 보존한다.
- [ ] README, plan, branch-strategy progress row에서 `raw-runs-v2.csv`, campaign manifest, environment snapshot 두 개, methodology-v1 archive로 가는 link를 추가한다.
- [ ] Markdown과 Java comment 전체에서 대체된 모든 numeric claim과 fixed-order/current-canonical phrase를 `rg`로 찾고, 각 match를 업데이트한 current evidence 또는 의도적으로 label한 history로 분류한다.
- [ ] summarizer를 다시 실행하고 모든 current table number를 generated output과 기계적으로 비교한다.
- [ ] `./gradlew check --rerun-tasks --console=plain`, `git diff --check`, 완전한 캠페인 검증기, v1 해시 검사 3개를 실행한다.
- [ ] 측정 타당성, 산출물 추적성, 의도하지 않은 전략 변경에 초점을 둔 최종 명세-대-diff 검토에 `superpowers:requesting-code-review`를 사용한다. 조치 가능한 발견 사항을 모두 해결하고 담당 검사를 다시 실행한다.
- [ ] 최종 verification evidence와 staged diff를 제시한다. 커밋 초안 `docs: update benchmark conclusions from v2 campaign`을 제시하고, 사용자가 명시적으로 승인한 뒤에만 커밋한다.

## 완료 기준

다음 조건을 모두 동시에 충족해야 작업이 complete이다.

- 스케줄러, 바이너리 파서, 행 작성기, 환경 보존, 캠페인 검증기/승격기, 실행기, 요약기 테스트가 `./gradlew check --rerun-tasks`에서 통과한다.
- check-in한 campaign에 성공한 measured row가 정확히 200개 있고 warmup row는 없으며, 두 phase 모두 모든 Williams 불변식을 통과한다.
- 저장한 모든 TPS를 저장된 request start/end timestamp에서 다시 계산할 수 있고, binary-log count와 `stats.js` count 양쪽에 일치한다.
- environment snapshot과 hash가 모든 행을 manifest에 연결하고 Phase A의 cap 5와 Phase B의 cap 20을 증명한다.
- incomplete, failed, mixed, unbalanced campaign은 요약하거나 승격할 수 없고 canonical v2 destination을 덮어쓸 수 없다.
- v1 artifact hash 3개가 모두 변경되지 않는다.
- current documentation을 v2 evidence에서 다시 생성하고, historical v1 evidence는 label과 link를 유지하며, 어떤 reservation strategy 동작도 변경하지 않는다.
