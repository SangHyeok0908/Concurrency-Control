import csv
import io
import subprocess
import tempfile
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "benchmark-duplicates.sh"
STRATEGIES = ("baseline", "unique", "conditional", "pessimistic", "optimistic")


class DuplicateBenchmarkRunnerPlanTest(unittest.TestCase):

    def run_plan(self, *arguments):
        return subprocess.run(
            ["bash", str(RUNNER), *arguments, "--print-plan"],
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
        for strategy in STRATEGIES:
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
                    "bash", str(RUNNER),
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
                    "bash", str(RUNNER),
                    "--rounds", "1",
                    "--strategies", "unique",
                    "--out", output.name,
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(2, result.returncode)
        self.assertIn("output already exists", result.stderr)


if __name__ == "__main__":
    unittest.main()
