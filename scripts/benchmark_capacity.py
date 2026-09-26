#!/usr/bin/env python3
"""Run and validate the methodology-v2 capacity-contention benchmark."""

import argparse
import csv
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


SCHEDULE_VERSION = "williams-10-v1"
WILLIAMS_BASE = (0, 1, 9, 2, 8, 3, 7, 4, 6, 5)
BURST_SENTINEL = "BENCHMARK_BURST_METRICS="
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080")
DEFAULT_MYSQL_CONTAINER = os.environ.get("MYSQL_CONTAINER", "reservation-mysql")
DEFAULT_GRADLEW = os.environ.get("BENCHMARK_GRADLEW", "./gradlew")
GATLING_REPORT_ROOT = PROJECT_ROOT / "build" / "reports" / "gatling"
SIMULATION_CLASS = "com.interview.reservation.loadtest.BaselineReservationSimulation"
BARE_STATS_KEY = re.compile(
    r"^(\s*)(type|name|path|pathFormatted|stats|contents):", re.MULTILINE
)

CSV_FIELDS = [
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
    """The benchmark profile is valid, but Hikari has not filled its pool."""


TREATMENTS = (
    Treatment(0, "baseline", "low", 100, 120),
    Treatment(1, "unique", "low", 100, 120),
    Treatment(2, "conditional", "low", 100, 120),
    Treatment(3, "pessimistic", "low", 100, 120),
    Treatment(4, "optimistic", "low", 100, 120),
    Treatment(5, "baseline", "extreme", 1, 200),
    Treatment(6, "unique", "extreme", 1, 200),
    Treatment(7, "conditional", "extreme", 1, 200),
    Treatment(8, "pessimistic", "extreme", 1, 200),
    Treatment(9, "optimistic", "extreme", 1, 200),
)

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


def build_plan(rounds: int) -> list[PlanEntry]:
    """Return a deterministic Williams order for complete ten-round blocks."""
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds <= 0 or rounds % 10:
        raise ValueError("rounds must be a positive multiple of 10")

    plan = []
    for measured_round in range(1, rounds + 1):
        schedule_row = (measured_round - 1) % 10
        order = ((treatment_id + schedule_row) % 10 for treatment_id in WILLIAMS_BASE)
        for position, treatment_id in enumerate(order, start=1):
            plan.append(PlanEntry(measured_round, position, TREATMENTS[treatment_id]))
    return plan


def parse_burst_sentinel(text: str, expected_requests: int) -> BurstMetrics:
    """Parse one complete Gatling burst sentinel and derive its TPS."""
    sentinel_lines = [
        line for line in text.splitlines() if line.startswith(BURST_SENTINEL)
    ]
    if len(sentinel_lines) != 1:
        raise ValueError("expected exactly one benchmark burst sentinel")

    try:
        payload = json.loads(sentinel_lines[0][len(BURST_SENTINEL):])
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError("invalid benchmark burst sentinel JSON") from error
    expected_keys = {"requests", "startEpochMs", "endEpochMs", "wallMs"}
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise ValueError("benchmark burst sentinel must contain the exact metric keys")
    if any(type(payload[key]) is not int or payload[key] < 0 for key in expected_keys):
        raise ValueError("benchmark burst metrics must be non-negative integers")

    requests = payload["requests"]
    start = payload["startEpochMs"]
    end = payload["endEpochMs"]
    wall = payload["wallMs"]
    if requests != expected_requests:
        raise ValueError("benchmark burst request count does not match contenders")
    if wall <= 0 or wall != end - start:
        raise ValueError("benchmark burst wall time is inconsistent")
    return BurstMetrics(requests, start, end, wall, round(requests * 1000 / wall, 1))


def validate_environment(payload: object, expected_max_attempts: int) -> dict:
    """Return a benchmark environment only when its controlled settings match."""
    if not isinstance(payload, dict):
        raise ValueError("benchmark environment must be an object")

    expected = dict(EXPECTED_ENVIRONMENT)
    expected["optimisticMaxAttempts"] = expected_max_attempts
    mismatches = [
        key for key, value in expected.items()
        if key != "totalConnections" and payload.get(key) != value
    ]
    if mismatches:
        raise ValueError(
            "benchmark environment mismatch: " + ", ".join(sorted(mismatches))
        )
    total_connections = payload.get("totalConnections")
    if type(total_connections) is int and total_connections < 100:
        raise EnvironmentNotReady(
            "benchmark pool is not ready: totalConnections must be 100"
        )
    if total_connections != 100:
        raise ValueError("benchmark environment mismatch: totalConnections")
    return payload


def _non_negative_integer(value: object) -> bool:
    return type(value) is int and value >= 0


def parse_retry_snapshot(payload: object) -> RetryMetrics:
    """Validate an optimistic-retry snapshot and return its CSV metrics."""
    if not isinstance(payload, dict):
        raise ValueError("optimistic retry snapshot must be an object")
    required = {
        "succeededByAttempts", "succeeded", "retryExhausted",
        "versionConflicts", "deadlocks", "meanAttemptsPerSuccess",
    }
    missing = required - set(payload)
    if missing:
        raise ValueError("missing optimistic retry metrics: " + ", ".join(sorted(missing)))

    count_keys = ("succeeded", "retryExhausted", "versionConflicts", "deadlocks")
    if any(not _non_negative_integer(payload[key]) for key in count_keys):
        raise ValueError("optimistic retry counts must be non-negative integers")
    mean_attempts = payload["meanAttemptsPerSuccess"]
    if (
        isinstance(mean_attempts, bool)
        or not isinstance(mean_attempts, (int, float))
        or not math.isfinite(mean_attempts)
        or mean_attempts < 0
    ):
        raise ValueError("meanAttemptsPerSuccess must be a finite non-negative number")

    distribution = payload["succeededByAttempts"]
    if not isinstance(distribution, dict):
        raise ValueError("succeededByAttempts must be an object")
    total = 0
    weighted = 0
    for attempts_text, count in distribution.items():
        try:
            attempts = int(attempts_text)
        except (TypeError, ValueError):
            attempts = 0
        if str(attempts) != attempts_text or attempts < 1 or not _non_negative_integer(count):
            raise ValueError(
                "attempt buckets must be positive integer strings with non-negative counts"
            )
        total += count
        weighted += attempts * count
    if total != payload["succeeded"]:
        raise ValueError("retry distribution total does not match succeeded")
    expected_mean = 0 if total == 0 else weighted / total
    if not math.isclose(float(mean_attempts), expected_mean, abs_tol=0.011):
        raise ValueError("meanAttemptsPerSuccess does not match retry distribution")

    return RetryMetrics(
        payload["succeeded"],
        payload["retryExhausted"],
        payload["versionConflicts"],
        payload["deadlocks"],
        float(mean_attempts),
    )


def _load_stats_object(report_dir: Path) -> dict:
    source = (report_dir / "js" / "stats.js").read_text(encoding="utf-8")
    function_start = source.index("function fillStats")
    document = source[source.index("{"):source.rindex("}", 0, function_start) + 1]
    payload = json.loads(BARE_STATS_KEY.sub(r'\1"\2":', document))
    if not isinstance(payload, dict):
        raise ValueError("Gatling stats root must be an object")
    return payload


def load_report_stats(
    report_dir: Path,
    entry: PlanEntry,
    gatling_output: str,
) -> ReportMetrics:
    """Read the labelled Gatling request and cross-check its burst sentinel."""
    treatment = entry.treatment
    label = "reserve [%s cap=%d cont=%d]" % (
        treatment.strategy,
        treatment.capacity,
        treatment.contenders,
    )
    root = _load_stats_object(report_dir)
    contents = root.get("contents")
    if not isinstance(contents, dict):
        raise ValueError("Gatling stats contents must be an object")
    matches = [
        item["stats"]
        for item in contents.values()
        if isinstance(item, dict)
        and isinstance(item.get("stats"), dict)
        and item["stats"].get("name") == label
    ]
    if len(matches) != 1:
        raise ValueError("expected exactly one Gatling request named %s" % label)

    stats = matches[0]
    try:
        requests = int(stats["numberOfRequests"]["total"])
        ok = int(stats["numberOfRequests"]["ok"])
        ko = int(stats["numberOfRequests"]["ko"])
        mean_ms = int(stats["meanResponseTime"]["total"])
        p95_ms = int(stats["percentiles3"]["total"])
        max_ms = int(stats["maxResponseTime"]["total"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid Gatling request statistics") from error
    if min(requests, ok, ko, mean_ms, p95_ms, max_ms) < 0 or requests != ok + ko:
        raise ValueError("invalid Gatling request counts or response times")

    burst = parse_burst_sentinel(gatling_output, treatment.contenders)
    if requests != burst.requests:
        raise ValueError("stats.js request count does not match burst sentinel")
    return ReportMetrics(requests, ok, ko, mean_ms, p95_ms, max_ms, burst)


def fetch_json(url: str, method: str = "GET") -> object:
    request = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _one_line(value: object) -> str:
    return " ".join(str(value).replace("\r", " ").replace("\n", " ").split())


def run_command(arguments: list[str], cwd: Path) -> str:
    result = subprocess.run(
        arguments,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        output_lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        detail = output_lines[-1] if output_lines else "no command output"
        raise RuntimeError(
            "command failed: %s: %s"
            % (_one_line(" ".join(arguments)), _one_line(detail))
        )
    return result.stdout


def mysql_query(container: str, sql: str) -> str:
    return run_command(
        [
            "docker", "exec", container, "mysql", "-uroot", "-N", "-B",
            "-e", sql, "reservation",
        ],
        PROJECT_ROOT,
    ).strip()


def snapshot_report_names(root: Path) -> set[str]:
    return {path.name for path in root.iterdir() if path.is_dir()}


def select_new_report(root: Path, before: set[str]) -> Path:
    created = [
        path for path in root.iterdir()
        if path.is_dir() and path.name not in before
    ]
    if len(created) != 1 or not (created[0] / "js" / "stats.js").is_file():
        raise RuntimeError("expected exactly one complete Gatling report")
    return created[0]


def environment_path(output: Path, phase: str) -> Path:
    if phase not in ("a", "b"):
        raise ValueError("phase must be a or b")
    return Path(str(output) + ".phase-%s-environment.json" % phase)


def capture_environment(
    output: Path,
    phase: str,
    base_url: str,
    expected_max_attempts: int,
    *,
    wait_seconds: int,
) -> str:
    """Validate, exclusively save, and hash one Phase environment snapshot."""
    destination = environment_path(output, phase)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("environment snapshot already exists: %s" % destination)
    if isinstance(wait_seconds, bool) or wait_seconds < 0:
        raise ValueError("ENVIRONMENT_WAIT_SECONDS must be a non-negative integer")

    waited = 0
    while True:
        payload = fetch_json(base_url + "/api/metrics/benchmark-environment")
        try:
            environment = validate_environment(payload, expected_max_attempts)
            break
        except EnvironmentNotReady:
            if waited >= wait_seconds:
                raise
            waited += 1
            time.sleep(1)

    canonical = (
        json.dumps(
            environment,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    with destination.open("xb") as handle:
        handle.write(canonical)
    return hashlib.sha256(canonical).hexdigest()


def request_no_content(url: str, method: str = "DELETE") -> None:
    request = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(request, timeout=5) as response:
        response.read()


def _query_max_slot(container: str = DEFAULT_MYSQL_CONTAINER) -> int:
    value = mysql_query(
        container,
        "SELECT COALESCE(MAX(id), 0) FROM interview_slot",
    )
    try:
        slot_id = int(value)
    except ValueError as error:
        raise RuntimeError("maximum slot query did not return an integer") from error
    if slot_id < 0 or str(slot_id) != value:
        raise RuntimeError("maximum slot query did not return a non-negative integer")
    return slot_id


def _gatling_arguments(
    entry: PlanEntry,
    base_url: str = DEFAULT_BASE_URL,
    gradlew: str = DEFAULT_GRADLEW,
) -> list[str]:
    treatment = entry.treatment
    return [
        gradlew,
        "gatlingRun",
        "--console=plain",
        "-q",
        "--simulation",
        SIMULATION_CLASS,
        "-DbaseUrl=%s" % base_url,
        "-Dstrategy=%s" % treatment.strategy,
        "-Dcapacity=%d" % treatment.capacity,
        "-Dcontenders=%d" % treatment.contenders,
    ]


def run_warmup_entry(entry: PlanEntry) -> None:
    request_no_content(DEFAULT_BASE_URL + "/api/metrics/optimistic-retries")
    before_slot = _query_max_slot()
    GATLING_REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    before_reports = snapshot_report_names(GATLING_REPORT_ROOT)
    run_command(_gatling_arguments(entry), PROJECT_ROOT)
    after_slot = _query_max_slot()
    if after_slot <= before_slot:
        raise RuntimeError("warmup did not create a new slot")
    select_new_report(GATLING_REPORT_ROOT, before_reports)


def warm_up(plan: list[PlanEntry]) -> None:
    first_round = [entry for entry in plan if entry.round == 1]
    if len(first_round) != len(TREATMENTS):
        raise ValueError("benchmark plan first round must contain ten treatments")
    for entry in first_round:
        run_warmup_entry(entry)


def _read_invariants(slot_id: int, container: str = DEFAULT_MYSQL_CONTAINER) -> tuple[int, int, int]:
    sql = (
        "/* benchmark invariant tuple */ "
        "SELECT s.remaining, "
        "(SELECT COUNT(*) FROM reservation r "
        "WHERE r.slot_id = s.id AND r.status = 'CONFIRMED'), "
        "(SELECT COALESCE(SUM(d.c - 1), 0) FROM "
        "(SELECT COUNT(*) AS c FROM reservation WHERE slot_id = %d "
        "GROUP BY applicant_id, slot_id HAVING COUNT(*) > 1) d) "
        "FROM interview_slot s WHERE s.id = %d" % (slot_id, slot_id)
    )
    output = mysql_query(container, sql)
    if "\n" in output or "\r" in output:
        raise RuntimeError("database invariant query returned multiple lines")
    fields = output.split("\t")
    if len(fields) != 3:
        raise RuntimeError("database invariant query must return three fields")
    try:
        remaining, confirmed, duplicates = (int(value) for value in fields)
    except ValueError as error:
        raise RuntimeError("database invariant query returned non-integer fields") from error
    if str(remaining) != fields[0] or any(
        value < 0 or str(value) != source
        for value, source in ((confirmed, fields[1]), (duplicates, fields[2]))
    ):
        raise RuntimeError("database invariant query returned invalid counts")
    return remaining, confirmed, duplicates


def measure_entry(
    entry: PlanEntry,
    *,
    campaign_id: str,
    phase: str,
    environment_sha256: str,
) -> dict[str, str]:
    """Run one treatment and build a successful 39-field CSV row."""
    if phase not in ("a", "b"):
        raise ValueError("phase must be a or b")
    request_no_content(DEFAULT_BASE_URL + "/api/metrics/optimistic-retries")
    before_slot = _query_max_slot()
    GATLING_REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    before_reports = snapshot_report_names(GATLING_REPORT_ROOT)
    gatling_output = run_command(_gatling_arguments(entry), PROJECT_ROOT)
    after_slot = _query_max_slot()
    if after_slot <= before_slot:
        raise RuntimeError("Gatling did not create a new slot")
    report_dir = select_new_report(GATLING_REPORT_ROOT, before_reports)
    report = load_report_stats(report_dir, entry, gatling_output)
    remaining, confirmed, duplicates = _read_invariants(after_slot)
    retry = parse_retry_snapshot(
        fetch_json(DEFAULT_BASE_URL + "/api/metrics/optimistic-retries")
    )

    treatment = entry.treatment
    retry_cap = 5 if phase == "a" else 20
    row = {
        "schema_version": "2",
        "campaign_id": campaign_id,
        "phase": phase,
        "app_start_id": "%s-phase-%s" % (campaign_id, phase),
        "environment_sha256": environment_sha256,
        "schedule_version": SCHEDULE_VERSION,
        "schedule_cycle": str((entry.round - 1) // 10 + 1),
        "schedule_row": str((entry.round - 1) % 10 + 1),
        "measured_round": str(entry.round),
        "position_in_round": str(entry.position),
        "treatment_id": str(treatment.id),
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "strategy": treatment.strategy,
        "optimistic_max_attempts": str(retry_cap),
        "contention": treatment.contention,
        "capacity": str(treatment.capacity),
        "contenders": str(treatment.contenders),
        "slot_id": str(after_slot),
        "requests": str(report.requests),
        "ok": str(report.ok),
        "ko": str(report.ko),
        "mean_ms": str(report.mean_ms),
        "p95_ms": str(report.p95_ms),
        "max_ms": str(report.max_ms),
        "burst_start_epoch_ms": str(report.burst.start_epoch_ms),
        "burst_end_epoch_ms": str(report.burst.end_epoch_ms),
        "burst_wall_ms": str(report.burst.wall_ms),
        "tps": str(report.burst.tps),
        "remaining": str(remaining),
        "confirmed": str(confirmed),
        "overbooking": str(max(confirmed - treatment.capacity, 0)),
        "duplicates": str(duplicates),
        "retry_succeeded": str(retry.succeeded),
        "retry_exhausted": str(retry.exhausted),
        "version_conflicts": str(retry.version_conflicts),
        "deadlocks": str(retry.deadlocks),
        "mean_attempts": str(retry.mean_attempts),
        "run_status": "ok",
        "error_reason": "",
    }
    if list(row) != CSV_FIELDS:
        raise RuntimeError("internal benchmark row field order is invalid")
    return row


def _integer(row: dict[str, str], field: str, *, positive: bool = False) -> int:
    try:
        value = int(row[field])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("%s must be an integer" % field) from error
    if str(value) != row[field] or value < (1 if positive else 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError("%s must be a %s integer" % (field, qualifier))
    return value


def _validate_phase_rows(
    phase_rows: list[dict[str, str]],
    phase: str,
    expected_rounds: int,
) -> None:
    expected_cap = 5 if phase == "a" else 20
    environment_hashes = {row["environment_sha256"] for row in phase_rows}
    if len(environment_hashes) != 1 or not next(iter(environment_hashes)):
        raise ValueError("each phase must use exactly one environment_sha256")
    if any(_integer(row, "optimistic_max_attempts", positive=True) != expected_cap
           for row in phase_rows):
        raise ValueError("optimistic_max_attempts does not match phase")

    expected_plan = {
        (entry.round, entry.position): entry for entry in build_plan(expected_rounds)
    }
    actual_keys = []
    slot_ids = []
    for row in phase_rows:
        measured_round = _integer(row, "measured_round", positive=True)
        position = _integer(row, "position_in_round", positive=True)
        key = (measured_round, position)
        actual_keys.append(key)
        if key not in expected_plan:
            raise ValueError("round or position is outside the expected plan")

        expected_entry = expected_plan[key]
        treatment = expected_entry.treatment
        if _integer(row, "treatment_id") != treatment.id:
            raise ValueError("treatment_id does not match the Williams plan")
        actual_treatment = (
            row["strategy"],
            row["contention"],
            _integer(row, "capacity", positive=True),
            _integer(row, "contenders", positive=True),
        )
        expected_treatment = (
            treatment.strategy,
            treatment.contention,
            treatment.capacity,
            treatment.contenders,
        )
        if actual_treatment != expected_treatment:
            raise ValueError("strategy/contention/capacity/contenders mismatch")

        if row["schedule_version"] != SCHEDULE_VERSION:
            raise ValueError("schedule_version does not match")
        if _integer(row, "schedule_cycle", positive=True) != (measured_round - 1) // 10 + 1:
            raise ValueError("schedule_cycle does not match measured_round")
        if _integer(row, "schedule_row", positive=True) != (measured_round - 1) % 10 + 1:
            raise ValueError("schedule_row does not match measured_round")

        slot_ids.append(_integer(row, "slot_id", positive=True))
        _validate_measurement_equations(row, treatment)

    if Counter(actual_keys) != Counter(expected_plan.keys()):
        raise ValueError("each round must contain every treatment and position once")
    if len(slot_ids) != len(set(slot_ids)):
        raise ValueError("slot_id values must be unique")

    for block_start in range(1, expected_rounds + 1, 10):
        block = [
            row for row in phase_rows
            if block_start <= int(row["measured_round"]) < block_start + 10
        ]
        positions = Counter(
            (int(row["treatment_id"]), int(row["position_in_round"]))
            for row in block
        )
        if len(positions) != 100 or set(positions.values()) != {1}:
            raise ValueError("each ten-round block must balance treatment positions")


def _validate_measurement_equations(
    row: dict[str, str],
    treatment: Treatment,
) -> None:
    requests = _integer(row, "requests")
    ok = _integer(row, "ok")
    ko = _integer(row, "ko")
    if requests != treatment.contenders or requests != ok + ko:
        raise ValueError("requests must equal contenders and ok + ko")

    confirmed = _integer(row, "confirmed")
    overbooking = _integer(row, "overbooking")
    if overbooking != max(confirmed - treatment.capacity, 0):
        raise ValueError("overbooking does not match confirmed and capacity")

    start = _integer(row, "burst_start_epoch_ms")
    end = _integer(row, "burst_end_epoch_ms")
    wall = _integer(row, "burst_wall_ms", positive=True)
    if wall != end - start:
        raise ValueError("burst_wall_ms does not match start and end")
    try:
        tps = float(row["tps"])
    except (TypeError, ValueError) as error:
        raise ValueError("tps must be a number") from error
    expected_tps = round(requests * 1000 / wall, 1)
    if not math.isfinite(tps) or tps != expected_tps:
        raise ValueError("tps does not match requests and burst_wall_ms")

    for field in (
        "mean_ms", "p95_ms", "max_ms", "duplicates", "retry_succeeded",
        "retry_exhausted", "version_conflicts", "deadlocks",
    ):
        _integer(row, field)
    try:
        remaining = int(row["remaining"])
        mean_attempts = float(row["mean_attempts"])
    except (TypeError, ValueError) as error:
        raise ValueError("remaining and mean_attempts must be numeric") from error
    if str(remaining) != row["remaining"]:
        raise ValueError("remaining must be an integer")
    if not math.isfinite(mean_attempts) or mean_attempts < 0:
        raise ValueError("mean_attempts must be a finite non-negative number")


def validate_rows(
    rows: list[dict[str, str]],
    requirement: str,
    expected_rounds: int,
) -> None:
    """Reject any partial or internally inconsistent methodology-v2 campaign."""
    if requirement not in ("phase-a", "complete"):
        raise ValueError("requirement must be phase-a or complete")
    build_plan(expected_rounds)
    if not rows:
        raise ValueError("benchmark campaign is empty")
    for row in rows:
        if not isinstance(row, dict) or list(row.keys()) != CSV_FIELDS:
            raise ValueError("CSV row must contain the exact 39 fields in order")
        if any(not isinstance(value, str) for value in row.values()):
            raise ValueError("CSV row values must all be strings")

    campaign_ids = {row["campaign_id"] for row in rows}
    if len(campaign_ids) != 1 or not next(iter(campaign_ids)):
        raise ValueError("campaign must contain exactly one campaign_id")
    campaign_id = next(iter(campaign_ids))
    if any(row["schema_version"] != "2" for row in rows):
        raise ValueError("schema_version must be 2")
    if any(row["run_status"] != "ok" or row["error_reason"] != "" for row in rows):
        raise ValueError("complete campaigns may contain only successful rows")

    rows_per_phase = expected_rounds * len(TREATMENTS)
    expected_phases = ["a"] * rows_per_phase
    if requirement == "complete":
        expected_phases += ["b"] * rows_per_phase
    if [row["phase"] for row in rows] != expected_phases:
        raise ValueError("campaign does not contain the required complete phase rows")

    seen_slots = []
    for phase in (("a",) if requirement == "phase-a" else ("a", "b")):
        phase_rows = [row for row in rows if row["phase"] == phase]
        if any(row["app_start_id"] != "%s-phase-%s" % (campaign_id, phase)
               for row in phase_rows):
            raise ValueError("app_start_id does not match campaign and phase")
        _validate_phase_rows(phase_rows, phase, expected_rounds)
        seen_slots.extend(int(row["slot_id"]) for row in phase_rows)
    if len(seen_slots) != len(set(seen_slots)):
        raise ValueError("slot_id values must be unique across phases")


def read_rows(path: Path) -> list[dict[str, str]]:
    """Read one exact methodology-v2 CSV without normalizing its values."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        if reader.fieldnames != CSV_FIELDS:
            raise ValueError("CSV header does not match the exact 39 fields")
        rows = []
        for line_number, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError("malformed CSV row at line %d" % line_number)
            rows.append(row)
    return rows


def prepare_output(
    output: Path,
    phase: str,
    campaign_id: str,
    rounds: int,
) -> list[dict[str, str]]:
    """Enforce the create-only Phase A and validated append-only Phase B boundary."""
    build_plan(rounds)
    if phase == "a":
        if output.exists() or output.is_symlink():
            raise ValueError("output already exists: %s" % output)
        return []
    if phase != "b":
        raise ValueError("phase must be a or b")
    if not output.is_file():
        raise ValueError("Phase B requires a complete Phase A output")

    rows = read_rows(output)
    campaign_ids = {row["campaign_id"] for row in rows}
    if campaign_ids != {campaign_id}:
        raise ValueError("Phase A campaign does not match --campaign-id")
    measured_rounds = {
        int(row["measured_round"])
        for row in rows
        if row.get("measured_round", "").isdigit()
    }
    if measured_rounds and max(measured_rounds) != rounds:
        raise ValueError("Phase A round count does not match --rounds")
    try:
        validate_rows(rows, "phase-a", rounds)
    except ValueError as error:
        raise ValueError("Phase B requires a complete Phase A: %s" % error) from error
    return rows


def _environment_wait_seconds() -> int:
    source = os.environ.get("ENVIRONMENT_WAIT_SECONDS", "60")
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)", source):
        raise ValueError("ENVIRONMENT_WAIT_SECONDS must be a non-negative integer")
    return int(source)


def _ensure_trailing_newline(path: Path) -> None:
    """Terminate the validated final CSV record before Phase B appends."""
    with path.open("rb+") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            return
        handle.seek(-1, os.SEEK_END)
        if handle.read(1) == b"\n":
            return
        handle.seek(0, os.SEEK_END)
        handle.write(b"\n")
        handle.flush()


def run_phase(args: argparse.Namespace) -> None:
    """Run one independently started application Phase into the requested CSV."""
    output = Path(args.out)
    prepare_output(output, args.phase, args.campaign_id, args.rounds)
    plan = build_plan(args.rounds)
    retry_cap = 5 if args.phase == "a" else 20
    environment_sha256 = capture_environment(
        output,
        args.phase,
        DEFAULT_BASE_URL,
        retry_cap,
        wait_seconds=_environment_wait_seconds(),
    )
    warm_up(plan)

    if args.phase == "b":
        _ensure_trailing_newline(output)
    mode = "x" if args.phase == "a" else "a"
    with output.open(mode, encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=CSV_FIELDS,
            lineterminator="\n",
        )
        if args.phase == "a":
            writer.writeheader()
            handle.flush()
        for entry in plan:
            row = measure_entry(
                entry,
                campaign_id=args.campaign_id,
                phase=args.phase,
                environment_sha256=environment_sha256,
            )
            writer.writerow(row)
            handle.flush()

    requirement = "phase-a" if args.phase == "a" else "complete"
    validate_rows(read_rows(output), requirement, args.rounds)


def write_plan(entries: list[PlanEntry]) -> None:
    writer = csv.writer(sys.stdout, delimiter="\t", lineterminator="\n")
    writer.writerow(
        ("round", "position", "treatment_id", "strategy", "contention",
         "capacity", "contenders")
    )
    for entry in entries:
        treatment = entry.treatment
        writer.writerow((
            entry.round,
            entry.position,
            treatment.id,
            treatment.strategy,
            treatment.contention,
            treatment.capacity,
            treatment.contenders,
        ))


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--phase", required=True, choices=("a", "b"))
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--print-plan", action="store_true")
    args = parser.parse_args(argv)
    if not args.campaign_id:
        parser.error("--campaign-id must not be empty")
    try:
        build_plan(args.rounds)
    except ValueError as error:
        parser.error(str(error))
    if not args.print_plan and args.out is None:
        parser.error("--out is required unless --print-plan is used")
    return args


def main(argv=None) -> int:
    args = _parse_args(argv)
    if args.print_plan:
        write_plan(build_plan(args.rounds))
        return 0
    try:
        run_phase(args)
    except (
        ValueError,
        RuntimeError,
        OSError,
        csv.Error,
        json.JSONDecodeError,
        UnicodeError,
    ) as error:
        print("benchmark_capacity.py: error: " + _one_line(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
