import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import duplicate_benchmark


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts" / "duplicate_benchmark.py"


class DuplicateStatusParsingTest(unittest.TestCase):

    def test_parses_exactly_one_complete_status_sentinel(self):
        log = """Gatling output
DUPLICATE_HTTP_STATUS_COUNTS={"201":1,"409":199,"500":0,"503":0,"other":0,"no_response":0}
BUILD SUCCESSFUL
"""

        self.assertEqual(
            {
                "201": 1,
                "409": 199,
                "500": 0,
                "503": 0,
                "other": 0,
                "no_response": 0,
            },
            duplicate_benchmark.parse_status_counts(log),
        )

    def test_rejects_a_status_sentinel_that_drops_a_bucket(self):
        log = (
            'DUPLICATE_HTTP_STATUS_COUNTS='
            '{"201":1,"409":199,"500":0,"503":0,"other":0}\n'
        )

        with self.assertRaisesRegex(ValueError, "status keys"):
            duplicate_benchmark.parse_status_counts(log)

    def test_rejects_multiple_status_sentinels(self):
        sentinel = (
            'DUPLICATE_HTTP_STATUS_COUNTS='
            '{"201":1,"409":199,"500":0,"503":0,"other":0,"no_response":0}\n'
        )

        with self.assertRaisesRegex(ValueError, "exactly one"):
            duplicate_benchmark.parse_status_counts(sentinel + sentinel)

    def test_parses_one_independent_gatling_classification_sentinel(self):
        log = 'DUPLICATE_GATLING_COUNTS={"ok":200,"ko":0}\n'

        self.assertEqual(
            {"ok": 200, "ko": 0},
            duplicate_benchmark.parse_gatling_counts(log),
        )


class DuplicateRunValidationTest(unittest.TestCase):

    def valid_row(self, strategy="baseline"):
        status = {
            "http_201": 1,
            "http_409": 0,
            "http_500": 199,
            "http_503": 0,
            "http_other": 0,
            "http_no_response": 0,
            "ok": 1,
            "ko": 199,
        }
        if strategy == "unique":
            status.update({
                "http_409": 199,
                "http_500": 0,
                "ok": 200,
                "ko": 0,
            })
        return {
            "schema_version": 1,
            "ts": "2026-09-25T00:00:00",
            "round": 1,
            "position": 1,
            "strategy": strategy,
            "workload": "same-applicant-slot",
            "capacity": 200,
            "requests": 200,
            "slot_id": 1,
            "applicant_id": 1,
            **status,
            "remaining": 199,
            "confirmed": 1,
            "pair_reservations": 1,
            "duplicate_rows": 0,
            "seats_consumed": 1,
            "invariant_pass": True,
        }

    def test_accepts_a_complete_database_safe_run(self):
        self.assertEqual([], duplicate_benchmark.validate_row(self.valid_row()))

    def test_accepts_unique_only_when_duplicates_are_conflicts(self):
        self.assertEqual([], duplicate_benchmark.validate_row(self.valid_row("unique")))

    def test_rejects_zero_reservations_even_when_duplicate_rows_are_zero(self):
        row = self.valid_row()
        row.update({
            "http_201": 0,
            "http_500": 200,
            "ok": 0,
            "ko": 200,
            "remaining": 200,
            "confirmed": 0,
            "pair_reservations": 0,
            "seats_consumed": 0,
        })

        errors = duplicate_benchmark.validate_row(row)

        self.assertTrue(any("confirmed" in error for error in errors), errors)
        self.assertTrue(any("pair_reservations" in error for error in errors), errors)
        self.assertTrue(any("seats_consumed" in error for error in errors), errors)

    def test_rejects_a_status_histogram_that_does_not_cover_every_request(self):
        row = self.valid_row()
        row["http_500"] = 198
        row["ko"] = 198

        errors = duplicate_benchmark.validate_row(row)

        self.assertTrue(any("HTTP status total" in error for error in errors), errors)
        self.assertTrue(any("Gatling total" in error for error in errors), errors)

    def test_rejects_wrong_remaining_and_duplicate_rows(self):
        row = self.valid_row()
        row["remaining"] = 198
        row["duplicate_rows"] = 1
        row["seats_consumed"] = 2

        errors = duplicate_benchmark.validate_row(row)

        self.assertTrue(any("remaining" in error for error in errors), errors)
        self.assertTrue(any("duplicate_rows" in error for error in errors), errors)
        self.assertTrue(any("seats_consumed" in error for error in errors), errors)

    def test_rejects_unique_when_a_duplicate_is_reported_as_500(self):
        row = self.valid_row("unique")
        row.update({"http_409": 198, "http_500": 1, "ok": 199, "ko": 1})

        errors = duplicate_benchmark.validate_row(row)

        self.assertTrue(any("unique HTTP 409" in error for error in errors), errors)
        self.assertTrue(any("unique HTTP 500" in error for error in errors), errors)


