#!/usr/bin/env python3
import copy
import csv
import io
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from benchmark_schedule import build_schedule  # noqa: E402
from benchmark_schema import FIELDS, normalize_and_validate_row  # noqa: E402
import validate_benchmark_campaign as campaign_validator  # noqa: E402
from validate_benchmark_campaign import (  # noqa: E402
    CampaignSummary,
    validate_campaign,
    validate_rows,
)


VALIDATOR = SCRIPTS / "validate_benchmark_campaign.py"
MEASUREMENT_FIELDS = FIELDS[17:37]


def build_phase(rounds=10, phase="a", campaign_id="campaign-001", slot_start=1):
    rows = []
    base_time = datetime(2026, 9, 24, 10, 0, 0)
    environment_sha256 = ("a" if phase == "a" else "b") * 64
    max_attempts = 5 if phase == "a" else 20
    for offset, entry in enumerate(build_schedule(rounds)):
        treatment = entry.treatment
        slot_id = slot_start + offset
        wall_ms = 1000
        start_ms = 1_800_000_000_000 + slot_id * 2000
        confirmed = treatment.contenders
        payload = {
            "schema_version": 2,
            "campaign_id": campaign_id,
            "phase": phase,
            "app_start_id": "%s-phase-%s" % (campaign_id, phase),
            "environment_sha256": environment_sha256,
            "schedule_version": entry.schedule_version,
            "schedule_cycle": entry.schedule_cycle,
            "schedule_row": entry.schedule_row,
            "measured_round": entry.measured_round,
            "position_in_round": entry.position_in_round,
            "treatment_id": treatment.treatment_id,
            "ts": (base_time + timedelta(seconds=slot_id)).isoformat(),
            "strategy": treatment.strategy,
            "optimistic_max_attempts": max_attempts,
            "contention": treatment.contention,
            "capacity": treatment.capacity,
            "contenders": treatment.contenders,
            "slot_id": slot_id,
            "requests": treatment.contenders,
            "ok": treatment.contenders,
            "ko": 0,
            "mean_ms": 10,
            "p95_ms": 20,
            "max_ms": 30,
            "burst_start_epoch_ms": start_ms,
            "burst_end_epoch_ms": start_ms + wall_ms,
            "burst_wall_ms": wall_ms,
            "tps": round(treatment.contenders * 1000.0 / wall_ms, 1),
            "remaining": treatment.capacity - confirmed,
            "confirmed": confirmed,
            "overbooking": max(confirmed - treatment.capacity, 0),
            "duplicates": 0,
            "retry_succeeded": 0,
            "retry_exhausted": 0,
            "version_conflicts": 0,
            "deadlocks": 0,
            "mean_attempts": 1.0,
            "run_status": "ok",
            "error_reason": "",
        }
        rows.append(normalize_and_validate_row(payload))
    return rows


def build_rows(rounds=10, phases=("a", "b"), campaign_id="campaign-001"):
    rows = []
    slot_start = 1
    for phase in phases:
        phase_rows = build_phase(rounds, phase, campaign_id, slot_start)
        rows.extend(phase_rows)
        slot_start += len(phase_rows)
    return rows


def csv_bytes(rows, fields=FIELDS):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def failed_row(row):
    result = dict(row)
    for field in MEASUREMENT_FIELDS:
        result[field] = ""
    result["run_status"] = "seed_failed"
    result["error_reason"] = "controlled failure"
    return result


