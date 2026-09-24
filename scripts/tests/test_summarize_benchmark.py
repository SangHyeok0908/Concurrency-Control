import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SUMMARIZER = ROOT / "scripts" / "summarize_benchmark.py"
FIELDS = [
    "ts", "phase", "round", "strategy", "optimistic_max_attempts", "contention",
    "capacity", "contenders", "slot_id", "requests", "ok", "ko", "mean_ms",
    "p95_ms", "max_ms", "tps", "remaining", "confirmed", "overbooking",
    "duplicates", "retry_succeeded", "retry_exhausted", "version_conflicts",
    "deadlocks", "mean_attempts",
]


class SummarizeBenchmarkTest(unittest.TestCase):

    def test_counts_an_equal_pair_as_a_tie(self):
        base = dict.fromkeys(FIELDS, "0")
        base.update({
            "phase": "a",
            "round": "1",
            "optimistic_max_attempts": "5",
            "contention": "low",
            "capacity": "100",
            "contenders": "120",
            "requests": "120",
            "ok": "120",
            "mean_ms": "136",
            "p95_ms": "190",
            "max_ms": "200",
            "tps": "600.0",
            "confirmed": "100",
        })
        rows = []
        for strategy in ("conditional", "pessimistic"):
            row = dict(base)
            row["strategy"] = strategy
            rows.append(row)

        with tempfile.NamedTemporaryFile("w", newline="", suffix=".csv") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            result = subprocess.run(
                [sys.executable, str(SUMMARIZER), handle.name],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("② 0승 / ④ 0승 / 동률 1", result.stdout)


if __name__ == "__main__":
    unittest.main()
