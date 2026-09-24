import json
import math
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PARSER = ROOT / "scripts" / "parse_optimistic_metrics.py"


class ParseOptimisticMetricsTest(unittest.TestCase):

    def parse(self, payload):
        return subprocess.run(
            [sys.executable, str(PARSER)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

    def test_outputs_the_csv_fields_used_by_the_benchmark(self):
        result = self.parse({
            "succeededByAttempts": {"1": 1, "2": 1},
            "succeeded": 2,
            "retryExhausted": 2,
            "versionConflicts": 3,
            "deadlocks": 4,
            "meanAttemptsPerSuccess": 1.5,
        })

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("2,2,3,4,1.5", result.stdout.strip())

    def test_rejects_an_incomplete_snapshot(self):
        result = self.parse({"succeeded": 1})

        self.assertNotEqual(0, result.returncode)
        self.assertIn("missing optimistic metrics fields", result.stderr)

    def test_rejects_negative_boolean_and_non_finite_values(self):
        valid = {
            "succeededByAttempts": {"1": 1},
            "succeeded": 1,
            "retryExhausted": 2,
            "versionConflicts": 3,
            "deadlocks": 4,
            "meanAttemptsPerSuccess": 1.0,
        }
        corruptions = (
            ("negative count", {"retryExhausted": -1}),
            ("boolean count", {"deadlocks": False}),
            ("not-a-number mean", {"meanAttemptsPerSuccess": math.nan}),
        )

        for label, changes in corruptions:
            with self.subTest(label=label):
                result = self.parse(valid | changes)
                self.assertNotEqual(0, result.returncode)

    def test_rejects_an_inconsistent_success_distribution(self):
        result = self.parse({
            "succeededByAttempts": {"1": 1, "2": 1},
            "succeeded": 1,
            "retryExhausted": 0,
            "versionConflicts": 0,
            "deadlocks": 0,
            "meanAttemptsPerSuccess": 1.5,
        })

        self.assertNotEqual(0, result.returncode)
        self.assertIn("distribution total", result.stderr)

    def test_rejects_an_invalid_attempt_bucket(self):
        result = self.parse({
            "succeededByAttempts": {"0": 1},
            "succeeded": 1,
            "retryExhausted": 0,
            "versionConflicts": 0,
            "deadlocks": 0,
            "meanAttemptsPerSuccess": 0,
        })

        self.assertNotEqual(0, result.returncode)
        self.assertIn("attempt buckets", result.stderr)


if __name__ == "__main__":
    unittest.main()
