#!/usr/bin/env python3
"""Verify that Phase B will append only to one complete, untouched Phase A CSV."""

import argparse
import csv
import sys
from collections import Counter


FIELDS = [
    "ts", "phase", "round", "strategy", "optimistic_max_attempts", "contention",
    "capacity", "contenders", "slot_id", "requests", "ok", "ko", "mean_ms",
    "p95_ms", "max_ms", "tps", "remaining", "confirmed", "overbooking",
    "duplicates", "retry_succeeded", "retry_exhausted", "version_conflicts",
    "deadlocks", "mean_attempts",
]
STRATEGIES = ("baseline", "unique", "conditional", "pessimistic", "optimistic")
POINTS = (("low", "100", "120"), ("extreme", "1", "200"))


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    parser.add_argument("--rounds", required=True, type=int)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.rounds < 1:
        print("rounds must be positive", file=sys.stderr)
        return 1

    try:
        with open(args.csv_path, newline="") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames != FIELDS:
                print("Phase B input has an unexpected CSV header", file=sys.stderr)
                return 1
            rows = list(reader)
    except (OSError, csv.Error) as error:
        print("cannot read Phase B input: %s" % error, file=sys.stderr)
        return 1

    expected_count = args.rounds * len(STRATEGIES) * len(POINTS)
    if len(rows) != expected_count:
        print(
            "Phase B input expected %d rows from Phase A, found %d"
            % (expected_count, len(rows)),
            file=sys.stderr,
        )
        return 1

    expected = {
        (str(round_number), strategy, contention, capacity, contenders)
        for round_number in range(1, args.rounds + 1)
        for contention, capacity, contenders in POINTS
        for strategy in STRATEGIES
    }
    observed = Counter()
    slot_ids = set()
    for line_number, row in enumerate(rows, start=2):
        key = (
            row["round"], row["strategy"], row["contention"],
            row["capacity"], row["contenders"],
        )
        if (row["phase"] != "a"
                or row["optimistic_max_attempts"] != "5"
                or key not in expected
                or row["requests"] != row["contenders"]):
            print(
                "unexpected benchmark row at CSV line %d; Phase B requires a complete Phase A"
                % line_number,
                file=sys.stderr,
            )
            return 1
        try:
            slot_id = int(row["slot_id"])
        except ValueError:
            slot_id = 0
        if slot_id < 1 or slot_id in slot_ids:
            print("Phase A slot ids must be positive and unique", file=sys.stderr)
            return 1
        slot_ids.add(slot_id)
        observed[key] += 1

    if set(observed) != expected or any(count != 1 for count in observed.values()):
        print("Phase A rows do not cover every expected run exactly once", file=sys.stderr)
        return 1

    print("Phase A input verified (%d rows)" % expected_count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
