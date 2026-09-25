#!/usr/bin/env python3
"""Validate and publish the same-applicant/same-slot benchmark evidence."""

import argparse
import csv
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path


STATUS_KEYS = ("201", "409", "500", "503", "other", "no_response")
SENTINEL = "DUPLICATE_HTTP_STATUS_COUNTS="
GATLING_KEYS = ("ok", "ko")
GATLING_SENTINEL = "DUPLICATE_GATLING_COUNTS="
STRATEGIES = ("baseline", "unique", "conditional", "pessimistic", "optimistic")
CSV_FIELDS = (
    "schema_version",
    "ts",
    "round",
    "position",
    "strategy",
    "workload",
    "capacity",
    "requests",
    "slot_id",
    "applicant_id",
    "http_201",
    "http_409",
    "http_500",
    "http_503",
    "http_other",
    "http_no_response",
    "ok",
    "ko",
    "remaining",
    "confirmed",
    "pair_reservations",
    "duplicate_rows",
    "seats_consumed",
    "invariant_pass",
)


def parse_status_counts(text):
    """Return the one complete status histogram emitted by the Gatling run."""
    payloads = [
        line[len(SENTINEL):]
        for line in text.splitlines()
        if line.startswith(SENTINEL)
    ]
    if len(payloads) != 1:
        raise ValueError(
            "expected exactly one %s sentinel, found %d" % (SENTINEL, len(payloads))
        )

    try:
        counts = json.loads(payloads[0])
    except json.JSONDecodeError as error:
        raise ValueError("invalid status sentinel JSON: %s" % error) from error

    if not isinstance(counts, dict) or set(counts) != set(STATUS_KEYS):
        raise ValueError(
            "status keys must be exactly %s" % ", ".join(STATUS_KEYS)
        )
    for key, value in counts.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("status %s must be a non-negative integer" % key)
    return {key: counts[key] for key in STATUS_KEYS}


def parse_gatling_counts(text):
    """Return Gatling's classification, observed independently of HTTP buckets."""
    payloads = [
        line[len(GATLING_SENTINEL):]
        for line in text.splitlines()
        if line.startswith(GATLING_SENTINEL)
    ]
    if len(payloads) != 1:
        raise ValueError(
            "expected exactly one %s sentinel, found %d"
            % (GATLING_SENTINEL, len(payloads))
        )

    try:
        counts = json.loads(payloads[0])
    except json.JSONDecodeError as error:
        raise ValueError("invalid Gatling sentinel JSON: %s" % error) from error

    if not isinstance(counts, dict) or set(counts) != set(GATLING_KEYS):
        raise ValueError("Gatling keys must be exactly %s" % ", ".join(GATLING_KEYS))
    for key, value in counts.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("Gatling %s must be a non-negative integer" % key)
    return {key: counts[key] for key in GATLING_KEYS}


def _non_negative_int(row, field, errors):
    value = row.get(field)
    if isinstance(value, bool):
        errors.append("%s must be a non-negative integer" % field)
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        errors.append("%s must be a non-negative integer" % field)
        return None
    if parsed < 0 or str(value).strip() != str(parsed):
        errors.append("%s must be a non-negative integer" % field)
        return None
    return parsed


