#!/usr/bin/env python3
"""Deterministic Williams-order benchmark schedule."""

import argparse
import csv
import sys
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

_FIELDNAMES = (
    "schedule_version", "schedule_cycle", "schedule_row", "measured_round",
    "position_in_round", "treatment_id", "strategy", "contention", "capacity",
    "contenders",
)


def build_schedule(rounds: int) -> List[ScheduleEntry]:
    """Return a deterministic schedule for a positive multiple of ten rounds."""
    if rounds <= 0 or rounds % len(WILLIAMS_BASE) != 0:
        raise ValueError("rounds must be a positive multiple of 10")

    entries = []
    for measured_round in range(1, rounds + 1):
        schedule_cycle = (measured_round - 1) // len(WILLIAMS_BASE) + 1
        schedule_row = (measured_round - 1) % len(WILLIAMS_BASE) + 1
        order = tuple((treatment_id + schedule_row - 1) % len(WILLIAMS_BASE)
                      for treatment_id in WILLIAMS_BASE)
        for position_in_round, treatment_id in enumerate(order, start=1):
            entries.append(ScheduleEntry(
                schedule_version=SCHEDULE_VERSION,
                schedule_cycle=schedule_cycle,
                schedule_row=schedule_row,
                measured_round=measured_round,
                position_in_round=position_in_round,
                treatment=TREATMENTS[treatment_id],
            ))
    return entries


def write_tsv(entries: Sequence[ScheduleEntry], output: TextIO) -> None:
    """Write schedule entries as a stable LF-terminated TSV stream."""
    writer = csv.DictWriter(output, fieldnames=_FIELDNAMES, delimiter="\t",
                            lineterminator="\n")
    writer.writeheader()
    for entry in entries:
        treatment = entry.treatment
        writer.writerow({
            "schedule_version": entry.schedule_version,
            "schedule_cycle": entry.schedule_cycle,
            "schedule_row": entry.schedule_row,
            "measured_round": entry.measured_round,
            "position_in_round": entry.position_in_round,
            "treatment_id": treatment.treatment_id,
            "strategy": treatment.strategy,
            "contention": treatment.contention,
            "capacity": treatment.capacity,
            "contenders": treatment.contenders,
        })


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", required=True, type=int)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        entries = build_schedule(args.rounds)
    except ValueError as error:
        _parse_args_error(str(error))
        return 2
    write_tsv(entries, sys.stdout)
    return 0


def _parse_args_error(message: str) -> None:
    print("benchmark_schedule.py: error: %s" % message, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
