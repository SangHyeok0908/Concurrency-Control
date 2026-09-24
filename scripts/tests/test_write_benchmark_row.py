import copy
import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import benchmark_schema
import write_benchmark_row


WRITER = SCRIPTS / "write_benchmark_row.py"
EXPECTED_FIELDS = [
    "schema_version", "campaign_id", "phase", "app_start_id",
    "environment_sha256", "schedule_version", "schedule_cycle",
    "schedule_row", "measured_round", "position_in_round", "treatment_id",
    "ts", "strategy", "optimistic_max_attempts", "contention", "capacity",
    "contenders", "slot_id", "requests", "ok", "ko", "mean_ms", "p95_ms",
    "max_ms", "burst_start_epoch_ms", "burst_end_epoch_ms", "burst_wall_ms",
    "tps", "remaining", "confirmed", "overbooking", "duplicates",
    "retry_succeeded", "retry_exhausted", "version_conflicts", "deadlocks",
    "mean_attempts", "run_status", "error_reason",
]
EXPECTED_HEADER = (
    "schema_version,campaign_id,phase,app_start_id,environment_sha256,"
    "schedule_version,schedule_cycle,schedule_row,measured_round,"
    "position_in_round,treatment_id,ts,strategy,optimistic_max_attempts,"
    "contention,capacity,contenders,slot_id,requests,ok,ko,mean_ms,p95_ms,"
    "max_ms,burst_start_epoch_ms,burst_end_epoch_ms,burst_wall_ms,tps,"
    "remaining,confirmed,overbooking,duplicates,retry_succeeded,"
    "retry_exhausted,version_conflicts,deadlocks,mean_attempts,run_status,"
    "error_reason\n"
)
MEASUREMENT_FIELDS = EXPECTED_FIELDS[17:37]
REPORT_FIELDS = EXPECTED_FIELDS[18:28]
DB_FIELDS = EXPECTED_FIELDS[28:32]
RETRY_FIELDS = EXPECTED_FIELDS[32:37]
WILLIAMS_BASE = (0, 1, 9, 2, 8, 3, 7, 4, 6, 5)
TREATMENTS = (
    ("baseline", "low", 100, 120),
    ("unique", "low", 100, 120),
    ("conditional", "low", 100, 120),
    ("pessimistic", "low", 100, 120),
    ("optimistic", "low", 100, 120),
    ("baseline", "extreme", 1, 200),
    ("unique", "extreme", 1, 200),
    ("conditional", "extreme", 1, 200),
    ("pessimistic", "extreme", 1, 200),
    ("optimistic", "extreme", 1, 200),
)


def valid_payload():
    return {
        "schema_version": 2,
        "campaign_id": "campaign_2026.09-24",
        "phase": "a",
        "app_start_id": "campaign_2026.09-24-phase-a",
        "environment_sha256": "a" * 64,
        "schedule_version": "williams-10-v1",
        "schedule_cycle": 1,
        "schedule_row": 1,
        "measured_round": 1,
        "position_in_round": 1,
        "treatment_id": 0,
        "ts": "2026-09-24T10:20:30",
        "strategy": "baseline",
        "optimistic_max_attempts": 5,
        "contention": "low",
        "capacity": 100,
        "contenders": 120,
        "slot_id": 42,
        "requests": 120,
        "ok": 119,
        "ko": 1,
        "mean_ms": 10,
        "p95_ms": 20,
        "max_ms": 30,
        "burst_start_epoch_ms": 1000,
        "burst_end_epoch_ms": 1100,
        "burst_wall_ms": 100,
        "tps": 1200.0,
        "remaining": -19,
        "confirmed": 119,
        "overbooking": 19,
        "duplicates": 0,
        "retry_succeeded": 0,
        "retry_exhausted": 0,
        "version_conflicts": 0,
        "deadlocks": 0,
        "mean_attempts": 0.0,
        "run_status": "ok",
        "error_reason": "",
    }


def failure_payload(status, shape="empty"):
    payload = valid_payload()
    for field in MEASUREMENT_FIELDS:
        payload[field] = None
    payload["run_status"] = status
    payload["error_reason"] = "controlled failure"
    if shape in ("slot_report", "slot_report_db"):
        payload["slot_id"] = 42
        report = valid_payload()
        for field in REPORT_FIELDS:
            payload[field] = report[field]
    if shape == "slot_report_db":
        db_values = valid_payload()
        for field in DB_FIELDS:
            payload[field] = db_values[field]
    if shape == "slot":
        payload["slot_id"] = 42
    return payload