class CampaignValidatorAcceptanceTest(unittest.TestCase):

    def test_accepts_ten_and_twenty_round_phase_a_campaigns(self):
        for rounds in (10, 20):
            with self.subTest(rounds=rounds):
                rows = build_rows(rounds, phases=("a",))
                summary = validate_rows(
                    rows, requirement="phase-a", expected_rounds=rounds,
                )
                self.assertEqual(
                    CampaignSummary(
                        campaign_id="campaign-001",
                        phases=["a"],
                        measured_rounds=rounds,
                        rows_per_phase={"a": rounds * 10},
                        environment_sha256_by_phase={"a": "a" * 64},
                    ),
                    summary,
                )

    def test_accepts_ten_and_twenty_round_complete_campaigns(self):
        for rounds in (10, 20):
            with self.subTest(rounds=rounds):
                rows = build_rows(rounds)
                summary = validate_rows(
                    rows, requirement="complete", expected_rounds=rounds,
                )
                self.assertEqual(["a", "b"], summary.phases)
                self.assertEqual(rounds, summary.measured_rounds)
                self.assertEqual(
                    {"a": rounds * 10, "b": rounds * 10},
                    summary.rows_per_phase,
                )
                self.assertEqual(
                    {"a": "a" * 64, "b": "b" * 64},
                    summary.environment_sha256_by_phase,
                )

    def test_infers_twenty_rounds_when_expected_rounds_is_omitted(self):
        phase_a = validate_rows(build_rows(20, phases=("a",)), "phase-a")
        complete = validate_rows(build_rows(20), "complete")
        thirty_round_phase_a = validate_rows(
            build_rows(30, phases=("a",)), "phase-a",
        )

        self.assertEqual(20, phase_a.measured_rounds)
        self.assertEqual(20, complete.measured_rounds)
        self.assertEqual(30, thirty_round_phase_a.measured_rounds)

        with tempfile.TemporaryDirectory() as directory_name:
            path = Path(directory_name) / "twenty-rounds.csv"
            path.write_bytes(csv_bytes(build_rows(20)))
            self.assertEqual(
                20,
                validate_campaign(path, "complete").measured_rounds,
            )

    def test_validate_campaign_reads_the_exact_v2_csv(self):
        with tempfile.TemporaryDirectory() as directory_name:
            path = Path(directory_name) / "campaign.csv"
            path.write_bytes(csv_bytes(build_rows()))

            summary = validate_campaign(
                path, requirement="complete", campaign_id="campaign-001",
                expected_rounds=10,
            )

            self.assertEqual("campaign-001", summary.campaign_id)
            self.assertEqual(10, summary.measured_rounds)

    def test_rejects_an_expected_round_count_mismatch(self):
        with self.assertRaisesRegex(ValueError, "expected 20 rounds"):
            validate_rows(
                build_rows(10), requirement="complete", expected_rounds=20,
            )

    def test_rejects_invalid_requirements_and_expected_round_values(self):
        rows = build_rows(10, phases=("a",))
        for requirement in ("", "phase-b", "all", None):
            with self.subTest(requirement=requirement):
                with self.assertRaises(ValueError):
                    validate_rows(rows, requirement=requirement)
        for rounds in (0, -10, 1, 9, 11, True):
            with self.subTest(rounds=rounds):
                with self.assertRaises(ValueError):
                    validate_rows(
                        rows, requirement="phase-a", expected_rounds=rounds,
                    )