def validate_row(row):
    """Return every violated HTTP or database invariant for one complete run."""
    errors = []
    numeric_fields = (
        "schema_version",
        "round",
        "position",
        "capacity",
        "requests",
        "slot_id",
        "applicant_id",
        "http_201",
        "http_409",
        "http_500",
        "http_503",
        "http_other",
        "http_no_response",
        "ok",
        "ko",
        "remaining",
        "confirmed",
        "pair_reservations",
        "duplicate_rows",
        "seats_consumed",
    )
    numbers = {
        field: _non_negative_int(row, field, errors)
        for field in numeric_fields
    }
    if errors:
        return errors

    if numbers["schema_version"] != 1:
        errors.append("schema_version must be 1")
    if row.get("workload") != "same-applicant-slot":
        errors.append("workload must be same-applicant-slot")
    if row.get("strategy") not in STRATEGIES:
        errors.append("strategy is not supported")
    if not str(row.get("ts", "")).strip():
        errors.append("ts must not be empty")
    if numbers["round"] < 1:
        errors.append("round must be positive")
    if not 1 <= numbers["position"] <= len(STRATEGIES):
        errors.append("position must be between 1 and %d" % len(STRATEGIES))
    if numbers["capacity"] < 1:
        errors.append("capacity must be positive")
    if numbers["requests"] < 1:
        errors.append("requests must be positive")
    if numbers["slot_id"] < 1:
        errors.append("slot_id must be positive")
    if numbers["applicant_id"] < 1:
        errors.append("applicant_id must be positive")
    if numbers["capacity"] < numbers["requests"]:
        errors.append("capacity must be at least requests")

    http_total = sum(
        numbers[field]
        for field in (
            "http_201", "http_409", "http_500", "http_503",
            "http_other", "http_no_response",
        )
    )
    if http_total != numbers["requests"]:
        errors.append(
            "HTTP status total must equal requests: %d != %d"
            % (http_total, numbers["requests"])
        )
    if numbers["ok"] + numbers["ko"] != numbers["requests"]:
        errors.append("Gatling total must equal requests")
    if numbers["ok"] != numbers["http_201"] + numbers["http_409"]:
        errors.append("Gatling OK must equal HTTP 201 + HTTP 409")
    expected_ko = sum(
        numbers[field]
        for field in ("http_500", "http_503", "http_other", "http_no_response")
    )
    if numbers["ko"] != expected_ko:
        errors.append("Gatling KO must equal unexpected HTTP and no-response counts")

    if numbers["http_201"] != 1:
        errors.append("HTTP 201 must be 1")
    if numbers["http_other"] != 0:
        errors.append("http_other must be 0")
    if numbers["http_no_response"] != 0:
        errors.append("http_no_response must be 0")
    if numbers["confirmed"] != 1:
        errors.append("confirmed must be 1")
    if numbers["pair_reservations"] != 1:
        errors.append("pair_reservations must be 1")
    if numbers["duplicate_rows"] != 0:
        errors.append("duplicate_rows must be 0")
    if numbers["remaining"] != numbers["capacity"] - 1:
        errors.append("remaining must equal capacity - 1")
    if numbers["seats_consumed"] != 1:
        errors.append("seats_consumed must be 1")
    if numbers["seats_consumed"] != numbers["capacity"] - numbers["remaining"]:
        errors.append("seats_consumed must equal capacity - remaining")

    if row.get("strategy") == "unique":
        if numbers["http_409"] != numbers["requests"] - 1:
            errors.append("unique HTTP 409 must equal requests - 1")
        if numbers["http_500"] != 0:
            errors.append("unique HTTP 500 must be 0")
        if numbers["http_503"] != 0:
            errors.append("unique HTTP 503 must be 0")

    return errors


def _is_true(value):
    return value is True or (isinstance(value, str) and value.lower() == "true")


def validate_campaign(rows, rounds):
    """Return structural and per-run errors for one complete cyclic campaign."""
    errors = []
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1:
        return ["rounds must be a positive integer"]

    expected_count = rounds * len(STRATEGIES)
    if len(rows) != expected_count:
        errors.append(
            "campaign must contain exactly %d rows; found %d"
            % (expected_count, len(rows))
        )

    seen_positions = set()
    slot_ids = []
    applicant_ids = []
    workload_controls = set()
    strategy_counts = Counter()
    for index, row in enumerate(rows, start=1):
        row_errors = validate_row(row)
        errors.extend("row %d: %s" % (index, error) for error in row_errors)
        if not _is_true(row.get("invariant_pass")):
            errors.append("row %d: invariant_pass must be true" % index)

        try:
            round_number = int(row["round"])
            position = int(row["position"])
            slot_id = int(row["slot_id"])
            applicant_id = int(row["applicant_id"])
            capacity = int(row["capacity"])
            requests = int(row["requests"])
        except (KeyError, TypeError, ValueError):
            continue

        key = (round_number, position)
        if key in seen_positions:
            errors.append("duplicate round/position: %d/%d" % key)
        seen_positions.add(key)
        slot_ids.append(slot_id)
        applicant_ids.append(applicant_id)
        workload_controls.add((capacity, requests))

        strategy = row.get("strategy")
        strategy_counts[strategy] += 1
        if 1 <= round_number <= rounds and 1 <= position <= len(STRATEGIES):
            expected = STRATEGIES[(round_number + position - 2) % len(STRATEGIES)]
            if strategy != expected:
                errors.append(
                    "row %d violates cyclic schedule: round %d position %d "
                    "expected %s, found %s"
                    % (index, round_number, position, expected, strategy)
                )

    expected_positions = {
        (round_number, position)
        for round_number in range(1, rounds + 1)
        for position in range(1, len(STRATEGIES) + 1)
    }
    missing_positions = sorted(expected_positions - seen_positions)
    if missing_positions:
        errors.append("missing round/position entries: %s" % missing_positions)
    if len(slot_ids) != len(set(slot_ids)):
        errors.append("slot_id must be unique per run")
    if len(applicant_ids) != len(set(applicant_ids)):
        errors.append("applicant_id must be unique per run")
    if len(workload_controls) != 1:
        errors.append("all campaign rows must use the same capacity and requests")
    for strategy in STRATEGIES:
        if strategy_counts[strategy] != rounds:
            errors.append(
                "%s must appear %d times; found %d"
                % (strategy, rounds, strategy_counts[strategy])
            )
    return errors


