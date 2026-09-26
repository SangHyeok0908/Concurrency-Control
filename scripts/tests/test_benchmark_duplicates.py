import csv
from collections import Counter
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import benchmark_duplicates


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts" / "benchmark_duplicates.py"


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
            benchmark_duplicates.parse_status_counts(log),
        )

    def test_rejects_a_status_sentinel_that_drops_a_bucket(self):
        log = (
            'DUPLICATE_HTTP_STATUS_COUNTS='
            '{"201":1,"409":199,"500":0,"503":0,"other":0}\n'
        )

        with self.assertRaisesRegex(ValueError, "status keys"):
            benchmark_duplicates.parse_status_counts(log)

    def test_rejects_multiple_status_sentinels(self):
        sentinel = (
            'DUPLICATE_HTTP_STATUS_COUNTS='
            '{"201":1,"409":199,"500":0,"503":0,"other":0,"no_response":0}\n'
        )

        with self.assertRaisesRegex(ValueError, "exactly one"):
            benchmark_duplicates.parse_status_counts(sentinel + sentinel)

    def test_parses_one_independent_gatling_classification_sentinel(self):
        log = 'DUPLICATE_GATLING_COUNTS={"ok":200,"ko":0}\n'

        self.assertEqual(
            {"ok": 200, "ko": 0},
            benchmark_duplicates.parse_gatling_counts(log),
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
        self.assertEqual([], benchmark_duplicates.validate_row(self.valid_row()))

    def test_accepts_unique_only_when_duplicates_are_conflicts(self):
        self.assertEqual([], benchmark_duplicates.validate_row(self.valid_row("unique")))

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

        errors = benchmark_duplicates.validate_row(row)

        self.assertTrue(any("confirmed" in error for error in errors), errors)
        self.assertTrue(any("pair_reservations" in error for error in errors), errors)
        self.assertTrue(any("seats_consumed" in error for error in errors), errors)

    def test_rejects_a_status_histogram_that_does_not_cover_every_request(self):
        row = self.valid_row()
        row["http_500"] = 198
        row["ko"] = 198

        errors = benchmark_duplicates.validate_row(row)

        self.assertTrue(any("HTTP status total" in error for error in errors), errors)
        self.assertTrue(any("Gatling total" in error for error in errors), errors)

    def test_rejects_wrong_remaining_and_duplicate_rows(self):
        row = self.valid_row()
        row["remaining"] = 198
        row["duplicate_rows"] = 1
        row["seats_consumed"] = 2

        errors = benchmark_duplicates.validate_row(row)

        self.assertTrue(any("remaining" in error for error in errors), errors)
        self.assertTrue(any("duplicate_rows" in error for error in errors), errors)
        self.assertTrue(any("seats_consumed" in error for error in errors), errors)

    def test_rejects_unique_when_a_duplicate_is_reported_as_500(self):
        row = self.valid_row("unique")
        row.update({"http_409": 198, "http_500": 1, "ok": 199, "ko": 1})

        errors = benchmark_duplicates.validate_row(row)

        self.assertTrue(any("unique HTTP 409" in error for error in errors), errors)
        self.assertTrue(any("unique HTTP 500" in error for error in errors), errors)


class DuplicateExecutionTest(unittest.TestCase):
    def execute(self, output, *, invalid_second=False, mismatch=False, fail_second=False):
        # Only HTTP and external processes are replaced; collection, validation,
        # CSV writing and campaign checking run as production code.
        identity = 0

        def external(arguments, cwd):
            nonlocal identity
            if "gatlingRun" in arguments:
                self.assertIn("com.interview.reservation.loadtest.DuplicateReservationSimulation", arguments)
                self.assertIn("-Dcapacity=200", arguments)
                self.assertIn("-Drequests=200", arguments)
                identity += 1
                if fail_second and identity == 2:
                    raise RuntimeError("Gatling failed")
                unique = "-Dstrategy=unique" in arguments
                counts = {"201": 1, "409": 199 if unique else 0,
                          "500": 0 if unique else 199, "503": 0,
                          "other": 0, "no_response": 0}
                gatling = {"ok": 200 if unique else 1, "ko": 0 if unique else 199}
                if mismatch:
                    gatling = {"ok": 199, "ko": 1}
                return ("DUPLICATE_HTTP_STATUS_COUNTS=" + json.dumps(counts) + "\n"
                        "DUPLICATE_GATLING_COUNTS=" + json.dumps(gatling) + "\n")
            self.assertEqual(["docker", "exec"], arguments[:2])
            sql = arguments[arguments.index("-e") + 1]
            if "MAX(id)" in sql:
                return f"{identity}\t{identity}"
            self.assertIn(f"s.id = {identity}", sql)
            self.assertIn(f"applicant_id = {identity}", sql)
            remaining = 198 if invalid_second and identity == 2 else 199
            return f"{remaining}\t1\t1\t0"

        args = benchmark_duplicates.parse_args(["run", "--out", str(output)])
        environment = {
            **benchmark_duplicates.capacity.EXPECTED_ENVIRONMENT,
            "optimisticMaxAttempts": 5,
        }
        with mock.patch.object(benchmark_duplicates.capacity, "fetch_json", return_value=environment), \
                mock.patch.object(benchmark_duplicates.capacity, "run_command", side_effect=external), \
                contextlib.redirect_stdout(io.StringIO()):
            benchmark_duplicates.run(args)

    def test_full_run_collects_25_rows_and_summarizes_real_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "runs.csv"
            self.execute(output)
            with output.open() as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(25, len(rows))
            self.assertEqual([], benchmark_duplicates.validate_campaign(rows, 5))
            self.assertEqual("199", rows[0]["http_500"])
            self.assertEqual("199", rows[1]["http_409"])
            self.assertEqual("200", rows[1]["ok"])
            self.assertTrue(all(row["seats_consumed"] == "1" for row in rows))
            self.assertEqual(1, output.read_text().count("schema_version,ts,"))

    def test_invalid_observation_is_saved_and_stops_the_run(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "runs.csv"
            with self.assertRaisesRegex(ValueError, "remaining"):
                self.execute(output, invalid_second=True)
            with output.open() as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(2, len(rows))
            self.assertEqual(["true", "false"], [row["invariant_pass"] for row in rows])
            self.assertTrue(benchmark_duplicates.validate_campaign(rows, 5))

    def test_http_and_gatling_disagreement_is_saved_as_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "runs.csv"
            with self.assertRaisesRegex(ValueError, "Gatling OK"):
                self.execute(output, mismatch=True)
            with output.open() as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual("199", row["ok"])
            self.assertEqual("false", row["invariant_pass"])

    def test_process_failure_preserves_previous_rows_without_success_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "runs.csv"
            with self.assertRaisesRegex(RuntimeError, "Gatling failed"):
                self.execute(output, fail_second=True)
            with output.open() as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(1, len(rows))
            self.assertTrue(benchmark_duplicates.validate_campaign(rows, 5))


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
            [], benchmark_duplicates.validate_campaign(self.complete_rows(), rounds=5)
        )

    def test_rejects_a_campaign_with_a_missing_run(self):
        errors = benchmark_duplicates.validate_campaign(self.complete_rows()[:-1], rounds=5)

        self.assertTrue(any("25 rows" in error for error in errors), errors)

    def test_rejects_a_campaign_that_does_not_rotate_positions(self):
        rows = self.complete_rows()
        for row in rows:
            row["strategy"] = self.STRATEGIES[row["position"] - 1]
            if row["strategy"] == "unique":
                row.update({"http_409": 199, "http_500": 0, "ok": 200, "ko": 0})
            else:
                row.update({"http_409": 0, "http_500": 199, "ok": 1, "ko": 199})

        errors = benchmark_duplicates.validate_campaign(rows, rounds=5)

        self.assertTrue(any("cyclic schedule" in error for error in errors), errors)

    def test_rejects_reused_seed_ids_and_duplicate_round_positions(self):
        rows = self.complete_rows()
        rows[1]["round"] = rows[0]["round"]
        rows[1]["position"] = rows[0]["position"]
        rows[1]["slot_id"] = rows[0]["slot_id"]
        rows[1]["applicant_id"] = rows[0]["applicant_id"]

        errors = benchmark_duplicates.validate_campaign(rows, rounds=5)

        self.assertTrue(any("duplicate round/position" in error for error in errors), errors)
        self.assertTrue(any("slot_id must be unique" in error for error in errors), errors)
        self.assertTrue(any("applicant_id must be unique" in error for error in errors), errors)

    def test_rejects_a_campaign_with_mixed_capacity_controls(self):
        rows = self.complete_rows()
        rows[-1]["capacity"] = 201
        rows[-1]["remaining"] = 200

        errors = benchmark_duplicates.validate_campaign(rows, rounds=5)

        self.assertTrue(any("same capacity and requests" in error for error in errors), errors)

    def test_summarize_outputs_correctness_totals_without_performance_ranking(self):
        with tempfile.NamedTemporaryFile("w", newline="", suffix=".csv") as handle:
            writer = csv.DictWriter(handle, fieldnames=benchmark_duplicates.CSV_FIELDS)
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


class DuplicateBenchmarkRunnerPlanTest(unittest.TestCase):

    def run_plan(self, *arguments):
        return subprocess.run(
            [sys.executable, str(CLI), "run", *arguments, "--print-plan"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_default_plan_has_25_cyclically_balanced_runs(self):
        result = self.run_plan()

        self.assertEqual(0, result.returncode, result.stderr)
        rows = list(csv.DictReader(io.StringIO(result.stdout)))
        self.assertEqual(25, len(rows))
        self.assertEqual(
            ["round", "position", "strategy", "capacity", "requests"],
            list(rows[0]),
        )
        self.assertTrue(all(row["capacity"] == "200" for row in rows))
        self.assertTrue(all(row["requests"] == "200" for row in rows))
        for strategy in benchmark_duplicates.STRATEGIES:
            positions = Counter(
                int(row["position"])
                for row in rows
                if row["strategy"] == strategy
            )
            self.assertEqual(Counter({1: 1, 2: 1, 3: 1, 4: 1, 5: 1}), positions)

    def test_one_strategy_one_round_is_available_for_live_smoke_testing(self):
        result = self.run_plan("--rounds", "1", "--strategies", "unique")

        self.assertEqual(0, result.returncode, result.stderr)
        rows = list(csv.DictReader(io.StringIO(result.stdout)))
        self.assertEqual(
            [{
                "round": "1",
                "position": "1",
                "strategy": "unique",
                "capacity": "200",
                "requests": "200",
            }],
            rows,
        )

    def test_rejects_capacity_smaller_than_the_request_count(self):
        result = self.run_plan("--capacity", "199", "--requests", "200")

        self.assertEqual(2, result.returncode)
        self.assertIn("capacity must be at least requests", result.stderr)

    def test_rejects_an_unknown_strategy(self):
        result = self.run_plan("--strategies", "unique,redis")

        self.assertEqual(2, result.returncode)
        self.assertIn("unknown strategy: redis", result.stderr)

    def test_rejects_a_non_five_round_public_campaign(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "duplicate-runs.csv"
            result = subprocess.run(
                [
                    sys.executable, str(CLI), "run",
                    "--rounds", "4",
                    "--out", str(output),
                    "--print-plan",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(2, result.returncode)
        self.assertIn("full campaign requires exactly 5 rounds", result.stderr)

    def test_live_run_refuses_to_overwrite_an_existing_output(self):
        with tempfile.NamedTemporaryFile(suffix=".csv") as output:
            result = subprocess.run(
                [
                    sys.executable, str(CLI), "run",
                    "--rounds", "1",
                    "--strategies", "unique",
                    "--out", output.name,
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("output already exists", result.stderr)



if __name__ == "__main__":
    unittest.main()