class CampaignValidatorMutationTest(unittest.TestCase):

    def assert_rejected(self, rows, requirement="complete", **kwargs):
        with self.assertRaises(ValueError):
            validate_rows(rows, requirement=requirement, **kwargs)

    def test_rejects_failure_rows_before_reporting_a_complete_result(self):
        rows = build_rows()
        rows[-1] = failed_row(rows[-1])
        self.assert_rejected(rows)

    def test_rejects_missing_and_duplicate_treatments_or_positions(self):
        missing = build_rows()
        del missing[4]
        self.assert_rejected(missing)

        duplicate = build_rows()
        duplicate[1] = dict(duplicate[0], slot_id="99999")
        self.assert_rejected(duplicate)

    def test_rejects_a_mismatched_or_mixed_campaign_id(self):
        rows = build_rows()
        self.assert_rejected(rows, campaign_id="another-campaign")

        mixed = build_rows()
        mixed[-1]["campaign_id"] = "campaign-002"
        mixed[-1]["app_start_id"] = "campaign-002-phase-b"
        self.assert_rejected(mixed)

        invalid = build_rows()
        invalid[0]["campaign_id"] = "bad/id"
        invalid[0]["app_start_id"] = "bad/id-phase-a"
        self.assert_rejected(invalid)

    def test_rejects_invalid_phase_cap_and_app_start_metadata(self):
        mutations = (
            ("optimistic_max_attempts", "20"),
            ("app_start_id", "wrong-phase-a"),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                rows = build_rows()
                rows[0][field] = value
                self.assert_rejected(rows)

    def test_rejects_multiple_environment_hashes_within_a_phase(self):
        rows = build_rows()
        rows[1]["environment_sha256"] = "c" * 64
        self.assert_rejected(rows)

    def test_rejects_request_and_database_equation_violations(self):
        mutations = (
            ("requests", "119"),
            ("ok", "119"),
            ("overbooking", "0"),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                rows = build_rows()
                rows[0][field] = value
                self.assert_rejected(rows)

    def test_rejects_duplicate_or_nonpositive_slot_ids(self):
        for value in ("0", "2"):
            with self.subTest(value=value):
                rows = build_rows()
                rows[0]["slot_id"] = value
                self.assert_rejected(rows)

    def test_rejects_wall_clock_and_tps_equation_violations(self):
        mutations = (
            ("burst_wall_ms", "999"),
            ("burst_end_epoch_ms", "1800000001000"),
            ("tps", "999.9"),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                rows = build_rows()
                rows[0][field] = value
                self.assert_rejected(rows)

        rows = build_rows()
        rows[0]["burst_end_epoch_ms"] = rows[0]["burst_start_epoch_ms"]
        rows[0]["burst_wall_ms"] = "0"
        self.assert_rejected(rows)

    def test_rejects_incomplete_or_noncontiguous_ten_round_blocks(self):
        incomplete = build_rows()
        del incomplete[90:100]
        self.assert_rejected(incomplete)

        noncontiguous = build_rows(20, phases=("a",))
        del noncontiguous[90:100]
        self.assert_rejected(noncontiguous, requirement="phase-a")

    def test_huge_noncontiguous_round_is_a_controlled_validation_error(self):
        rows = build_rows(10, phases=("a",))
        rows[90]["measured_round"] = str(10 ** 30)
        rows[90]["schedule_cycle"] = str(10 ** 29)

        with self.assertRaises(ValueError):
            validate_rows(rows, requirement="phase-a")

    def test_phase_internal_file_order_is_not_a_validation_constraint(self):
        rows = build_rows()
        rows[1], rows[2] = rows[2], rows[1]
        summary = validate_rows(rows, requirement="complete")
        self.assertEqual(10, summary.measured_rounds)

        rows = build_rows()
        rows[:10], rows[10:20] = rows[10:20], rows[:10]
        summary = validate_rows(rows, requirement="complete")
        self.assertEqual(10, summary.measured_rounds)

    def test_explicitly_rejects_a_schedule_with_unbalanced_directed_pairs(self):
        rows = build_rows(10, phases=("a",))
        for row in rows:
            treatment_id = (
                int(row["position_in_round"]) - 1
                + int(row["measured_round"]) - 1
            ) % 10
            row["treatment_id"] = str(treatment_id)

        with mock.patch.object(
            campaign_validator,
            "validate_csv_row",
            side_effect=lambda row: dict(row),
        ):
            with self.assertRaisesRegex(ValueError, "directed adjacent pairs"):
                validate_rows(rows, requirement="phase-a")

    def test_complete_requires_a_before_b_with_equal_round_counts(self):
        b_before_a = build_rows(10, phases=("b", "a"))
        self.assert_rejected(b_before_a)

        unequal = build_phase(10, "a", slot_start=1)
        unequal += build_phase(20, "b", slot_start=101)
        self.assert_rejected(unequal)

    def test_phase_a_mode_rejects_any_phase_b_row(self):
        self.assert_rejected(build_rows(), requirement="phase-a")

    def test_phase_b_boundary_rejects_wrong_campaign_cap_hash_incomplete_or_failed_a(self):
        valid_a = build_rows(10, phases=("a",))

        cases = []
        cases.append((valid_a, {"campaign_id": "other-campaign"}))
        bad_cap = copy.deepcopy(valid_a)
        bad_cap[0]["optimistic_max_attempts"] = "20"
        cases.append((bad_cap, {}))
        bad_hash = copy.deepcopy(valid_a)
        bad_hash[1]["environment_sha256"] = "f" * 64
        cases.append((bad_hash, {}))
        cases.append((copy.deepcopy(valid_a[:-1]), {}))
        failed = copy.deepcopy(valid_a)
        failed[-1] = failed_row(failed[-1])
        cases.append((failed, {}))

        for index, (rows, kwargs) in enumerate(cases):
            with self.subTest(case=index):
                self.assert_rejected(rows, requirement="phase-a", **kwargs)

        for cap in ("5", "19"):
            with self.subTest(phase_b_cap=cap):
                invalid_b_cap = build_rows()
                invalid_b_cap[100]["optimistic_max_attempts"] = cap
                self.assert_rejected(invalid_b_cap)

        drifting_b_hash = build_rows()
        drifting_b_hash[101]["environment_sha256"] = "f" * 64
        self.assert_rejected(drifting_b_hash)


class CampaignValidatorCsvAndCliTest(unittest.TestCase):

    def invoke(self, path, *arguments):
        return subprocess.run(
            [sys.executable, str(VALIDATOR), str(path)] + list(arguments),
            text=True,
            capture_output=True,
            check=False,
        )

    def test_rejects_empty_bad_header_and_malformed_csv_rows(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            empty = directory / "empty.csv"
            empty.write_bytes(b"")
            with self.assertRaises(ValueError):
                validate_campaign(empty, "complete")

            bad_header = directory / "bad-header.csv"
            bad_header.write_bytes(csv_bytes(build_rows(), fields=list(reversed(FIELDS))))
            with self.assertRaises(ValueError):
                validate_campaign(bad_header, "complete")

            malformed = directory / "malformed.csv"
            malformed.write_bytes(csv_bytes(build_rows()) + b"extra,cell\n")
            with self.assertRaises(ValueError):
                validate_campaign(malformed, "complete")

            header_only = directory / "header-only.csv"
            header_only.write_bytes(csv_bytes([]))
            with self.assertRaises(ValueError):
                validate_campaign(header_only, "complete")

    def test_cli_prints_summary_only_after_successful_validation(self):
        with tempfile.TemporaryDirectory() as directory_name:
            path = Path(directory_name) / "campaign.csv"
            path.write_bytes(csv_bytes(build_rows()))

            result = self.invoke(path, "--require", "complete", "--rounds", "10")

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("", result.stderr)
            self.assertIn("campaign-001", result.stdout)
            self.assertIn("200 rows", result.stdout)

    def test_cli_failure_is_one_stderr_line_with_no_summary(self):
        with tempfile.TemporaryDirectory() as directory_name:
            path = Path(directory_name) / "campaign.csv"
            path.write_bytes(csv_bytes(build_rows()[:-1]))

            result = self.invoke(path, "--require", "complete")

            self.assertNotEqual(0, result.returncode)
            self.assertEqual("", result.stdout)
            self.assertEqual(1, len(result.stderr.rstrip("\n").splitlines()))

    def test_cli_controls_missing_invalid_utf8_and_argument_failures(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            missing = directory / "missing.csv"
            invalid_utf8 = directory / "invalid.csv"
            invalid_utf8.write_bytes(b"\xff\xfe")
            cases = (
                self.invoke(missing, "--require", "complete"),
                self.invoke(invalid_utf8, "--require", "complete"),
                self.invoke(missing, "--require", "unknown"),
                self.invoke(missing, "--require", "complete", "--rounds", "nope"),
            )
            for result in cases:
                with self.subTest(stderr=result.stderr):
                    self.assertNotEqual(0, result.returncode)
                    self.assertEqual("", result.stdout)
                    self.assertEqual(1, len(result.stderr.rstrip("\n").splitlines()))

    def test_cli_controls_a_schema_valid_huge_noncontiguous_round(self):
        with tempfile.TemporaryDirectory() as directory_name:
            path = Path(directory_name) / "huge-round.csv"
            rows = build_rows(10, phases=("a",))
            rows[90]["measured_round"] = str(10 ** 30)
            rows[90]["schedule_cycle"] = str(10 ** 29)
            path.write_bytes(csv_bytes(rows))

            result = self.invoke(path, "--require", "phase-a")

            self.assertEqual(1, result.returncode)
            self.assertEqual("", result.stdout)
            self.assertEqual(1, len(result.stderr.rstrip("\n").splitlines()))
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