def csv_bytes(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=EXPECTED_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


class SchemaContractTest(unittest.TestCase):

    def test_constants_and_exact_39_field_order(self):
        self.assertEqual("2", benchmark_schema.SCHEMA_VERSION)
        self.assertEqual("williams-10-v1", benchmark_schema.SCHEDULE_VERSION)
        self.assertEqual(EXPECTED_FIELDS, benchmark_schema.FIELDS)
        self.assertEqual(39, len(benchmark_schema.FIELDS))
        self.assertEqual(
            (
                "ok", "seed_failed", "gatling_failed", "parse_failed",
                "db_read_failed", "retry_metrics_failed",
            ),
            benchmark_schema.RUN_STATUSES,
        )

    def test_normalizes_json_null_but_preserves_numeric_zero(self):
        payload = failure_payload("seed_failed")
        payload["optimistic_max_attempts"] = 5

        row = benchmark_schema.normalize_and_validate_row(payload)

        self.assertEqual("", row["slot_id"])
        self.assertEqual("5", row["optimistic_max_attempts"])
        payload = valid_payload()
        row = benchmark_schema.normalize_and_validate_row(payload)
        self.assertEqual(
            [
                "2", "campaign_2026.09-24", "a",
                "campaign_2026.09-24-phase-a", "a" * 64,
                "williams-10-v1", "1", "1", "1", "1", "0",
                "2026-09-24T10:20:30", "baseline", "5", "low",
                "100", "120", "42", "120", "119", "1", "10", "20",
                "30", "1000", "1100", "100", "1200.0", "-19", "119",
                "19", "0", "0", "0", "0", "0", "0.0", "ok", "",
            ],
            [row[field] for field in EXPECTED_FIELDS],
        )

    def test_omitted_optional_measurements_normalize_to_empty_fields(self):
        payload = failure_payload("seed_failed")
        for field in MEASUREMENT_FIELDS:
            del payload[field]
        del payload["error_reason"]
        payload["run_status"] = "ok"
        for field, value in valid_payload().items():
            if field in MEASUREMENT_FIELDS:
                payload[field] = value

        row = benchmark_schema.normalize_and_validate_row(payload)
        self.assertEqual("", row["error_reason"])

        payload = failure_payload("seed_failed")
        for field in MEASUREMENT_FIELDS:
            del payload[field]
        row = benchmark_schema.normalize_and_validate_row(payload)
        self.assertTrue(all(row[field] == "" for field in MEASUREMENT_FIELDS))

    def test_rejects_boolean_and_nested_or_container_scalar_values(self):
        for value in (True, False, [], {}, (1,), {1}):
            with self.subTest(value=value):
                payload = valid_payload()
                payload["requests"] = value
                with self.assertRaises(ValueError):
                    benchmark_schema.normalize_and_validate_row(payload)

    def test_accepts_every_approved_failure_shape(self):
        cases = (
            failure_payload("seed_failed"),
            failure_payload("gatling_failed"),
            failure_payload("parse_failed", "slot"),
            failure_payload("db_read_failed"),
            failure_payload("db_read_failed", "slot_report"),
            failure_payload("retry_metrics_failed"),
            failure_payload("retry_metrics_failed", "slot_report_db"),
        )
        for payload in cases:
            with self.subTest(status=payload["run_status"], slot=payload["slot_id"]):
                row = benchmark_schema.normalize_and_validate_row(payload)
                self.assertEqual(payload["run_status"], row["run_status"])

    def test_rejects_every_unapproved_status_shape(self):
        cases = []
        for status in ("seed_failed", "gatling_failed"):
            cases.append(failure_payload(status, "slot"))
        cases.extend((
            failure_payload("parse_failed"),
            failure_payload("parse_failed", "slot_report"),
            failure_payload("db_read_failed", "slot"),
            failure_payload("db_read_failed", "slot_report_db"),
            failure_payload("retry_metrics_failed", "slot"),
            failure_payload("retry_metrics_failed", "slot_report"),
        ))
        for payload in cases:
            with self.subTest(status=payload["run_status"], slot=payload["slot_id"]):
                with self.assertRaises(ValueError):
                    benchmark_schema.normalize_and_validate_row(payload)

    def test_rejects_partial_report_db_and_retry_groups(self):
        group_cases = (
            ("parse_failed", "slot", REPORT_FIELDS),
            ("db_read_failed", "slot_report", DB_FIELDS),
            ("retry_metrics_failed", "slot_report_db", RETRY_FIELDS),
        )
        values = valid_payload()
        for status, shape, group in group_cases:
            for field in group:
                with self.subTest(status=status, field=field):
                    payload = failure_payload(status, shape)
                    payload[field] = values[field]
                    with self.assertRaises(ValueError):
                        benchmark_schema.normalize_and_validate_row(payload)

        for group in (REPORT_FIELDS, DB_FIELDS, RETRY_FIELDS):
            for field in group:
                with self.subTest(status="ok", missing=field):
                    payload = valid_payload()
                    payload[field] = None
                    with self.assertRaises(ValueError):
                        benchmark_schema.normalize_and_validate_row(payload)

    def test_requires_exact_keys_known_status_and_nonliteral_null(self):
        cases = []
        missing = valid_payload()
        del missing["campaign_id"]
        cases.append(missing)
        extra = valid_payload()
        extra["unexpected"] = "value"
        cases.append(extra)
        unknown = valid_payload()
        unknown["run_status"] = "timeout"
        cases.append(unknown)
        literal_null = valid_payload()
        literal_null["duplicates"] = "null"
        cases.append(literal_null)
        for payload in cases:
            with self.subTest(keys=len(payload), status=payload.get("run_status")):
                with self.assertRaises(ValueError):
                    benchmark_schema.normalize_and_validate_row(payload)

    def test_validates_campaign_phase_environment_and_timestamp(self):
        mutations = (
            ("schema_version", 3),
            ("campaign_id", ""),
            ("campaign_id", "bad/id"),
            ("campaign_id", "bad id"),
            ("phase", "c"),
            ("app_start_id", "wrong-phase-a"),
            ("environment_sha256", "A" * 64),
            ("environment_sha256", "a" * 63),
            ("schedule_version", "williams-next"),
            ("ts", "2026-09-24"),
            ("ts", "2026/09/24 10:20:30"),
            ("ts", "2026-09-24T25:20:30"),
        )
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                payload = valid_payload()
                payload[field] = value
                with self.assertRaises(ValueError):
                    benchmark_schema.normalize_and_validate_row(payload)

    def test_phase_controls_the_process_retry_cap(self):
        payload = valid_payload()
        payload.update({
            "phase": "b",
            "app_start_id": payload["campaign_id"] + "-phase-b",
            "optimistic_max_attempts": 20,
        })
        self.assertEqual("20", benchmark_schema.normalize_and_validate_row(payload)["optimistic_max_attempts"])

        for phase, cap in (("a", 20), ("b", 5), ("b", 19)):
            with self.subTest(phase=phase, cap=cap):
                payload = valid_payload()
                payload.update({
                    "phase": phase,
                    "app_start_id": payload["campaign_id"] + "-phase-" + phase,
                    "optimistic_max_attempts": cap,
                })
                with self.assertRaises(ValueError):
                    benchmark_schema.normalize_and_validate_row(payload)

    def test_every_treatment_mapping_is_accepted_at_its_scheduled_position(self):
        for position, treatment_id in enumerate(WILLIAMS_BASE, start=1):
            strategy, contention, capacity, contenders = TREATMENTS[treatment_id]
            payload = valid_payload()
            payload.update({
                "position_in_round": position,
                "treatment_id": treatment_id,
                "strategy": strategy,
                "contention": contention,
                "capacity": capacity,
                "contenders": contenders,
                "requests": contenders,
                "ok": contenders,
                "ko": 0,
            })
            with self.subTest(position=position, treatment_id=treatment_id):
                row = benchmark_schema.normalize_and_validate_row(payload)
                self.assertEqual(str(treatment_id), row["treatment_id"])

    def test_schedule_numbers_and_treatment_are_derived_not_trusted(self):
        payload = valid_payload()
        payload.update({
            "schedule_cycle": 2,
            "schedule_row": 3,
            "measured_round": 13,
            "position_in_round": 4,
            "treatment_id": 4,
            "strategy": "optimistic",
            "contention": "low",
            "capacity": 100,
            "contenders": 120,
            "requests": 120,
            "ok": 120,
            "ko": 0,
        })
        benchmark_schema.normalize_and_validate_row(payload)

        zero_round = copy.deepcopy(payload)
        zero_round["measured_round"] = 0
        with self.assertRaises(ValueError):
            benchmark_schema.normalize_and_validate_row(zero_round)

        for field, value in (
            ("schedule_cycle", 1),
            ("schedule_row", 2),
            ("position_in_round", 0),
            ("position_in_round", 11),
            ("treatment_id", 3),
            ("strategy", "pessimistic"),
            ("contention", "extreme"),
            ("capacity", 1),
            ("contenders", 200),
        ):
            with self.subTest(field=field, value=value):
                broken = copy.deepcopy(payload)
                broken[field] = value
                with self.assertRaises(ValueError):
                    benchmark_schema.normalize_and_validate_row(broken)

    def test_numeric_domain_validation_allows_only_remaining_to_be_negative(self):
        integer_fields = (
            "schedule_cycle", "schedule_row", "measured_round", "position_in_round",
            "treatment_id", "optimistic_max_attempts", "capacity", "contenders",
            "slot_id", "requests", "ok", "ko", "mean_ms", "p95_ms", "max_ms",
            "burst_start_epoch_ms", "burst_end_epoch_ms", "burst_wall_ms",
            "confirmed", "overbooking", "duplicates", "retry_succeeded",
            "retry_exhausted", "version_conflicts", "deadlocks",
        )
        for field in integer_fields:
            with self.subTest(field=field):
                payload = valid_payload()
                payload[field] = -1
                with self.assertRaises(ValueError):
                    benchmark_schema.normalize_and_validate_row(payload)
        for field in ("tps", "mean_attempts"):
            with self.subTest(field=field):
                payload = valid_payload()
                payload[field] = -0.1
                with self.assertRaises(ValueError):
                    benchmark_schema.normalize_and_validate_row(payload)
        payload = valid_payload()
        payload["remaining"] = -999
        self.assertEqual("-999", benchmark_schema.normalize_and_validate_row(payload)["remaining"])
        payload = valid_payload()
        payload["slot_id"] = 0
        self.assertEqual("0", benchmark_schema.normalize_and_validate_row(payload)["slot_id"])
        payload["tps"] = "1.2e3"
        payload["mean_attempts"] = "-0.0"
        normalized = benchmark_schema.normalize_and_validate_row(payload)
        self.assertEqual("1.2e3", normalized["tps"])
        self.assertEqual("-0.0", normalized["mean_attempts"])
        for value in (1.5, "1.0"):
            with self.subTest(remaining=value):
                payload = valid_payload()
                payload["remaining"] = value
                with self.assertRaises(ValueError):
                    benchmark_schema.normalize_and_validate_row(payload)

    def test_rejects_nonfinite_numeric_values_and_spellings(self):
        for value in (
            float("nan"), float("inf"), float("-inf"),
            "NaN", "Infinity", "-Infinity", "nan", "inf",
        ):
            with self.subTest(value=value):
                payload = valid_payload()
                payload["tps"] = value
                with self.assertRaises(ValueError):
                    benchmark_schema.normalize_and_validate_row(payload)

    def test_validate_csv_row_requires_exact_string_shape(self):
        row = benchmark_schema.normalize_and_validate_row(valid_payload())
        cases = []
        missing = dict(row)
        del missing["error_reason"]
        cases.append(missing)
        extra = dict(row)
        extra["extra"] = ""
        cases.append(extra)
        nonstring_key = dict(row)
        nonstring_key[1] = nonstring_key.pop("error_reason")
        cases.append(nonstring_key)
        nonstring_value = dict(row)
        nonstring_value["duplicates"] = 0
        cases.append(nonstring_value)
        for candidate in cases:
            with self.subTest(keys=len(candidate)):
                with self.assertRaises(ValueError):
                    benchmark_schema.validate_csv_row(candidate)

    def test_error_reason_contract_and_csv_validation_without_json_conversion(self):
        success = valid_payload()
        success["error_reason"] = "unexpected"
        with self.assertRaises(ValueError):
            benchmark_schema.normalize_and_validate_row(success)

        failure = failure_payload("seed_failed")
        failure["error_reason"] = ""
        with self.assertRaises(ValueError):
            benchmark_schema.normalize_and_validate_row(failure)
        failure["error_reason"] = "   \t"
        with self.assertRaises(ValueError):
            benchmark_schema.normalize_and_validate_row(failure)

        for newline in ("\r", "\n", "\r\n"):
            with self.subTest(newline=repr(newline)):
                failure = failure_payload("seed_failed")
                failure["error_reason"] = "first" + newline + "second"
                with self.assertRaises(ValueError):
                    benchmark_schema.normalize_and_validate_row(failure)

        normalized = benchmark_schema.normalize_and_validate_row(valid_payload())
        self.assertEqual(normalized, benchmark_schema.validate_csv_row(normalized))
        not_csv = dict(normalized)
        not_csv["slot_id"] = None
        with self.assertRaises(ValueError):
            benchmark_schema.validate_csv_row(not_csv)


class AtomicWriterTest(unittest.TestCase):

    def normalized(self, payload=None):
        return benchmark_schema.normalize_and_validate_row(payload or valid_payload())

    def assert_only(self, directory, expected_names):
        self.assertEqual(sorted(expected_names), sorted(path.name for path in directory.iterdir()))

    def partial_writer_patch(self):
        real_dict_writer = csv.DictWriter

        def partial_writer(*args, **kwargs):
            delegate = real_dict_writer(*args, **kwargs)

            def fail_after_partial_row(rows):
                rows = list(rows)
                if rows:
                    delegate.writerow(rows[0])
                raise OSError("writer")

            delegate.writerows = fail_after_partial_row
            return delegate

        return mock.patch.object(write_benchmark_row.csv, "DictWriter", side_effect=partial_writer)

    def test_write_new_uses_exact_header_lf_and_exclusive_creation(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            path = directory / "raw-runs.csv"
            row = self.normalized()
            write_benchmark_row.write_new(path, row)

            data = path.read_bytes()
            self.assertTrue(data.startswith(EXPECTED_HEADER.encode("utf-8")))
            self.assertTrue(data.endswith(b"\n"))
            self.assertNotIn(b"\r", data)
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row], rows)

            original = path.read_bytes()
            with self.assertRaises(FileExistsError):
                write_benchmark_row.write_new(path, row)
            self.assertEqual(original, path.read_bytes())
            self.assert_only(directory, ["raw-runs.csv"])

    def test_csv_quotes_comma_and_quote_in_error_reason_exactly(self):
        with tempfile.TemporaryDirectory() as directory_name:
            path = Path(directory_name) / "raw-runs.csv"
            payload = failure_payload("seed_failed")
            payload["error_reason"] = 'seed, said "no"'
            write_benchmark_row.write_new(path, self.normalized(payload))

            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            self.assertEqual(EXPECTED_HEADER, lines[0])
            self.assertTrue(lines[1].endswith(',seed_failed,"seed, said ""no"""\n'))

    def test_append_validates_header_and_every_existing_row_then_replaces(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            path = directory / "raw-runs.csv"
            first = self.normalized()
            second_payload = failure_payload("parse_failed", "slot")
            second_payload["error_reason"] = "report missing"
            second = self.normalized(second_payload)
            write_benchmark_row.write_new(path, first)
            write_benchmark_row.append(path, second)

            self.assertEqual(csv_bytes([first, second]), path.read_bytes())
            self.assert_only(directory, ["raw-runs.csv"])

    def test_append_rejects_missing_file_bad_headers_and_bad_existing_rows(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            path = directory / "raw-runs.csv"
            with self.assertRaises(FileNotFoundError):
                write_benchmark_row.append(path, self.normalized())

            row = self.normalized()
            bad_headers = (
                EXPECTED_FIELDS[:-1],
                EXPECTED_FIELDS + ["extra"],
                EXPECTED_FIELDS[:-1] + [EXPECTED_FIELDS[-2]],
                [EXPECTED_FIELDS[1], EXPECTED_FIELDS[0]] + EXPECTED_FIELDS[2:],
            )
            for index, fields in enumerate(bad_headers):
                bad_path = directory / ("bad-%d.csv" % index)
                with bad_path.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
                    writer.writeheader()
                    writer.writerow(row)
                original = bad_path.read_bytes()
                with self.subTest(fields=fields):
                    with self.assertRaises(ValueError):
                        write_benchmark_row.append(bad_path, row)
                    self.assertEqual(original, bad_path.read_bytes())

            bad_row = dict(row)
            bad_row["campaign_id"] = "bad/id"
            valid_then_bad = directory / "bad-row.csv"
            valid_then_bad.write_bytes(csv_bytes([row, bad_row]))
            original = valid_then_bad.read_bytes()
            with self.assertRaises(ValueError):
                write_benchmark_row.append(valid_then_bad, row)
            self.assertEqual(original, valid_then_bad.read_bytes())

    def test_write_new_rejects_real_and_symbolic_destinations_without_clobber(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            target = directory / "target"
            target.write_bytes(b"target")
            dangling_target = directory / "missing"
            cases = (
                (directory / "real", None),
                (directory / "real-link", target),
                (directory / "dangling-link", dangling_target),
            )
            cases[0][0].write_bytes(b"existing")
            os.symlink(str(cases[1][1]), str(cases[1][0]))
            os.symlink(str(cases[2][1]), str(cases[2][0]))

            for path, link_target in cases:
                with self.subTest(path=path.name):
                    before = os.readlink(path) if path.is_symlink() else path.read_bytes()
                    with self.assertRaises(FileExistsError):
                        write_benchmark_row.write_new(path, self.normalized())
                    after = os.readlink(path) if path.is_symlink() else path.read_bytes()
                    self.assertEqual(before, after)
            self.assertEqual(b"target", target.read_bytes())
            self.assertFalse(dangling_target.exists())
            self.assert_only(directory, ["real", "real-link", "dangling-link", "target"])

    def test_write_new_rejects_a_preexisting_destination_directory(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            path = directory / "raw-runs.csv"
            path.mkdir()
            with self.assertRaises(FileExistsError):
                write_benchmark_row.write_new(path, self.normalized())
            self.assertTrue(path.is_dir())
            self.assert_only(directory, ["raw-runs.csv"])

    def test_write_new_loses_late_publication_race_without_clobber(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            path = directory / "raw-runs.csv"
            real_link = os.link

            def racing_link(source, destination):
                Path(destination).write_bytes(b"winner")
                real_link(source, destination)

            with mock.patch.object(write_benchmark_row.os, "link", side_effect=racing_link):
                with self.assertRaises(FileExistsError):
                    write_benchmark_row.write_new(path, self.normalized())

            self.assertEqual(b"winner", path.read_bytes())
            self.assert_only(directory, ["raw-runs.csv"])

    def test_write_new_never_reopens_the_exclusive_temp_name_through_a_symlink(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            path = directory / "raw-runs.csv"
            victim = directory / "victim.txt"
            victim.write_bytes(b"must remain unchanged")
            real_close = os.close
            swapped_names = []

            def swap_temp_name_after_close(descriptor):
                real_close(descriptor)
                candidates = list(directory.glob(".raw-runs.csv.*.tmp"))
                self.assertEqual(1, len(candidates))
                temporary = candidates[0]
                temporary.unlink()
                os.symlink(str(victim), str(temporary))
                swapped_names.append(temporary)

            with mock.patch.object(
                write_benchmark_row.os,
                "close",
                side_effect=swap_temp_name_after_close,
            ):
                write_benchmark_row.write_new(path, self.normalized())

            self.assertEqual(b"must remain unchanged", victim.read_bytes())
            self.assertEqual([], swapped_names, "the mkstemp descriptor must not be closed before writing")
            self.assertTrue(path.is_file())
            self.assertFalse(path.is_symlink())
            self.assertFalse(os.path.samefile(path, victim))
            self.assert_only(directory, ["raw-runs.csv", "victim.txt"])

    def test_fdopen_setup_failure_closes_descriptor_cleans_temp_and_keeps_primary_error(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            path = directory / "raw-runs.csv"
            real_mkstemp = tempfile.mkstemp
            real_close = os.close
            created_descriptors = []

            def recording_mkstemp(*args, **kwargs):
                descriptor, name = real_mkstemp(*args, **kwargs)
                created_descriptors.append(descriptor)
                return descriptor, name

            def close_then_fail(descriptor):
                real_close(descriptor)
                raise OSError("secondary close failure")

            with mock.patch.object(
                write_benchmark_row.tempfile,
                "mkstemp",
                side_effect=recording_mkstemp,
            ), mock.patch.object(
                write_benchmark_row.os,
                "fdopen",
                side_effect=OSError("primary fdopen failure"),
                create=True,
            ), mock.patch.object(
                write_benchmark_row.os,
                "close",
                side_effect=close_then_fail,
            ):
                with self.assertRaisesRegex(OSError, "primary fdopen failure"):
                    write_benchmark_row.write_new(path, self.normalized())

            self.assertEqual(1, len(created_descriptors))
            with self.assertRaises(OSError):
                os.fstat(created_descriptors[0])
            self.assertFalse(path.exists())
            self.assert_only(directory, [])

    def test_create_faults_never_publish_and_always_remove_temporary_file(self):
        fault_patches = (
            ("writer", self.partial_writer_patch),
            ("flush", lambda: mock.patch.object(write_benchmark_row, "_flush", side_effect=OSError("flush"))),
            ("fsync", lambda: mock.patch.object(write_benchmark_row.os, "fsync", side_effect=OSError("fsync"))),
            ("link", lambda: mock.patch.object(write_benchmark_row.os, "link", side_effect=OSError("link"))),
        )
        for name, patch_factory in fault_patches:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory_name:
                directory = Path(directory_name)
                path = directory / "raw-runs.csv"
                with patch_factory():
                    with self.assertRaises(OSError):
                        write_benchmark_row.write_new(path, self.normalized())
                self.assertFalse(os.path.lexists(str(path)))
                self.assert_only(directory, [])

    def test_append_faults_preserve_original_bytes_and_remove_temporary_file(self):
        fault_patches = (
            ("writer", self.partial_writer_patch),
            ("flush", lambda: mock.patch.object(write_benchmark_row, "_flush", side_effect=OSError("flush"))),
            ("fsync", lambda: mock.patch.object(write_benchmark_row.os, "fsync", side_effect=OSError("fsync"))),
            ("replace", lambda: mock.patch.object(write_benchmark_row.os, "replace", side_effect=OSError("replace"))),
        )
        for name, patch_factory in fault_patches:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory_name:
                directory = Path(directory_name)
                path = directory / "raw-runs.csv"
                row = self.normalized()
                write_benchmark_row.write_new(path, row)
                original = path.read_bytes()
                with patch_factory():
                    with self.assertRaises(OSError):
                        write_benchmark_row.append(path, row)
                self.assertEqual(original, path.read_bytes())
                self.assert_only(directory, ["raw-runs.csv"])


class WriterCliTest(unittest.TestCase):

    def invoke(self, mode, path, stdin):
        return subprocess.run(
            [sys.executable, str(WRITER), mode, str(path)],
            input=stdin,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_cli_reads_exactly_one_json_object_and_allows_trailing_whitespace(self):
        with tempfile.TemporaryDirectory() as directory_name:
            path = Path(directory_name) / "raw-runs.csv"
            result = self.invoke("--create", path, json.dumps(valid_payload()) + " \n\t")
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("", result.stdout)
            self.assertTrue(path.exists())

    def test_cli_rejects_trailing_json_or_nonwhitespace_and_nonobjects(self):
        inputs = (
            json.dumps(valid_payload()) + json.dumps(valid_payload()),
            json.dumps(valid_payload()) + " trailing",
            "",
            "   \n\t",
            '"hello"',
            "123",
            "1.5",
            "[]",
            "null",
            "true",
        )
        for index, stdin in enumerate(inputs):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory_name:
                path = Path(directory_name) / "raw-runs.csv"
                result = self.invoke("--create", path, stdin)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual("", result.stdout)
                self.assertFalse(path.exists())
                self.assertNotIn("\n", result.stderr.rstrip("\n"))


if __name__ == "__main__":
    unittest.main()
