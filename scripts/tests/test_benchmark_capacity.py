#!/usr/bin/env python3
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import benchmark_capacity as benchmark  # noqa: E402


SCRIPT = SCRIPTS / "benchmark_capacity.py"


def run_cli(*arguments):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def write_rows(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=benchmark.CSV_FIELDS,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def valid_environment(max_attempts=5):
    return {
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
        "optimisticMaxAttempts": max_attempts,
        "backoffBaseMillis": 10,
        "backoffMaxMillis": 200,
        "backoffPolicy": "exponential-jitter",
    }


def burst_sentinel(requests, start, end):
    return "BENCHMARK_BURST_METRICS=" + json.dumps({
        "requests": requests,
        "startEpochMs": start,
        "endEpochMs": end,
        "wallMs": end - start,
    }, separators=(",", ":"))


def complete_rows(rounds=10, phases=("a", "b"), campaign_id="campaign-001"):
    rows = []
    next_slot_id = 1
    for phase in phases:
        max_attempts = 5 if phase == "a" else 20
        environment_sha256 = ("a" if phase == "a" else "b") * 64
        for entry in benchmark.build_plan(rounds):
            treatment = entry.treatment
            start_ms = 1_800_000_000_000 + next_slot_id * 2_000
            wall_ms = 1_000
            confirmed = treatment.contenders
            row = {
                "schema_version": "2",
                "campaign_id": campaign_id,
                "phase": phase,
                "app_start_id": "%s-phase-%s" % (campaign_id, phase),
                "environment_sha256": environment_sha256,
                "schedule_version": benchmark.SCHEDULE_VERSION,
                "schedule_cycle": str((entry.round - 1) // 10 + 1),
                "schedule_row": str((entry.round - 1) % 10 + 1),
                "measured_round": str(entry.round),
                "position_in_round": str(entry.position),
                "treatment_id": str(treatment.id),
                "ts": "2026-09-25T00:00:00",
                "strategy": treatment.strategy,
                "optimistic_max_attempts": str(max_attempts),
                "contention": treatment.contention,
                "capacity": str(treatment.capacity),
                "contenders": str(treatment.contenders),
                "slot_id": str(next_slot_id),
                "requests": str(treatment.contenders),
                "ok": str(treatment.contenders),
                "ko": "0",
                "mean_ms": "10",
                "p95_ms": "20",
                "max_ms": "30",
                "burst_start_epoch_ms": str(start_ms),
                "burst_end_epoch_ms": str(start_ms + wall_ms),
                "burst_wall_ms": str(wall_ms),
                "tps": str(round(treatment.contenders * 1000 / wall_ms, 1)),
                "remaining": str(treatment.capacity - confirmed),
                "confirmed": str(confirmed),
                "overbooking": str(max(confirmed - treatment.capacity, 0)),
                "duplicates": "0",
                "retry_succeeded": "0",
                "retry_exhausted": "0",
                "version_conflicts": "0",
                "deadlocks": "0",
                "mean_attempts": "0.0",
                "run_status": "ok",
                "error_reason": "",
            }
            rows.append(row)
            next_slot_id += 1
    return rows


class PlanTest(unittest.TestCase):
    def test_ten_rounds_balance_every_treatment_at_every_position(self):
        plan = benchmark.build_plan(10)

        self.assertEqual(100, len(plan))
        positions = Counter((entry.treatment.id, entry.position) for entry in plan)
        self.assertEqual({1}, set(positions.values()))

    def test_rounds_must_be_a_positive_multiple_of_ten(self):
        for rounds in (0, -10, 1, 5, 11):
            with self.subTest(rounds=rounds):
                with self.assertRaisesRegex(ValueError, "positive multiple of 10"):
                    benchmark.build_plan(rounds)


class BurstParserTest(unittest.TestCase):
    def test_reads_exactly_one_complete_sentinel(self):
        metrics = benchmark.parse_burst_sentinel(
            'noise\nBENCHMARK_BURST_METRICS={"requests":200,'
            '"startEpochMs":1000,"endEpochMs":1250,"wallMs":250}\n',
            expected_requests=200,
        )

        self.assertEqual(250, metrics.wall_ms)
        self.assertEqual(800.0, metrics.tps)

    def test_rejects_missing_duplicate_or_inconsistent_sentinel(self):
        invalid = (
            "",
            'BENCHMARK_BURST_METRICS={"requests":199,"startEpochMs":1000,'
            '"endEpochMs":1250,"wallMs":250}',
            'BENCHMARK_BURST_METRICS={"requests":200,"startEpochMs":1000,'
            '"endEpochMs":1250,"wallMs":200}',
        )
        for text in invalid:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    benchmark.parse_burst_sentinel(text, 200)

    def test_rejects_added_missing_or_repeated_json_contract(self):
        valid = (
            'BENCHMARK_BURST_METRICS={"requests":200,"startEpochMs":1000,'
            '"endEpochMs":1250,"wallMs":250}'
        )
        invalid = (
            valid[:-1] + ',"unexpected":1}',
            'BENCHMARK_BURST_METRICS={"requests":200,"startEpochMs":1000,'
            '"endEpochMs":1250}',
            valid + "\n" + valid,
        )

        for text in invalid:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    benchmark.parse_burst_sentinel(text, 200)


class EnvironmentTest(unittest.TestCase):
    def test_environment_requires_the_phase_retry_cap(self):
        payload = valid_environment(max_attempts=5)

        self.assertIs(payload, benchmark.validate_environment(payload, 5))
        with self.assertRaisesRegex(ValueError, "optimisticMaxAttempts"):
            benchmark.validate_environment(payload, 20)

    def test_non_pool_mismatch_is_not_treated_as_a_retryable_pool_warmup(self):
        payload = valid_environment(max_attempts=5)
        payload["totalConnections"] = 99

        with self.assertRaisesRegex(ValueError, "optimisticMaxAttempts") as raised:
            benchmark.validate_environment(payload, 20)
        self.assertNotIsInstance(raised.exception, benchmark.EnvironmentNotReady)


class RetryMetricsTest(unittest.TestCase):
    def test_retry_distribution_must_match_success_count_and_mean(self):
        parsed = benchmark.parse_retry_snapshot({
            "succeededByAttempts": {"1": 1, "2": 1},
            "succeeded": 2,
            "retryExhausted": 3,
            "versionConflicts": 4,
            "deadlocks": 0,
            "meanAttemptsPerSuccess": 1.5,
        })

        self.assertEqual(2, parsed.succeeded)


class ReportParserTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.report = Path(directory.name)
        (self.report / "js").mkdir()
        self.entry = next(
            entry for entry in benchmark.build_plan(10)
            if entry.treatment.strategy == "baseline"
            and entry.treatment.contention == "extreme"
        )

    def write_stats_fixture(self, requests, ok, ko):
        treatment = self.entry.treatment
        label = "reserve [%s cap=%d cont=%d]" % (
            treatment.strategy,
            treatment.capacity,
            treatment.contenders,
        )
        stats = {
            "contents": {
                "request": {
                    "stats": {
                        "name": label,
                        "numberOfRequests": {
                            "total": str(requests),
                            "ok": str(ok),
                            "ko": str(ko),
                        },
                        "meanResponseTime": {"total": "86"},
                        "percentiles3": {"total": "132"},
                        "maxResponseTime": {"total": "136"},
                    }
                }
            }
        }
        source = "var stats = " + json.dumps(stats) + ";\nfunction fillStats() {}\n"
        (self.report / "js" / "stats.js").write_text(source, encoding="utf-8")
        return self.report

    def test_report_counts_must_match_the_burst_sentinel(self):
        report = self.write_stats_fixture(requests=200, ok=198, ko=2)
        output = burst_sentinel(requests=199, start=1000, end=1250)

        with self.assertRaisesRegex(ValueError, "request count"):
            benchmark.load_report_stats(report, self.entry, output)


class CampaignValidationTest(unittest.TestCase):
    def test_accepts_exact_phase_a_and_complete_campaigns(self):
        benchmark.validate_rows(complete_rows(phases=("a",)), "phase-a", 10)
        benchmark.validate_rows(complete_rows(), "complete", 10)

    def test_rejects_core_completeness_and_equation_mutations(self):
        mutations = {
            "missing row": lambda rows: rows.pop(),
            "duplicate slot": lambda rows: rows[1].update(
                slot_id=rows[0]["slot_id"]
            ),
            "request equation": lambda rows: rows[0].update(ok="0", ko="0"),
            "overbooking equation": lambda rows: rows[0].update(overbooking="9"),
            "tps equation": lambda rows: rows[0].update(tps="999.9"),
            "failed row": lambda rows: rows[0].update(run_status="parse_failed"),
            "mixed environment": lambda rows: rows[1].update(
                environment_sha256="b" * 64
            ),
        }

        for label, mutate in mutations.items():
            with self.subTest(label=label):
                rows = complete_rows()
                mutate(rows)
                with self.assertRaises(ValueError):
                    benchmark.validate_rows(rows, "complete", 10)


class CliAndOutputBoundaryTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.output = Path(directory.name) / "campaign.csv"

    def test_print_plan_has_header_and_one_hundred_rows_without_external_access(self):
        result = run_cli(
            "--campaign-id", "plan", "--phase", "a",
            "--rounds", "10", "--print-plan",
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(101, len(result.stdout.splitlines()))

    def test_empty_campaign_id_is_rejected_before_phase_side_effects(self):
        with mock.patch.object(benchmark, "run_phase") as run_phase, \
                mock.patch.object(benchmark.sys, "stderr"):
            with self.assertRaises(SystemExit) as raised:
                benchmark.main([
                    "--campaign-id", "", "--phase", "a",
                    "--rounds", "10", "--out", str(self.output),
                ])

        self.assertEqual(2, raised.exception.code)
        run_phase.assert_not_called()
        self.assertFalse(self.output.exists())
        self.assertFalse(benchmark.environment_path(self.output, "a").exists())

    def test_phase_a_refuses_existing_output(self):
        self.output.write_text("existing", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "already exists"):
            benchmark.prepare_output(self.output, "a", "campaign", 10)

    def test_phase_b_rejects_incomplete_phase_a(self):
        write_rows(
            self.output,
            complete_rows(phases=("a",), campaign_id="campaign")[:-1],
        )

        with self.assertRaisesRegex(ValueError, "complete Phase A"):
            benchmark.prepare_output(self.output, "b", "campaign", 10)

    def test_phase_b_rejects_a_different_campaign_or_round_count(self):
        write_rows(
            self.output,
            complete_rows(phases=("a",), campaign_id="original"),
        )

        with self.assertRaisesRegex(ValueError, "campaign"):
            benchmark.prepare_output(self.output, "b", "different", 10)
        with self.assertRaisesRegex(ValueError, "round"):
            benchmark.prepare_output(self.output, "b", "original", 20)


class ExternalBoundaryTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_run_command_reports_only_a_one_line_error_with_the_last_output(self):
        with self.assertRaisesRegex(RuntimeError, "last output") as raised:
            benchmark.run_command(
                [
                    sys.executable,
                    "-c",
                    "print('first output'); print('last output'); raise SystemExit(7)",
                ],
                self.root,
            )

        self.assertNotIn("\n", str(raised.exception))

    def test_selects_exactly_one_new_complete_gatling_report(self):
        (self.root / "existing").mkdir()
        before = benchmark.snapshot_report_names(self.root)
        created = self.root / "created"
        (created / "js").mkdir(parents=True)
        (created / "js" / "stats.js").write_text("stats", encoding="utf-8")

        self.assertEqual(created, benchmark.select_new_report(self.root, before))

        (self.root / "also-created").mkdir()
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            benchmark.select_new_report(self.root, before)


class EnvironmentAndWarmupTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.output = Path(directory.name) / "campaign.csv"

    def test_environment_snapshot_is_create_only_canonical_json(self):
        payload = valid_environment(max_attempts=5)
        canonical = (
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
        with mock.patch.object(benchmark, "fetch_json", return_value=payload):
            actual_hash = benchmark.capture_environment(
                self.output,
                "a",
                "http://example.test",
                5,
                wait_seconds=0,
            )

        snapshot = benchmark.environment_path(self.output, "a")
        self.assertEqual(canonical, snapshot.read_bytes())
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), actual_hash)
        with mock.patch.object(benchmark, "fetch_json") as fetch:
            with self.assertRaisesRegex(FileExistsError, "environment"):
                benchmark.capture_environment(
                    self.output,
                    "a",
                    "http://example.test",
                    5,
                    wait_seconds=0,
                )
            fetch.assert_not_called()

    def test_warmup_runs_only_the_first_round_of_ten_treatments(self):
        observed = []
        plan = benchmark.build_plan(20)
        with mock.patch.object(
            benchmark,
            "run_warmup_entry",
            side_effect=lambda entry: observed.append(entry),
        ):
            benchmark.warm_up(plan)

        self.assertEqual(plan[:10], observed)


class MeasurementTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.report = Path(directory.name)
        (self.report / "js").mkdir()
        self.entry = next(
            entry for entry in benchmark.build_plan(10)
            if entry.treatment.strategy == "baseline"
            and entry.treatment.contention == "extreme"
        )
        treatment = self.entry.treatment
        label = "reserve [%s cap=%d cont=%d]" % (
            treatment.strategy,
            treatment.capacity,
            treatment.contenders,
        )
        root = {
            "contents": {
                "request": {
                    "stats": {
                        "name": label,
                        "numberOfRequests": {"total": "200", "ok": "198", "ko": "2"},
                        "meanResponseTime": {"total": "86"},
                        "percentiles3": {"total": "132"},
                        "maxResponseTime": {"total": "136"},
                    }
                }
            }
        }
        source = "var stats = " + json.dumps(root) + ";\nfunction fillStats() {}\n"
        (self.report / "js" / "stats.js").write_text(source, encoding="utf-8")
        self.retry_snapshot = {
            "succeededByAttempts": {},
            "succeeded": 0,
            "retryExhausted": 0,
            "versionConflicts": 0,
            "deadlocks": 0,
            "meanAttemptsPerSuccess": 0,
        }

    def measure(self, completed_requests=200):
        output = burst_sentinel(completed_requests, 1000, 1250)
        patches = (
            mock.patch.object(benchmark, "request_no_content"),
            mock.patch.object(benchmark, "_query_max_slot", side_effect=(10, 11)),
            mock.patch.object(benchmark, "snapshot_report_names", return_value={"old"}),
            mock.patch.object(benchmark, "run_command", return_value=output),
            mock.patch.object(benchmark, "select_new_report", return_value=self.report),
            mock.patch.object(benchmark, "mysql_query", return_value="-100\t101\t0"),
            mock.patch.object(benchmark, "fetch_json", return_value=self.retry_snapshot),
        )
        entered = []
        try:
            for patcher in patches:
                entered.append(patcher.start())
            return benchmark.measure_entry(
                self.entry,
                campaign_id="campaign",
                phase="a",
                environment_sha256="a" * 64,
            )
        finally:
            for patcher in reversed(patches):
                patcher.stop()

    def test_success_row_uses_burst_tps_and_database_overbooking(self):
        row = self.measure()

        self.assertEqual(benchmark.CSV_FIELDS, list(row))
        self.assertEqual("800.0", row["tps"])
        self.assertEqual("101", row["confirmed"])
        self.assertEqual("100", row["overbooking"])
        self.assertEqual("ok", row["run_status"])
        self.assertEqual("", row["error_reason"])

    def test_incomplete_burst_fails_before_a_success_row_is_built(self):
        with self.assertRaisesRegex(ValueError, "request count"):
            self.measure(completed_requests=199)


class RunPhaseTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.output = Path(directory.name) / "campaign.csv"
        self.args = argparse.Namespace(
            campaign_id="campaign",
            phase="a",
            rounds=10,
            out=self.output,
            print_plan=False,
        )

    def test_successful_phase_writes_and_validates_one_hundred_rows(self):
        rows = complete_rows(phases=("a",), campaign_id="campaign")
        with mock.patch.object(benchmark, "capture_environment", return_value="a" * 64), \
                mock.patch.object(benchmark, "warm_up"), \
                mock.patch.object(benchmark, "measure_entry", side_effect=rows):
            benchmark.run_phase(self.args)

        stored = benchmark.read_rows(self.output)
        self.assertEqual(100, len(stored))
        benchmark.validate_rows(stored, "phase-a", 10)

    def test_phase_b_appends_after_phase_a_with_or_without_trailing_newline(self):
        rows = complete_rows(campaign_id="campaign")
        phase_a_rows = rows[:100]
        phase_b_rows = rows[100:]

        for trailing_newline in (True, False):
            with self.subTest(trailing_newline=trailing_newline):
                output = self.output.with_name(
                    "campaign-%s.csv" % ("newline" if trailing_newline else "eof")
                )
                write_rows(output, phase_a_rows)
                if not trailing_newline:
                    output.write_bytes(output.read_bytes().removesuffix(b"\n"))
                args = argparse.Namespace(
                    campaign_id="campaign",
                    phase="b",
                    rounds=10,
                    out=output,
                    print_plan=False,
                )

                with mock.patch.object(
                    benchmark, "capture_environment", return_value="b" * 64
                ), mock.patch.object(benchmark, "warm_up"), mock.patch.object(
                    benchmark, "measure_entry", side_effect=phase_b_rows
                ):
                    benchmark.run_phase(args)

                stored = benchmark.read_rows(output)
                self.assertEqual(rows, stored)
                benchmark.validate_rows(stored, "complete", 10)

    def test_measurement_failure_leaves_only_success_rows_in_partial_csv(self):
        first = complete_rows(phases=("a",), campaign_id="campaign")[0]
        with mock.patch.object(benchmark, "capture_environment", return_value="a" * 64), \
                mock.patch.object(benchmark, "warm_up"), \
                mock.patch.object(
                    benchmark,
                    "measure_entry",
                    side_effect=(first, RuntimeError("controlled measurement failure")),
                ):
            with self.assertRaisesRegex(RuntimeError, "controlled measurement failure"):
                benchmark.run_phase(self.args)

        stored = benchmark.read_rows(self.output)
        self.assertEqual([first], stored)

    def test_invalid_phase_b_is_rejected_before_environment_access(self):
        self.args.phase = "b"
        with mock.patch.object(benchmark, "capture_environment") as capture:
            with self.assertRaisesRegex(ValueError, "complete Phase A"):
                benchmark.run_phase(self.args)
            capture.assert_not_called()

    def test_cli_runtime_error_is_one_stderr_line_without_traceback(self):
        self.output.write_text("existing", encoding="utf-8")

        result = run_cli(
            "--campaign-id", "campaign", "--phase", "a",
            "--rounds", "10", "--out", str(self.output),
        )

        self.assertEqual(1, result.returncode)
        self.assertEqual(1, len(result.stderr.splitlines()), result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
