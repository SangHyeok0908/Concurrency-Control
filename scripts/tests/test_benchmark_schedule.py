#!/usr/bin/env python3
import csv
import io
import pathlib
import subprocess
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from benchmark_schedule import (  # noqa: E402
    SCHEDULE_VERSION,
    WILLIAMS_BASE,
    build_schedule,
    write_tsv,
)


class BenchmarkScheduleTest(unittest.TestCase):
    def test_first_row_matches_the_approved_snapshot(self):
        entries = build_schedule(10)
        self.assertEqual([entry.treatment.treatment_id for entry in entries[:10]],
                         [0, 1, 9, 2, 8, 3, 7, 4, 6, 5])
        self.assertEqual(
            [(entry.treatment.strategy, entry.treatment.contention)
             for entry in entries[:10]],
            [("baseline", "low"), ("unique", "low"),
             ("optimistic", "extreme"), ("conditional", "low"),
             ("pessimistic", "extreme"), ("pessimistic", "low"),
             ("conditional", "extreme"), ("optimistic", "low"),
             ("unique", "extreme"), ("baseline", "extreme")],
        )
        self.assertEqual(entries[0].schedule_version, SCHEDULE_VERSION)
        self.assertEqual(entries[0].schedule_cycle, 1)
        self.assertEqual(entries[0].schedule_row, 1)
        self.assertEqual(entries[0].measured_round, 1)
        self.assertEqual(entries[0].position_in_round, 1)

    def test_ten_rounds_cover_every_treatment_and_position_once(self):
        entries = build_schedule(10)
        positions = {(entry.treatment.treatment_id, entry.position_in_round)
                     for entry in entries}
        self.assertEqual(len(positions), 100)
        self.assertEqual({entry.treatment.treatment_id for entry in entries},
                         set(range(10)))
        for treatment_id in range(10):
            self.assertEqual(
                {entry.position_in_round for entry in entries
                 if entry.treatment.treatment_id == treatment_id},
                set(range(1, 11)),
            )

    def test_every_directed_within_round_adjacent_pair_occurs_once(self):
        entries = build_schedule(10)
        pairs = []
        for start in range(0, len(entries), 10):
            row = entries[start:start + 10]
            pairs.extend((left.treatment.treatment_id, right.treatment.treatment_id)
                         for left, right in zip(row, row[1:]))
        self.assertEqual(len(pairs), 90)
        self.assertEqual(
            set(pairs),
            {(left, right) for left in range(10) for right in range(10)
             if left != right},
        )

    def test_strategy_and_contention_position_balance(self):
        entries = build_schedule(10)
        for strategy in ("baseline", "unique", "conditional", "pessimistic", "optimistic"):
            for position in range(1, 11):
                self.assertEqual(
                    sum(entry.treatment.strategy == strategy and
                        entry.position_in_round == position for entry in entries),
                    2,
                )
        for contention in ("low", "extreme"):
            for position in range(1, 11):
                self.assertEqual(
                    sum(entry.treatment.contention == contention and
                        entry.position_in_round == position for entry in entries),
                    5,
                )

    def test_twenty_rounds_form_two_identical_cycles_with_distinct_cycle_numbers(self):
        entries = build_schedule(20)
        first = [(entry.treatment.treatment_id, entry.position_in_round)
                 for entry in entries[:100]]
        second = [(entry.treatment.treatment_id, entry.position_in_round)
                  for entry in entries[100:]]
        self.assertEqual(first, second)
        self.assertEqual({entry.schedule_cycle for entry in entries[:100]}, {1})
        self.assertEqual({entry.schedule_cycle for entry in entries[100:]}, {2})
        self.assertEqual(
            [entries[index].schedule_row for index in range(100, 200, 10)],
            list(range(1, 11)),
        )

    def test_tsv_is_byte_deterministic(self):
        first = io.StringIO()
        second = io.StringIO()
        write_tsv(build_schedule(10), first)
        write_tsv(build_schedule(10), second)
        self.assertEqual(first.getvalue(), second.getvalue())
        self.assertNotIn("\r", first.getvalue())
        self.assertEqual(first.getvalue().count("\n"), 101)
        self.assertTrue(first.getvalue().endswith("\n"))
        self.assertEqual(first.getvalue().splitlines()[0],
                         "schedule_version\tschedule_cycle\tschedule_row\tmeasured_round\tposition_in_round\ttreatment_id\tstrategy\tcontention\tcapacity\tcontenders")
        rows = list(csv.DictReader(io.StringIO(first.getvalue()), delimiter="\t"))
        self.assertEqual(rows[0]["treatment_id"], "0")

    def test_rejects_invalid_round_counts(self):
        script = ROOT / "scripts" / "benchmark_schedule.py"
        for rounds in ("0", "-10", "1", "9", "11"):
            with self.subTest(rounds=rounds):
                result = subprocess.run(
                    [sys.executable, str(script), "--rounds", rounds],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
