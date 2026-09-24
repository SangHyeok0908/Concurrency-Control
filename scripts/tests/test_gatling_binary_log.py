import importlib
import importlib.util
import struct
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
FIXTURE = Path(__file__).parent / "fixtures/gatling-3.13.5-baseline-cap1-cont200"
LABEL = "reserve [baseline cap=1 cont=200]"


def i32(value):
    return struct.pack(">i", value)


def string(value, coder=0):
    encoding = "latin-1" if coder == 0 else "utf-16-" + ("le" if sys.byteorder == "little" else "be")
    data = value.encode(encoding)
    return i32(len(data)) + (data + bytes([coder]) if data else b"")


def cached(index, value=None, coder=0):
    return i32(index) + (string(value, coder) if index >= 0 else b"")


def run(version="3.13.5", scenario_count=2, assertion_count=2, assertion_size=3):
    return (b"\x00" + string(version) + string("example.Simulation")
            + struct.pack(">q", 1700000000000) + string("실험", 1)
            + i32(scenario_count) + string("seed") + string("reserve")
            + i32(assertion_count)
            + i32(assertion_size) + b"\x00\xff\x80"
            + i32(2) + b"\x01\x04")


def request(name, start=10, end=110, ok=1, message=None, groups=()):
    return (b"\x01" + i32(len(groups)) + b"".join(groups) + name
            + i32(start) + i32(end) + bytes([ok])
            + (cached(0, "") if message is None else message))


def synthetic_records():
    # Group and Error introduce cache entries that later Request records reuse.
    return [
        run(),
        b"\x02" + i32(1) + b"\x01" + i32(0),
        b"\x03" + i32(1) + cached(1, "그룹", 1) + i32(0) + i32(120) + i32(100) + b"\x00",
        b"\x04" + cached(2, "échec") + i32(5),
        request(cached(3, "reserve [test cap=1 cont=2]"), message=cached(4, ""), groups=[cached(-1)]),
        request(cached(-3), start=50, end=150, ok=0, message=cached(-2)),
        request(cached(5, "seed"), start=0, end=1000, message=cached(-4)),
        request(cached(6, "reserve [test cap=1 cont=2] unrelated"), start=0, end=2000, message=cached(-4)),
        b"\x02" + i32(1) + b"\x00" + i32(2000),
    ]


class GatlingBinaryLogTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("gatling_binary_log"), "Gatling binary decoder is not implemented")
        self.decoder = importlib.import_module("gatling_binary_log")
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "simulation.log"

    def write(self, data):
        self.path.write_bytes(data)
        return self.path

    def test_real_fixture_decodes_header_and_exact_reserve_burst(self):
        header, events = self.decoder.read_request_events(FIXTURE / "simulation.log")
        self.assertEqual("3.13.5", header.version)
        self.assertEqual(1788514321464, header.run_start_epoch_ms)
        self.assertEqual("com.interview.reservation.loadtest.BaselineReservationSimulation", header.simulation_class)
        self.assertEqual("", header.description)
        self.assertEqual(401, len(events))
        metrics = self.decoder.burst_metrics(FIXTURE / "simulation.log", LABEL, expected_requests=200)
        self.assertEqual((200, 191, 9, 1788514323074, 1788514323238, 164, 1219.5),
                         (metrics.requests, metrics.ok, metrics.ko, metrics.burst_start_epoch_ms,
                          metrics.burst_end_epoch_ms, metrics.burst_wall_ms, metrics.tps))

    def test_all_records_global_cache_and_both_string_coders(self):
        self.write(b"".join(synthetic_records()))
        header, events = self.decoder.read_request_events(self.path)
        self.assertEqual("실험", header.description)
        self.assertEqual(4, len(events))
        self.assertEqual(("reserve [test cap=1 cont=2]", 1700000000050, 1700000000150, False, "échec"),
                         (events[1].name, events[1].start_epoch_ms, events[1].end_epoch_ms, events[1].ok, events[1].message))
        metrics = self.decoder.burst_metrics(self.path, "reserve [test cap=1 cont=2]", 2)
        # Both requests last 100 ms, but their combined wall interval is 140 ms.
        self.assertEqual((2, 1, 1, 1700000000010, 1700000000150, 140, 14.3),
                         (metrics.requests, metrics.ok, metrics.ko, metrics.burst_start_epoch_ms,
                          metrics.burst_end_epoch_ms, metrics.burst_wall_ms, metrics.tps))

    def test_two_length_prefixed_assertions_preserve_the_following_request(self):
        self.write(run() + request(cached(1, "reserve")))
        _, events = self.decoder.read_request_events(self.path)
        self.assertEqual(1, len(events))
        self.assertEqual(("reserve", 1700000000010, 1700000000110, True, ""),
                         (events[0].name, events[0].start_epoch_ms, events[0].end_epoch_ms,
                          events[0].ok, events[0].message))

    def test_negative_assertion_count_and_individual_length_are_rejected(self):
        for name, data in [
            ("assertion count", run(assertion_count=-1)),
            ("individual assertion length", run(assertion_size=-1)),
        ]:
            with self.subTest(name=name):
                self.write(data)
                with self.assertRaisesRegex(ValueError, "negative count or length"):
                    self.decoder.read_request_events(self.path)

    def test_every_mid_record_truncation_fails_but_record_boundaries_succeed(self):
        prefix = b""
        for record_index, record in enumerate(synthetic_records()):
            for cut in range(1, len(record)):
                with self.subTest(record=record_index, cut=cut):
                    self.write(prefix + record[:cut])
                    with self.assertRaises(ValueError):
                        self.decoder.read_request_events(self.path)
            prefix += record
            self.write(prefix)
            self.decoder.read_request_events(self.path)

    def test_corrupt_records_are_rejected(self):
        valid_request = request(cached(1, "reserve"))
        cases = {
            "empty": b"",
            "first record not run": valid_request,
            "unsupported version": run("3.14.0"),
            "unknown tag": run() + b"\x05",
            "second run": run() + run(),
            "unknown coder": b"\x00" + i32(1) + b"x\x02",
            "invalid utf16": b"\x00" + i32(1) + b"x\x01",
            "negative string length": b"\x00" + i32(-1),
            "negative scenario count": run(scenario_count=-1),
            "negative group count": run() + b"\x01" + i32(-1),
            "missing reference": run() + request(cached(-1)),
            "duplicate declaration": run() + valid_request + request(cached(1, "again")),
            "duplicate zero declaration": run() + valid_request + request(cached(-1)),
            "request boolean": run() + request(cached(1, "reserve"), ok=2),
            "user boolean": run() + b"\x02" + i32(0) + b"\x02" + i32(0),
            "group boolean": run() + b"\x03" + i32(0) + i32(0) + i32(1) + i32(1) + b"\x02",
            "end before start": run() + request(cached(1, "reserve"), start=20, end=10),
            "group end before start": run() + b"\x03" + i32(0) + i32(20) + i32(10) + i32(1) + b"\x01",
        }
        for name, data in cases.items():
            with self.subTest(name=name):
                self.write(data)
                with self.assertRaises(ValueError):
                    self.decoder.read_request_events(self.path)

    def test_no_matching_requests_zero_wall_and_count_mismatch_fail(self):
        for name, data, label, count in [
            ("no match", run() + request(cached(1, "seed")), "reserve", None),
            ("zero wall", run() + request(cached(1, "reserve"), start=10, end=10), "reserve", None),
            ("count mismatch", run() + request(cached(1, "reserve")), "reserve", 2),
        ]:
            with self.subTest(name=name):
                self.write(data)
                with self.assertRaises(ValueError):
                    self.decoder.burst_metrics(self.path, label, count)


if __name__ == "__main__":
    unittest.main()