class DuplicateResultRecordingTest(unittest.TestCase):

    def run_record(
            self,
            output,
            log,
            strategy="unique",
            remaining=199,
            mode="create",
            round_number=1,
            position=1,
            slot_id=11,
            applicant_id=21):
        return subprocess.run(
            [
                sys.executable,
                str(CLI),
                "record",
                str(output),
                "--mode", mode,
                "--timestamp", "2026-09-25T00:00:00",
                "--round", str(round_number),
                "--position", str(position),
                "--strategy", strategy,
                "--capacity", "200",
                "--requests", "200",
                "--slot-id", str(slot_id),
                "--applicant-id", str(applicant_id),
                "--gatling-log", str(log),
                "--remaining", str(remaining),
                "--confirmed", "1",
                "--pair-reservations", "1",
                "--duplicate-rows", "0",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_record_writes_one_valid_unique_row_with_observed_gatling_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "runs.csv"
            log = root / "gatling.log"
            log.write_text(
                'DUPLICATE_HTTP_STATUS_COUNTS='
                '{"201":1,"409":199,"500":0,"503":0,"other":0,"no_response":0}\n'
                'DUPLICATE_GATLING_COUNTS={"ok":200,"ko":0}\n'
            )

            result = self.run_record(output, log)

            self.assertEqual(0, result.returncode, result.stderr)
            with output.open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(1, len(rows))
            self.assertEqual("200", rows[0]["ok"])
            self.assertEqual("0", rows[0]["ko"])
            self.assertEqual("1", rows[0]["seats_consumed"])
            self.assertEqual("true", rows[0]["invariant_pass"])

    def test_append_preserves_existing_rows_and_writes_one_header(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "runs.csv"
            log = root / "gatling.log"
            log.write_text(
                'DUPLICATE_HTTP_STATUS_COUNTS='
                '{"201":1,"409":199,"500":0,"503":0,"other":0,"no_response":0}\n'
                'DUPLICATE_GATLING_COUNTS={"ok":200,"ko":0}\n'
            )
            first = self.run_record(output, log)
            log.write_text(
                'DUPLICATE_HTTP_STATUS_COUNTS='
                '{"201":1,"409":0,"500":199,"503":0,"other":0,"no_response":0}\n'
                'DUPLICATE_GATLING_COUNTS={"ok":1,"ko":199}\n'
            )
            second = self.run_record(
                output,
                log,
                strategy="baseline",
                mode="append",
                position=2,
                slot_id=12,
                applicant_id=22,
            )

            self.assertEqual(0, first.returncode, first.stderr)
            self.assertEqual(0, second.returncode, second.stderr)
            content = output.read_text()
            self.assertEqual(1, content.count("schema_version,ts,"))
            with output.open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(["unique", "baseline"], [row["strategy"] for row in rows])

    def test_record_preserves_an_invalid_observation_before_failing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "runs.csv"
            log = root / "gatling.log"
            log.write_text(
                'DUPLICATE_HTTP_STATUS_COUNTS='
                '{"201":1,"409":199,"500":0,"503":0,"other":0,"no_response":0}\n'
                'DUPLICATE_GATLING_COUNTS={"ok":200,"ko":0}\n'
            )

            result = self.run_record(output, log, remaining=198)

            self.assertNotEqual(0, result.returncode)
            with output.open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(1, len(rows))
            self.assertEqual("false", rows[0]["invariant_pass"])
            self.assertIn("remaining", result.stderr)

    def test_record_rejects_disagreement_with_gatling_classification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "runs.csv"
            log = root / "gatling.log"
            log.write_text(
                'DUPLICATE_HTTP_STATUS_COUNTS='
                '{"201":1,"409":199,"500":0,"503":0,"other":0,"no_response":0}\n'
                'DUPLICATE_GATLING_COUNTS={"ok":199,"ko":1}\n'
            )

            result = self.run_record(output, log)

            self.assertNotEqual(0, result.returncode)
            with output.open(newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual("199", row["ok"])
            self.assertEqual("1", row["ko"])
            self.assertEqual("false", row["invariant_pass"])
            self.assertIn("Gatling OK", result.stderr)

    def test_atomic_writer_leaves_original_file_when_replace_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "runs.csv"
            output.write_text("original\n")
            row = DuplicateRunValidationTest().valid_row("unique")

            with mock.patch.object(
                    duplicate_benchmark.os,
                    "replace",
                    side_effect=OSError("injected replace failure")):
                with self.assertRaisesRegex(OSError, "injected replace failure"):
                    duplicate_benchmark._write_rows_atomic(output, [row])

            self.assertEqual("original\n", output.read_text())
            self.assertEqual([output], list(Path(directory).iterdir()))


class DuplicateCampaignValidationTest(unittest.TestCase):

    STRATEGIES = (
        "baseline", "unique", "conditional", "pessimistic", "optimistic"
    )

    def complete_rows(self):
        rows = []
        identity = 1
        base = DuplicateRunValidationTest().valid_row()
        for round_number in range(1, 6):
            for position in range(1, 6):
                strategy = self.STRATEGIES[(round_number + position - 2) % 5]
                row = dict(base)
                row.update({
                    "round": round_number,
                    "position": position,
                    "strategy": strategy,
                    "slot_id": identity,
                    "applicant_id": identity,
                    "invariant_pass": True,
                })
                if strategy == "unique":
                    row.update({
                        "http_409": 199,
                        "http_500": 0,
                        "ok": 200,
                        "ko": 0,
                    })
                rows.append(row)
                identity += 1
        return rows

    def test_accepts_one_complete_balanced_five_round_campaign(self):
        self.assertEqual(
            [], duplicate_benchmark.validate_campaign(self.complete_rows(), rounds=5)
        )

    def test_rejects_a_campaign_with_a_missing_run(self):
        errors = duplicate_benchmark.validate_campaign(self.complete_rows()[:-1], rounds=5)

        self.assertTrue(any("25 rows" in error for error in errors), errors)

    def test_rejects_a_campaign_that_does_not_rotate_positions(self):
        rows = self.complete_rows()
        for row in rows:
            row["strategy"] = self.STRATEGIES[row["position"] - 1]
            if row["strategy"] == "unique":
                row.update({"http_409": 199, "http_500": 0, "ok": 200, "ko": 0})
            else:
                row.update({"http_409": 0, "http_500": 199, "ok": 1, "ko": 199})

        errors = duplicate_benchmark.validate_campaign(rows, rounds=5)

        self.assertTrue(any("cyclic schedule" in error for error in errors), errors)

    def test_rejects_reused_seed_ids_and_duplicate_round_positions(self):
        rows = self.complete_rows()
        rows[1]["round"] = rows[0]["round"]
        rows[1]["position"] = rows[0]["position"]
        rows[1]["slot_id"] = rows[0]["slot_id"]
        rows[1]["applicant_id"] = rows[0]["applicant_id"]

        errors = duplicate_benchmark.validate_campaign(rows, rounds=5)

        self.assertTrue(any("duplicate round/position" in error for error in errors), errors)
        self.assertTrue(any("slot_id must be unique" in error for error in errors), errors)
        self.assertTrue(any("applicant_id must be unique" in error for error in errors), errors)

    def test_rejects_a_campaign_with_mixed_capacity_controls(self):
        rows = self.complete_rows()
        rows[-1]["capacity"] = 201
        rows[-1]["remaining"] = 200

        errors = duplicate_benchmark.validate_campaign(rows, rounds=5)

        self.assertTrue(any("same capacity and requests" in error for error in errors), errors)

    def test_summarize_outputs_correctness_totals_without_performance_ranking(self):
        with tempfile.NamedTemporaryFile("w", newline="", suffix=".csv") as handle:
            writer = csv.DictWriter(handle, fieldnames=duplicate_benchmark.CSV_FIELDS)
            writer.writeheader()
            for row in self.complete_rows():
                serialised = dict(row)
                serialised["invariant_pass"] = "true"
                writer.writerow(serialised)
            handle.flush()

            result = subprocess.run(
                [sys.executable, str(CLI), "summarize", handle.name, "--rounds", "5"],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(
            "| unique | 5 | 5 | 995 | 0 | 0 | 0 | 0 | PASS |",
            result.stdout,
        )
        self.assertNotIn("TPS", result.stdout)
        self.assertNotIn("평균 응답", result.stdout)


if __name__ == "__main__":
    unittest.main()
