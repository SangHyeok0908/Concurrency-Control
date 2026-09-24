#!/usr/bin/env python3
"""Validate an optimistic-retry snapshot and emit benchmark CSV fields."""

import json
import math
import sys


FIELDS = (
    "succeeded",
    "retryExhausted",
    "versionConflicts",
    "deadlocks",
    "meanAttemptsPerSuccess",
)
COUNT_FIELDS = FIELDS[:-1]


def is_non_negative_integer(value):
    return type(value) is int and value >= 0


def main():
    try:
        snapshot = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        print("invalid optimistic metrics JSON: %s" % error, file=sys.stderr)
        return 1

    if not isinstance(snapshot, dict):
        print("optimistic metrics snapshot must be an object", file=sys.stderr)
        return 1

    missing = [key for key in ("succeededByAttempts",) + FIELDS if key not in snapshot]
    if missing:
        print(
            "missing optimistic metrics fields: " + ", ".join(missing),
            file=sys.stderr,
        )
        return 1

    if not isinstance(snapshot["succeededByAttempts"], dict):
        print("succeededByAttempts must be an object", file=sys.stderr)
        return 1
    if any(not is_non_negative_integer(snapshot[key]) for key in COUNT_FIELDS):
        print("optimistic metric counts must be non-negative integers", file=sys.stderr)
        return 1

    mean_attempts = snapshot["meanAttemptsPerSuccess"]
    if (isinstance(mean_attempts, bool)
            or not isinstance(mean_attempts, (int, float))
            or not math.isfinite(mean_attempts)
            or mean_attempts < 0):
        print("meanAttemptsPerSuccess must be a finite non-negative number", file=sys.stderr)
        return 1

    distribution_total = 0
    weighted_attempts = 0
    for attempts_text, count in snapshot["succeededByAttempts"].items():
        try:
            attempts = int(attempts_text)
        except (TypeError, ValueError):
            attempts = 0
        if str(attempts) != attempts_text or attempts < 1 or not is_non_negative_integer(count):
            print(
                "attempt buckets must be positive integer strings with non-negative integer counts",
                file=sys.stderr,
            )
            return 1
        distribution_total += count
        weighted_attempts += attempts * count

    if distribution_total != snapshot["succeeded"]:
        print(
            "success distribution total does not match succeeded: %d != %d"
            % (distribution_total, snapshot["succeeded"]),
            file=sys.stderr,
        )
        return 1

    expected_mean = 0 if distribution_total == 0 else weighted_attempts / distribution_total
    if not math.isclose(mean_attempts, expected_mean, abs_tol=0.011):
        print(
            "meanAttemptsPerSuccess does not match the success distribution",
            file=sys.stderr,
        )
        return 1

    print(",".join(str(snapshot[key]) for key in FIELDS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
