#!/usr/bin/env python3
import csv
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import benchmark_capacity as benchmark  # noqa: E402


SUMMARIZER = SCRIPTS / "summarize_benchmark.py"
CHECKED_IN_CAMPAIGN = ROOT / "docs" / "benchmark" / "raw-runs-v2.csv"


def run_summary(path, *arguments):
    return subprocess.run(
        [sys.executable, str(SUMMARIZER), str(path), *arguments],
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


class BenchmarkSummaryTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.temporary = Path(directory.name)

    def summarize_temp(self, rows):
        path = self.temporary / "campaign.csv"
        write_rows(path, rows)
        return run_summary(path)

    def test_checked_in_campaign_is_complete_and_renders_phase_tables(self):
        result = run_summary(CHECKED_IN_CAMPAIGN)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Phase A", result.stdout)
        self.assertIn("Phase B", result.stdout)
        self.assertIn("② vs ④", result.stdout)
        self.assertIn("⑤ 낙관적 락", result.stdout)
        self.assertIn("| n |", result.stdout)
        self.assertIn("1688.05 <sub>(1398.6–1851.9)</sub>", result.stdout)
        self.assertIn("563.4 <sub>(526.3–594.1)</sub>", result.stdout)
        self.assertNotIn("000000000000", result.stdout)

    def test_capacity_summary_does_not_present_unexercised_duplicates_as_evidence(self):
        result = run_summary(CHECKED_IN_CAMPAIGN)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("중복 최댓값", result.stdout)
        self.assertIn("서로 다른 지원자의 정원 경쟁", result.stdout)
        self.assertIn("duplicate-runs.csv", result.stdout)

    def test_incomplete_campaign_is_rejected_without_partial_summary(self):
        rows = benchmark.read_rows(CHECKED_IN_CAMPAIGN)[:-1]

        result = self.summarize_temp(rows)

        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("Phase A", result.stdout)

    def test_equal_range_and_conditional_pessimistic_positions_are_rendered(self):
        rows = benchmark.read_rows(CHECKED_IN_CAMPAIGN)
        conditional = None
        pessimistic = None
        for row in rows:
            if (row["phase"], row["strategy"], row["contention"]) == (
                "a", "baseline", "low",
            ):
                row["mean_ms"] = "77"
            if (
                row["phase"] == "a"
                and row["contention"] == "low"
                and row["measured_round"] == "1"
            ):
                if row["strategy"] == "conditional":
                    row["mean_ms"] = "111"
                    conditional = row
                elif row["strategy"] == "pessimistic":
                    row["mean_ms"] = "222"
                    pessimistic = row

        result = self.summarize_temp(rows)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("77 <sub>(77–77)</sub>", result.stdout)
        self.assertIsNotNone(conditional)
        self.assertIsNotNone(pessimistic)
        expected_pair = "| A | 낮음 | 1 | %s | 111 | %s | 222 | -111 |" % (
            conditional["position_in_round"],
            pessimistic["position_in_round"],
        )
        self.assertIn(expected_pair, result.stdout)

    def test_legacy_mode_option_is_removed(self):
        result = run_summary(CHECKED_IN_CAMPAIGN, "--legacy-v1")

        self.assertNotEqual(0, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("unrecognized arguments", result.stderr)


if __name__ == "__main__":
    unittest.main()
