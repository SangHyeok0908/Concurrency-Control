import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_gatling_binary_log import FIXTURE, cached, request, run


PARSER = Path(__file__).resolve().parents[1] / "parse_gatling_report.py"


class ParseGatlingReportTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.report = Path(directory.name)
        (self.report / "js").mkdir()
        self.label = "reserve [test cap=1 cont=2]"
        self.stats = {
            "name": self.label,
            "numberOfRequests": {"total": "2", "ok": "1", "ko": "1"},
            "meanResponseTime": {"total": "100"},
            "percentiles3": {"total": "100"},
            "maxResponseTime": {"total": "100"},
        }
        (self.report / "simulation.log").write_bytes(
            run() + request(cached(1, self.label))
            + request(cached(-1), start=50, end=150, ok=0, message=cached(2, "failed"))
            + request(cached(3, "seed"), start=0, end=2000, message=cached(-2)))

    def parse(self, report=None, strategy="test", contenders="2"):
        if report is None:
            report = self.report
            root = {"contents": {"request": {"stats": self.stats}}}
            (report / "js/stats.js").write_text("var stats = " + json.dumps(root) + ";\nfunction fillStats() {}")
        return subprocess.run([sys.executable, str(PARSER), str(report), strategy, "1", contenders],
                              capture_output=True, text=True, check=False)

    def assert_rejected(self, result):
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertEqual(1, len(result.stderr.splitlines()), result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_real_report_produces_exact_csv(self):
        result = self.parse(FIXTURE, "baseline", "200")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stderr)
        self.assertEqual("200,191,9,86,132,136,1788514323074,1788514323238,164,1219.5\n", result.stdout)

    def test_tps_uses_wall_interval_and_stats_remain_authoritative_for_latency(self):
        self.stats["meanResponseTime"]["total"] = "72"
        self.stats["percentiles3"]["total"] = "93"
        result = self.parse()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("2,1,1,72,93,100,1700000000010,1700000000150,140,14.3\n", result.stdout)

    def test_count_ok_ko_disagreement_with_log_fails(self):
        for field in ("total", "ok", "ko"):
            with self.subTest(field=field):
                original = self.stats["numberOfRequests"][field]
                self.stats["numberOfRequests"][field] = "3"
                self.assert_rejected(self.parse())
                self.stats["numberOfRequests"][field] = original

    def test_expected_contender_count_is_checked_even_when_stats_match_log(self):
        self.label = "reserve [test cap=1 cont=3]"
        self.stats["name"] = self.label
        (self.report / "simulation.log").write_bytes(
            run() + request(cached(1, self.label))
            + request(cached(-1), start=50, end=150, ok=0, message=cached(2, "failed")))
        self.assert_rejected(self.parse(contenders="3"))

    def test_missing_or_corrupt_log_is_a_single_line_error(self):
        (self.report / "simulation.log").unlink()
        self.assert_rejected(self.parse())
        (self.report / "simulation.log").write_bytes(run() + b"\x01")
        self.assert_rejected(self.parse())

    def test_missing_or_malformed_stats_are_a_single_line_error(self):
        self.assert_rejected(self.parse(self.report))
        (self.report / "js/stats.js").write_text("not stats")
        self.assert_rejected(self.parse(self.report))

    def test_wrong_label_is_rejected(self):
        self.stats["name"] += " other"
        self.assert_rejected(self.parse())

    def test_missing_cli_arguments_are_a_single_line_error(self):
        result = subprocess.run([sys.executable, str(PARSER)], capture_output=True, text=True, check=False)
        self.assert_rejected(result)


if __name__ == "__main__":
    unittest.main()
