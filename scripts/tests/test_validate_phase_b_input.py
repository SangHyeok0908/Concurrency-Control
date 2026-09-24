import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts" / "validate_phase_b_input.py"
FIELDS = [
    "ts", "phase", "round", "strategy", "optimistic_max_attempts", "contention",
    "capacity", "contenders", "slot_id", "requests", "ok", "ko", "mean_ms",
    "p95_ms", "max_ms", "tps", "remaining", "confirmed", "overbooking",
    "duplicates", "retry_succeeded", "retry_exhausted", "version_conflicts",
    "deadlocks", "mean_attempts",
]
STRATEGIES = ("baseline", "unique", "conditional", "pessimistic", "optimistic")
POINTS = (("low", "100", "120"), ("extreme", "1", "200"))


class ValidatePhaseBInputTest(unittest.TestCase):

    def phase_a_rows(self, rounds=1):
        rows = []
        slot_id = 1
        for round_number in range(1, rounds + 1):
            for contention, capacity, contenders in POINTS:
                for strategy in STRATEGIES:
                    row = dict.fromkeys(FIELDS, "0")
                    row.update({
                        "ts": "2026-09-24T00:00:00",
                        "phase": "a",
                        "round": str(round_number),
                        "strategy": strategy,
                        "optimistic_max_attempts": "5",
                        "contention": contention,
                        "capacity": capacity,
                        "contenders": contenders,
                        "slot_id": str(slot_id),
                        "requests": contenders,
                    })
                    rows.append(row)
                    slot_id += 1
        return rows

    def validate(self, rows, rounds=1, fields=FIELDS):
        with tempfile.NamedTemporaryFile("w", newline="", suffix=".csv") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            return subprocess.run(
                [sys.executable, str(VALIDATOR), handle.name, "--rounds", str(rounds)],
                text=True,
                capture_output=True,
                check=False,
            )

    def test_accepts_one_complete_phase_a(self):
        result = self.validate(self.phase_a_rows())

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Phase A input verified", result.stdout)

    def test_rejects_an_incomplete_phase_a(self):
        result = self.validate(self.phase_a_rows()[:-1])

        self.assertNotEqual(0, result.returncode)
        self.assertIn("expected 10 rows", result.stderr)

    def test_rejects_a_file_that_already_contains_phase_b(self):
        rows = self.phase_a_rows()
        rows[-1]["phase"] = "b"

        result = self.validate(rows)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("unexpected benchmark row", result.stderr)


if __name__ == "__main__":
    unittest.main()
