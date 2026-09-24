#!/usr/bin/env python3
"""Validate complete methodology-v2 benchmark campaign evidence."""

import argparse
import csv
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from benchmark_schema import FIELDS, validate_csv_row


_REQUIREMENTS = ("phase-a", "complete")
_TREATMENT_IDS = set(range(10))
_POSITIONS = set(range(1, 11))
_DIRECTED_PAIRS = {
    (left, right)
    for left in range(10)
    for right in range(10)
    if left != right
}


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
    """Validate one v2 CSV as complete phase-A or complete two-phase evidence."""
    path = Path(csv_path)
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, strict=True)
        if reader.fieldnames != FIELDS:
            raise ValueError("CSV header does not exactly match methodology v2")
        rows = []
        for line_number, decoded in enumerate(reader, start=2):
            if None in decoded or any(value is None for value in decoded.values()):
                raise ValueError("malformed CSV row at line %d" % line_number)
            rows.append(decoded)
    return validate_rows(rows, requirement, campaign_id, expected_rounds)


def validate_rows(
    rows: List[Dict[str, str]],
    requirement: str,
    campaign_id: Optional[str] = None,
    expected_rounds: Optional[int] = None,
) -> CampaignSummary:
    """Apply the complete campaign invariants to already decoded CSV rows."""
    if requirement not in _REQUIREMENTS:
        raise ValueError("requirement must be phase-a or complete")
    if expected_rounds is not None:
        if (isinstance(expected_rounds, bool)
                or not isinstance(expected_rounds, int)
                or expected_rounds <= 0
                or expected_rounds % 10 != 0):
            raise ValueError("expected rounds must be a positive multiple of 10")
    if not rows:
        raise ValueError("benchmark campaign must contain at least one row")

    validated = []
    for row_number, row in enumerate(rows, start=1):
        try:
            validated.append(validate_csv_row(row))
        except ValueError as error:
            raise ValueError(
                "invalid benchmark row %d: %s" % (row_number, error)
            ) from error

    campaign_ids = {row["campaign_id"] for row in validated}
    if len(campaign_ids) != 1:
        raise ValueError("benchmark rows must contain exactly one campaign_id")
    actual_campaign_id = next(iter(campaign_ids))
    if campaign_id is not None and actual_campaign_id != campaign_id:
        raise ValueError(
            "campaign_id mismatch: expected %s, found %s"
            % (campaign_id, actual_campaign_id)
        )

    for row_number, row in enumerate(validated, start=1):
        if row["run_status"] != "ok":
            raise ValueError(
                "benchmark row %d is not successful: %s"
                % (row_number, row["run_status"])
            )

    _validate_slots(validated)
    _validate_measurement_equations(validated)

    phase_sequence = []
    for row in validated:
        if not phase_sequence or phase_sequence[-1] != row["phase"]:
            phase_sequence.append(row["phase"])
    expected_phases = ["a"] if requirement == "phase-a" else ["a", "b"]
    if phase_sequence != expected_phases:
        if requirement == "phase-a":
            raise ValueError("phase-a evidence must contain only Phase A rows")
        raise ValueError("complete evidence must contain all Phase A rows before Phase B")

    by_phase = {
        phase: [row for row in validated if row["phase"] == phase]
        for phase in expected_phases
    }
    rounds_by_phase = {}
    hashes_by_phase = {}
    for phase in expected_phases:
        phase_rows = by_phase[phase]
        rounds_by_phase[phase] = _validate_phase(phase, phase_rows)
        hashes = {row["environment_sha256"] for row in phase_rows}
        if len(hashes) != 1:
            raise ValueError("Phase %s must use exactly one environment hash" % phase.upper())
        hashes_by_phase[phase] = next(iter(hashes))

    measured_rounds = rounds_by_phase["a"]
    if requirement == "complete" and rounds_by_phase["b"] != measured_rounds:
        raise ValueError("Phase A and Phase B must contain the same round count")
    if expected_rounds is not None and measured_rounds != expected_rounds:
        raise ValueError(
            "expected %d rounds, found %d" % (expected_rounds, measured_rounds)
        )

    return CampaignSummary(
        campaign_id=actual_campaign_id,
        phases=expected_phases,
        measured_rounds=measured_rounds,
        rows_per_phase={phase: len(by_phase[phase]) for phase in expected_phases},
        environment_sha256_by_phase=hashes_by_phase,
    )


def _validate_slots(rows: List[Dict[str, str]]) -> None:
    observed = set()
    for row_number, row in enumerate(rows, start=1):
        slot_id = int(row["slot_id"])
        if slot_id <= 0:
            raise ValueError("benchmark row %d has a nonpositive slot_id" % row_number)
        if slot_id in observed:
            raise ValueError("slot_id values must be unique across the campaign")
        observed.add(slot_id)