def _serialise_row(row):
    return {
        field: (
            "true" if value is True else "false" if value is False else str(value)
        )
        for field, value in row.items()
    }


def _write_rows_atomic(path, rows):
    path = Path(path)
    if not path.parent.is_dir():
        raise ValueError("output directory does not exist: %s" % path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".%s." % path.name, suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(_serialise_row(row) for row in rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _read_rows(path):
    path = Path(path)
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != CSV_FIELDS:
            raise ValueError("unexpected duplicate benchmark CSV header")
        return list(reader)


def record(args):
    output = Path(args.output)
    if args.mode == "create" and output.exists():
        raise ValueError("output already exists: %s" % output)
    if args.mode == "append" and not output.is_file():
        raise ValueError("append output does not exist: %s" % output)

    gatling_log = Path(args.gatling_log).read_text()
    counts = parse_status_counts(gatling_log)
    gatling_counts = parse_gatling_counts(gatling_log)
    row = {
        "schema_version": 1,
        "ts": args.timestamp,
        "round": args.round,
        "position": args.position,
        "strategy": args.strategy,
        "workload": "same-applicant-slot",
        "capacity": args.capacity,
        "requests": args.requests,
        "slot_id": args.slot_id,
        "applicant_id": args.applicant_id,
        "http_201": counts["201"],
        "http_409": counts["409"],
        "http_500": counts["500"],
        "http_503": counts["503"],
        "http_other": counts["other"],
        "http_no_response": counts["no_response"],
        "ok": gatling_counts["ok"],
        "ko": gatling_counts["ko"],
        "remaining": args.remaining,
        "confirmed": args.confirmed,
        "pair_reservations": args.pair_reservations,
        "duplicate_rows": args.duplicate_rows,
        "seats_consumed": args.capacity - args.remaining,
    }
    errors = validate_row(row)
    row["invariant_pass"] = not errors
    rows = [] if args.mode == "create" else _read_rows(output)
    _write_rows_atomic(output, rows + [row])

    if errors:
        for error in errors:
            print("duplicate benchmark invariant failed: " + error, file=sys.stderr)
        return 1
    print(
        "duplicate benchmark row verified: strategy=%s round=%d position=%d"
        % (args.strategy, args.round, args.position)
    )
    return 0


def summarize(args):
    rows = _read_rows(args.csv)
    errors = validate_campaign(rows, args.rounds)
    if errors:
        for error in errors:
            print("duplicate benchmark campaign invalid: " + error, file=sys.stderr)
        return 1

    lines = [
        "| 전략 | 실행 | 201 | 409 | 500 | 503 | 기타 | 무응답 | DB 불변식 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for strategy in STRATEGIES:
        selected = [row for row in rows if row["strategy"] == strategy]
        totals = {
            field: sum(int(row[field]) for row in selected)
            for field in (
                "http_201", "http_409", "http_500", "http_503",
                "http_other", "http_no_response",
            )
        }
        lines.append(
            "| %s | %d | %d | %d | %d | %d | %d | %d | PASS |"
            % (
                strategy,
                len(selected),
                totals["http_201"],
                totals["http_409"],
                totals["http_500"],
                totals["http_503"],
                totals["http_other"],
                totals["http_no_response"],
            )
        )
    print("\n".join(lines))
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    record_parser = subparsers.add_parser("record")
    record_parser.add_argument("output")
    record_parser.add_argument("--mode", required=True, choices=("create", "append"))
    record_parser.add_argument("--timestamp", required=True)
    record_parser.add_argument("--round", required=True, type=int)
    record_parser.add_argument("--position", required=True, type=int)
    record_parser.add_argument("--strategy", required=True, choices=STRATEGIES)
    record_parser.add_argument("--capacity", required=True, type=int)
    record_parser.add_argument("--requests", required=True, type=int)
    record_parser.add_argument("--slot-id", required=True, type=int)
    record_parser.add_argument("--applicant-id", required=True, type=int)
    record_parser.add_argument("--gatling-log", required=True)
    record_parser.add_argument("--remaining", required=True, type=int)
    record_parser.add_argument("--confirmed", required=True, type=int)
    record_parser.add_argument("--pair-reservations", required=True, type=int)
    record_parser.add_argument("--duplicate-rows", required=True, type=int)

    summarize_parser = subparsers.add_parser("summarize")
    summarize_parser.add_argument("csv")
    summarize_parser.add_argument("--rounds", required=True, type=int)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        if args.command == "record":
            return record(args)
        return summarize(args)
    except (OSError, ValueError) as error:
        print("duplicate benchmark failed: %s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
