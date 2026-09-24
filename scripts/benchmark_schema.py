#!/usr/bin/env python3
"""Methodology-v2 benchmark row normalization and validation."""

import math
import re
from datetime import datetime
from typing import Dict


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

_MEASUREMENT_FIELDS = FIELDS[17:37]
_REPORT_FIELDS = FIELDS[18:28]
_DB_FIELDS = FIELDS[28:32]
_RETRY_FIELDS = FIELDS[32:37]
_REQUIRED_INPUT_FIELDS = tuple(FIELDS[:17]) + ("run_status",)
_OPTIONAL_INPUT_FIELDS = set(_MEASUREMENT_FIELDS + ["error_reason"])

_CAMPAIGN_ID = re.compile(r"[A-Za-z0-9._-]+\Z")
_LOWER_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ISO_LOCAL_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\Z"
)
_NONNEGATIVE_INTEGER = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_POSITIVE_INTEGER = re.compile(r"[1-9][0-9]*\Z")
_SIGNED_INTEGER = re.compile(r"(?:0|-?[1-9][0-9]*)\Z")
_NUMBER = re.compile(
    r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z"
)

_WILLIAMS_BASE = (0, 1, 9, 2, 8, 3, 7, 4, 6, 5)
_TREATMENTS = (
    ("baseline", "low", 100, 120),
    ("unique", "low", 100, 120),
    ("conditional", "low", 100, 120),
    ("pessimistic", "low", 100, 120),
    ("optimistic", "low", 100, 120),
    ("baseline", "extreme", 1, 200),
    ("unique", "extreme", 1, 200),
    ("conditional", "extreme", 1, 200),
    ("pessimistic", "extreme", 1, 200),
    ("optimistic", "extreme", 1, 200),
)


def normalize_and_validate_row(payload: object) -> Dict[str, str]:
    """Convert JSON nulls to blanks and return all fields as CSV strings."""
    if not isinstance(payload, dict):
        raise ValueError("benchmark row must be a JSON object")
    if any(not isinstance(key, str) for key in payload):
        raise ValueError("benchmark row keys must be strings")

    extra = set(payload) - set(FIELDS)
    missing = set(_REQUIRED_INPUT_FIELDS) - set(payload)
    if extra:
        raise ValueError("unexpected benchmark row fields: %s" % ", ".join(sorted(extra)))
    if missing:
        raise ValueError("missing benchmark row fields: %s" % ", ".join(sorted(missing)))

    row = {}
    for field in FIELDS:
        if field not in payload:
            if field not in _OPTIONAL_INPUT_FIELDS:
                raise ValueError("missing benchmark row field: %s" % field)
            value = None
        else:
            value = payload[field]
        row[field] = _normalize_scalar(field, value)
    return validate_csv_row(row)


def validate_csv_row(row: Dict[str, str]) -> Dict[str, str]:
    """Validate an already-decoded exact CSV row without null conversion."""
    if not isinstance(row, dict):
        raise ValueError("CSV row must be a dictionary")
    if len(row) != len(FIELDS) or set(row) != set(FIELDS):
        raise ValueError("CSV row must contain the exact v2 fields")
    if any(not isinstance(value, str) for value in row.values()):
        raise ValueError("CSV row values must all be strings")

    normalized = {field: row[field] for field in FIELDS}
    if any(value == "null" for value in normalized.values()):
        raise ValueError("literal null is not valid CSV data")

    _validate_plan_and_environment(normalized)
    _validate_measurements(normalized)
    _validate_status_shape(normalized)
    return normalized