def _validate_measurement_equations(rows: List[Dict[str, str]]) -> None:
    for row_number, row in enumerate(rows, start=1):
        requests = int(row["requests"])
        contenders = int(row["contenders"])
        ok = int(row["ok"])
        ko = int(row["ko"])
        if requests != contenders or requests != ok + ko:
            raise ValueError(
                "benchmark row %d violates the request count equation" % row_number
            )

        capacity = int(row["capacity"])
        confirmed = int(row["confirmed"])
        overbooking = int(row["overbooking"])
        if overbooking != max(confirmed - capacity, 0):
            raise ValueError(
                "benchmark row %d violates the overbooking equation" % row_number
            )

        burst_start = int(row["burst_start_epoch_ms"])
        burst_end = int(row["burst_end_epoch_ms"])
        burst_wall = int(row["burst_wall_ms"])
        if burst_wall <= 0 or burst_wall != burst_end - burst_start:
            raise ValueError(
                "benchmark row %d violates the burst wall-clock equation" % row_number
            )
        expected_tps = round(requests * 1000.0 / burst_wall, 1)
        if float(row["tps"]) != expected_tps:
            raise ValueError(
                "benchmark row %d violates the TPS equation" % row_number
            )


def _validate_phase(phase: str, rows: List[Dict[str, str]]) -> int:
    if not rows:
        raise ValueError("Phase %s rows are missing" % phase.upper())
    rows_by_round = {}
    for row in rows:
        rows_by_round.setdefault(int(row["measured_round"]), []).append(row)
    measured_rounds = max(rows_by_round)
    if (min(rows_by_round) != 1
            or len(rows_by_round) != measured_rounds
            or measured_rounds % 10 != 0):
        raise ValueError(
            "Phase %s rounds must be consecutive from 1 in complete 10-round blocks"
            % phase.upper()
        )
    if len(rows) != measured_rounds * 10:
        raise ValueError(
            "Phase %s expected %d rows, found %d"
            % (phase.upper(), measured_rounds * 10, len(rows))
        )

    for measured_round in range(1, measured_rounds + 1):
        round_rows = rows_by_round[measured_round]
        treatment_counts = Counter(int(row["treatment_id"]) for row in round_rows)
        position_counts = Counter(int(row["position_in_round"]) for row in round_rows)
        if (set(treatment_counts) != _TREATMENT_IDS
                or any(count != 1 for count in treatment_counts.values())
                or set(position_counts) != _POSITIONS
                or any(count != 1 for count in position_counts.values())):
            raise ValueError(
                "Phase %s round %d must contain every treatment and position once"
                % (phase.upper(), measured_round)
            )

    for block_start in range(1, measured_rounds + 1, 10):
        block_end = block_start + 9
        block_rows = [
            row for row in rows
            if block_start <= int(row["measured_round"]) <= block_end
        ]
        treatment_positions = Counter(
            (int(row["treatment_id"]), int(row["position_in_round"]))
            for row in block_rows
        )
        if (len(treatment_positions) != 100
                or any(count != 1 for count in treatment_positions.values())):
            raise ValueError(
                "Phase %s rounds %d-%d do not balance treatments by position"
                % (phase.upper(), block_start, block_end)
            )

        adjacent_pairs = Counter()
        for measured_round in range(block_start, block_end + 1):
            ordered = sorted(
                rows_by_round[measured_round],
                key=lambda row: int(row["position_in_round"]),
            )
            treatment_ids = [int(row["treatment_id"]) for row in ordered]
            adjacent_pairs.update(zip(treatment_ids, treatment_ids[1:]))
        if (set(adjacent_pairs) != _DIRECTED_PAIRS
                or any(count != 1 for count in adjacent_pairs.values())):
            raise ValueError(
                "Phase %s rounds %d-%d do not balance directed adjacent pairs"
                % (phase.upper(), block_start, block_end)
            )
    return measured_rounds


class _OneLineArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "%s: error: %s\n" % (Path(self.prog).name, _one_line(message)))


def _parse_args(argv=None):
    parser = _OneLineArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--require", required=True, choices=_REQUIREMENTS)
    parser.add_argument("--rounds", type=int)
    return parser.parse_args(argv)


def _one_line(value: object) -> str:
    return str(value).replace("\r", " ").replace("\n", " ")


def main(argv=None) -> int:
    args = _parse_args(argv)
    try:
        summary = validate_campaign(
            args.csv_path,
            requirement=args.require,
            expected_rounds=args.rounds,
        )
    except (OSError, ValueError, csv.Error, UnicodeError) as error:
        print(
            "validate_benchmark_campaign.py: error: %s" % _one_line(error),
            file=sys.stderr,
        )
        return 1

    row_count = sum(summary.rows_per_phase.values())
    print(
        "benchmark campaign verified "
        "(campaign=%s, phases=%s, rounds=%d, %d rows)"
        % (
            summary.campaign_id,
            ",".join(summary.phases),
            summary.measured_rounds,
            row_count,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