def _normalize_scalar(field: str, value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        raise ValueError("%s must not be a boolean" % field)
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("%s must be finite" % field)
        return str(value)
    raise ValueError("%s must be a scalar JSON value" % field)


def _validate_plan_and_environment(row: Dict[str, str]) -> None:
    for field in _REQUIRED_INPUT_FIELDS:
        if row[field] == "":
            raise ValueError("%s is required" % field)

    if row["schema_version"] != SCHEMA_VERSION:
        raise ValueError("schema_version must be %s" % SCHEMA_VERSION)
    if not _CAMPAIGN_ID.fullmatch(row["campaign_id"]):
        raise ValueError("invalid campaign_id")
    if row["phase"] not in ("a", "b"):
        raise ValueError("phase must be a or b")
    expected_app_start = "%s-phase-%s" % (row["campaign_id"], row["phase"])
    if row["app_start_id"] != expected_app_start:
        raise ValueError("app_start_id does not match campaign and phase")
    if not _LOWER_SHA256.fullmatch(row["environment_sha256"]):
        raise ValueError("environment_sha256 must be 64 lowercase hexadecimal characters")
    if row["schedule_version"] != SCHEDULE_VERSION:
        raise ValueError("schedule_version must be %s" % SCHEDULE_VERSION)
    if not _ISO_LOCAL_TIMESTAMP.fullmatch(row["ts"]):
        raise ValueError("ts must be an ISO-8601 local timestamp")
    try:
        datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("ts must be a real ISO-8601 datetime") from error

    positive_fields = (
        "schedule_cycle", "schedule_row", "measured_round", "position_in_round",
        "optimistic_max_attempts", "capacity", "contenders",
    )
    for field in positive_fields:
        _require_integer(row, field, positive=True)
    _require_integer(row, "treatment_id", positive=False)

    measured_round = int(row["measured_round"])
    schedule_cycle = int(row["schedule_cycle"])
    schedule_row = int(row["schedule_row"])
    position = int(row["position_in_round"])
    treatment_id = int(row["treatment_id"])
    if schedule_cycle != (measured_round - 1) // 10 + 1:
        raise ValueError("schedule_cycle does not match measured_round")
    if schedule_row != (measured_round - 1) % 10 + 1:
        raise ValueError("schedule_row does not match measured_round")
    if not 1 <= position <= 10:
        raise ValueError("position_in_round must be between 1 and 10")
    expected_treatment_id = (_WILLIAMS_BASE[position - 1] + schedule_row - 1) % 10
    if treatment_id != expected_treatment_id:
        raise ValueError("treatment_id does not match schedule row and position")
    if not 0 <= treatment_id < len(_TREATMENTS):
        raise ValueError("unknown treatment_id")

    strategy, contention, capacity, contenders = _TREATMENTS[treatment_id]
    actual_treatment = (
        row["strategy"], row["contention"], int(row["capacity"]),
        int(row["contenders"]),
    )
    if actual_treatment != (strategy, contention, capacity, contenders):
        raise ValueError("strategy/contention/capacity/contenders do not match treatment_id")

    expected_cap = 5 if row["phase"] == "a" else 20
    if int(row["optimistic_max_attempts"]) != expected_cap:
        raise ValueError("optimistic_max_attempts does not match phase")


def _validate_measurements(row: Dict[str, str]) -> None:
    if row["slot_id"]:
        _require_integer(row, "slot_id", positive=False)

    nonnegative_integer_fields = (
        "requests", "ok", "ko", "mean_ms", "p95_ms", "max_ms",
        "burst_start_epoch_ms", "burst_end_epoch_ms", "burst_wall_ms",
        "confirmed", "overbooking", "duplicates", "retry_succeeded",
        "retry_exhausted", "version_conflicts", "deadlocks",
    )
    for field in nonnegative_integer_fields:
        if row[field]:
            _require_integer(row, field, positive=False)
    if row["remaining"] and not _SIGNED_INTEGER.fullmatch(row["remaining"]):
        raise ValueError("remaining must be an integer")
    for field in ("tps", "mean_attempts"):
        if row[field]:
            if not _NUMBER.fullmatch(row[field]):
                raise ValueError("%s must be a nonnegative finite number" % field)
            value = float(row[field])
            if not math.isfinite(value) or value < 0:
                raise ValueError("%s must be a nonnegative finite number" % field)


def _validate_status_shape(row: Dict[str, str]) -> None:
    status = row["run_status"]
    if status not in RUN_STATUSES:
        raise ValueError("unknown run_status: %s" % status)
    reason = row["error_reason"]
    if "\r" in reason or "\n" in reason:
        raise ValueError("error_reason must be one line")

    slot = row["slot_id"] != ""
    report = _group_presence(row, _REPORT_FIELDS, "report")
    database = _group_presence(row, _DB_FIELDS, "database")
    retry = _group_presence(row, _RETRY_FIELDS, "retry")
    shape = (slot, report, database, retry)

    if status == "ok":
        if reason != "":
            raise ValueError("successful rows must have an empty error_reason")
        allowed = ((True, True, True, True),)
    else:
        if not reason.strip():
            raise ValueError("failed rows require a nonempty error_reason")
        if status in ("seed_failed", "gatling_failed"):
            allowed = ((False, False, False, False),)
        elif status == "parse_failed":
            allowed = ((True, False, False, False),)
        elif status == "db_read_failed":
            allowed = (
                (False, False, False, False),
                (True, True, False, False),
            )
        else:
            allowed = (
                (False, False, False, False),
                (True, True, True, False),
            )
    if shape not in allowed:
        raise ValueError("measurement fields do not match run_status %s" % status)


def _group_presence(row: Dict[str, str], fields: tuple, name: str) -> bool:
    present = [row[field] != "" for field in fields]
    if any(present) and not all(present):
        raise ValueError("%s fields must be all present or all empty" % name)
    return all(present)


def _require_integer(row: Dict[str, str], field: str, positive: bool) -> None:
    pattern = _POSITIVE_INTEGER if positive else _NONNEGATIVE_INTEGER
    if not pattern.fullmatch(row[field]):
        qualifier = "positive" if positive else "nonnegative"
        raise ValueError("%s must be a %s integer" % (field, qualifier))
