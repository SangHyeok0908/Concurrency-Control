#!/usr/bin/env python3
import csv
import io
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from benchmark_schedule import build_schedule  # noqa: E402
from benchmark_schema import FIELDS, normalize_and_validate_row  # noqa: E402
import promote_benchmark_campaign  # noqa: E402
import validate_benchmark_campaign  # noqa: E402


PROMOTER = SCRIPTS / "promote_benchmark_campaign.py"
MEASUREMENT_FIELDS = FIELDS[17:37]


def build_rows(rounds=10):
    rows = []
    base_time = datetime(2026, 9, 24, 10, 0, 0)
    slot_id = 1
    for phase in ("a", "b"):
        for entry in build_schedule(rounds):
            treatment = entry.treatment
            wall_ms = 1000
            start_ms = 1_800_000_000_000 + slot_id * 2000
            payload = {
                "schema_version": 2,
                "campaign_id": "campaign-001",
                "phase": phase,
                "app_start_id": "campaign-001-phase-%s" % phase,
                "environment_sha256": ("a" if phase == "a" else "b") * 64,
                "schedule_version": entry.schedule_version,
                "schedule_cycle": entry.schedule_cycle,
                "schedule_row": entry.schedule_row,
                "measured_round": entry.measured_round,
                "position_in_round": entry.position_in_round,
                "treatment_id": treatment.treatment_id,
                "ts": (base_time + timedelta(seconds=slot_id)).isoformat(),
                "strategy": treatment.strategy,
                "optimistic_max_attempts": 5 if phase == "a" else 20,
                "contention": treatment.contention,
                "capacity": treatment.capacity,
                "contenders": treatment.contenders,
                "slot_id": slot_id,
                "requests": treatment.contenders,
                "ok": treatment.contenders,
                "ko": 0,
                "mean_ms": 10,
                "p95_ms": 20,
                "max_ms": 30,
                "burst_start_epoch_ms": start_ms,
                "burst_end_epoch_ms": start_ms + wall_ms,
                "burst_wall_ms": wall_ms,
                "tps": round(treatment.contenders * 1000.0 / wall_ms, 1),
                "remaining": treatment.capacity - treatment.contenders,
                "confirmed": treatment.contenders,
                "overbooking": max(treatment.contenders - treatment.capacity, 0),
                "duplicates": 0,
                "retry_succeeded": 0,
                "retry_exhausted": 0,
                "version_conflicts": 0,
                "deadlocks": 0,
                "mean_attempts": 1.0,
                "run_status": "ok",
                "error_reason": "",
            }
            rows.append(normalize_and_validate_row(payload))
            slot_id += 1
    return rows


def csv_bytes(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def failed_row(row):
    result = dict(row)
    for field in MEASUREMENT_FIELDS:
        result[field] = ""
    result["run_status"] = "seed_failed"
    result["error_reason"] = "controlled failure"
    return result


class CampaignPromotionTest(unittest.TestCase):

    def assert_only(self, directory, expected_names):
        self.assertEqual(
            sorted(expected_names),
            sorted(path.name for path in directory.iterdir()),
        )

    def make_source(self, directory, rows=None, name="campaign.csv"):
        source = directory / name
        source.write_bytes(csv_bytes(rows if rows is not None else build_rows()))
        return source

    def test_success_publishes_identical_bytes_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as source_name, tempfile.TemporaryDirectory() as destination_name:
            source_directory = Path(source_name)
            destination_directory = Path(destination_name)
            source = self.make_source(source_directory)
            destination = destination_directory / "raw-runs-v2.csv"
            original = source.read_bytes()

            real_link = os.link

            def assert_destination_local_temp(temporary, target):
                self.assertEqual(destination_directory, Path(temporary).parent)
                self.assertEqual(destination, Path(target))
                real_link(temporary, target)

            with mock.patch.object(
                promote_benchmark_campaign.os,
                "link",
                side_effect=assert_destination_local_temp,
            ):
                summary = promote_benchmark_campaign.promote_campaign(source, destination)

            self.assertEqual(10, summary.measured_rounds)
            self.assertEqual(original, source.read_bytes())
            self.assertEqual(original, destination.read_bytes())
            self.assert_only(source_directory, ["campaign.csv"])
            self.assert_only(destination_directory, ["raw-runs-v2.csv"])

    def test_rejects_twenty_round_incomplete_and_failed_campaigns(self):
        cases = (
            build_rows(20),
            build_rows()[:-1],
            build_rows()[:-1] + [failed_row(build_rows()[-1])],
        )
        for index, rows in enumerate(cases):
            with self.subTest(case=index), tempfile.TemporaryDirectory() as directory_name:
                directory = Path(directory_name)
                source = self.make_source(directory, rows)
                destination = directory / "canonical.csv"
                original = source.read_bytes()

                with self.assertRaises(ValueError):
                    promote_benchmark_campaign.promote_campaign(source, destination)

                self.assertEqual(original, source.read_bytes())
                self.assertFalse(os.path.lexists(str(destination)))
                self.assert_only(directory, ["campaign.csv"])

    def test_rejects_every_existing_destination_without_clobbering(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            source = self.make_source(directory)
            target = directory / "target"
            target.write_bytes(b"target")
            missing = directory / "missing"
            destinations = (
                directory / "regular",
                directory / "real-link",
                directory / "dangling-link",
                directory / "existing-directory",
            )
            destinations[0].write_bytes(b"existing")
            os.symlink(str(target), str(destinations[1]))
            os.symlink(str(missing), str(destinations[2]))
            destinations[3].mkdir()
            original_source = source.read_bytes()

            for destination in destinations:
                with self.subTest(destination=destination.name):
                    if destination.is_symlink():
                        before = os.readlink(destination)
                    elif destination.is_dir():
                        before = None
                    else:
                        before = destination.read_bytes()
                    with self.assertRaises(FileExistsError):
                        promote_benchmark_campaign.promote_campaign(source, destination)
                    if destination.is_symlink():
                        self.assertEqual(before, os.readlink(destination))
                    elif destination.is_dir():
                        self.assertTrue(destination.is_dir())
                    else:
                        self.assertEqual(before, destination.read_bytes())
                    self.assertEqual(original_source, source.read_bytes())

            self.assertEqual(b"target", target.read_bytes())
            self.assertFalse(missing.exists())
            self.assertFalse(any(directory.glob(".promote-*.tmp")))

    def test_loses_a_late_publication_race_without_clobbering_winner(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            source = self.make_source(directory)
            destination = directory / "canonical.csv"
            real_link = os.link

            def racing_link(temporary, target):
                Path(target).write_bytes(b"winner")
                real_link(temporary, target)

            with mock.patch.object(
                promote_benchmark_campaign.os, "link", side_effect=racing_link,
            ):
                with self.assertRaises(FileExistsError):
                    promote_benchmark_campaign.promote_campaign(source, destination)

            self.assertEqual(b"winner", destination.read_bytes())
            self.assertTrue(source.exists())
            self.assert_only(directory, ["campaign.csv", "canonical.csv"])

    def test_validates_the_copied_temporary_bytes_not_a_later_source_path(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            source = self.make_source(directory)
            destination = directory / "canonical.csv"
            original = source.read_bytes()
            real_validate = validate_benchmark_campaign.validate_campaign
            observed = []

            def mutate_source_then_validate(path, *args, **kwargs):
                observed.append(Path(path))
                self.assertEqual(directory, Path(path).parent)
                self.assertNotEqual(source, Path(path))
                self.assertEqual(original, Path(path).read_bytes())
                self.assertEqual(("complete",), args)
                self.assertEqual({"expected_rounds": 10}, kwargs)
                source.write_bytes(b"changed after copy")
                return real_validate(path, *args, **kwargs)

            with mock.patch.object(
                promote_benchmark_campaign,
                "validate_campaign",
                side_effect=mutate_source_then_validate,
            ):
                promote_benchmark_campaign.promote_campaign(source, destination)

            self.assertEqual(1, len(observed))
            self.assertEqual(original, destination.read_bytes())
            self.assertEqual(b"changed after copy", source.read_bytes())

    def test_uses_the_original_exclusive_temporary_descriptor(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            source = self.make_source(directory)
            destination = directory / "canonical.csv"
            victim = directory / "victim.txt"
            victim.write_bytes(b"must remain unchanged")
            real_close = os.close
            swapped_names = []

            def swap_after_raw_descriptor_close(descriptor):
                real_close(descriptor)
                candidates = list(directory.glob(".promote-*.tmp"))
                self.assertEqual(1, len(candidates))
                temporary = candidates[0]
                temporary.unlink()
                os.symlink(str(victim), str(temporary))
                swapped_names.append(temporary)

            with mock.patch.object(
                promote_benchmark_campaign.os,
                "close",
                side_effect=swap_after_raw_descriptor_close,
            ):
                promote_benchmark_campaign.promote_campaign(source, destination)

            self.assertEqual([], swapped_names)
            self.assertEqual(b"must remain unchanged", victim.read_bytes())
            self.assertEqual(source.read_bytes(), destination.read_bytes())
            self.assertFalse(destination.is_symlink())

    def test_fdopen_failure_closes_descriptor_cleans_temp_and_keeps_primary_error(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            source = self.make_source(directory)
            destination = directory / "canonical.csv"
            real_mkstemp = tempfile.mkstemp
            real_close = os.close
            descriptors = []

            def recording_mkstemp(*args, **kwargs):
                descriptor, name = real_mkstemp(*args, **kwargs)
                descriptors.append(descriptor)
                return descriptor, name

            def close_then_fail(descriptor):
                real_close(descriptor)
                raise OSError("secondary close failure")

            with mock.patch.object(
                promote_benchmark_campaign.tempfile,
                "mkstemp",
                side_effect=recording_mkstemp,
            ), mock.patch.object(
                promote_benchmark_campaign.os,
                "fdopen",
                side_effect=OSError("primary fdopen failure"),
                create=True,
            ), mock.patch.object(
                promote_benchmark_campaign.os,
                "close",
                side_effect=close_then_fail,
            ):
                with self.assertRaisesRegex(OSError, "primary fdopen failure"):
                    promote_benchmark_campaign.promote_campaign(source, destination)

            self.assertEqual(1, len(descriptors))
            with self.assertRaises(OSError):
                os.fstat(descriptors[0])
            self.assert_only(directory, ["campaign.csv"])

    def test_copy_flush_fsync_close_validation_and_link_failures_clean_temp(self):
        patches = (
            ("copy", lambda: mock.patch.object(
                promote_benchmark_campaign, "_copy", side_effect=OSError("copy"),
            )),
            ("flush", lambda: mock.patch.object(
                promote_benchmark_campaign, "_flush", side_effect=OSError("flush"),
            )),
            ("fsync", lambda: mock.patch.object(
                promote_benchmark_campaign.os, "fsync", side_effect=OSError("fsync"),
            )),
            ("close", lambda: mock.patch.object(
                promote_benchmark_campaign, "_close", side_effect=OSError("close"),
            )),
            ("validation", lambda: mock.patch.object(
                promote_benchmark_campaign,
                "validate_campaign",
                side_effect=ValueError("validation"),
            )),
            ("link", lambda: mock.patch.object(
                promote_benchmark_campaign.os, "link", side_effect=OSError("link"),
            )),
        )
        for name, patch_factory in patches:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory_name:
                directory = Path(directory_name)
                source = self.make_source(directory)
                destination = directory / "canonical.csv"
                original = source.read_bytes()
                with patch_factory():
                    with self.assertRaises((OSError, ValueError)):
                        promote_benchmark_campaign.promote_campaign(source, destination)
                self.assertEqual(original, source.read_bytes())
                self.assertFalse(os.path.lexists(str(destination)))
                self.assert_only(directory, ["campaign.csv"])

    def test_copy_flush_fsync_and_close_failures_close_the_temporary_descriptor(self):
        patches = (
            ("copy", lambda: mock.patch.object(
                promote_benchmark_campaign, "_copy", side_effect=OSError("copy"),
            )),
            ("flush", lambda: mock.patch.object(
                promote_benchmark_campaign, "_flush", side_effect=OSError("flush"),
            )),
            ("fsync", lambda: mock.patch.object(
                promote_benchmark_campaign.os, "fsync", side_effect=OSError("fsync"),
            )),
            ("close", lambda: mock.patch.object(
                promote_benchmark_campaign, "_close", side_effect=OSError("close"),
            )),
        )
        for name, patch_factory in patches:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory_name:
                directory = Path(directory_name)
                source = self.make_source(directory)
                destination = directory / "canonical.csv"
                real_mkstemp = tempfile.mkstemp
                descriptors = []

                def recording_mkstemp(*args, **kwargs):
                    descriptor, temporary_name = real_mkstemp(*args, **kwargs)
                    descriptors.append(descriptor)
                    return descriptor, temporary_name

                with mock.patch.object(
                    promote_benchmark_campaign.tempfile,
                    "mkstemp",
                    side_effect=recording_mkstemp,
                ), patch_factory():
                    with self.assertRaises(OSError):
                        promote_benchmark_campaign.promote_campaign(source, destination)

                self.assertEqual(1, len(descriptors))
                with self.assertRaises(OSError):
                    os.fstat(descriptors[0])
                self.assert_only(directory, ["campaign.csv"])

    def test_cleanup_failure_does_not_replace_the_primary_error(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            source = self.make_source(directory)
            destination = directory / "canonical.csv"
            real_remove = promote_benchmark_campaign._remove_temporary

            def remove_then_fail(path):
                real_remove(path)
                raise OSError("secondary cleanup failure")

            with mock.patch.object(
                promote_benchmark_campaign, "_copy", side_effect=OSError("primary copy failure"),
            ), mock.patch.object(
                promote_benchmark_campaign,
                "_remove_temporary",
                side_effect=remove_then_fail,
            ):
                with self.assertRaisesRegex(OSError, "primary copy failure"):
                    promote_benchmark_campaign.promote_campaign(source, destination)

            self.assert_only(directory, ["campaign.csv"])


class CampaignPromotionCliTest(unittest.TestCase):

    def invoke(self, source, destination):
        return subprocess.run(
            [sys.executable, str(PROMOTER), str(source), "--to", str(destination)],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_cli_reports_success_only_after_publication(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            source = directory / "campaign.csv"
            source.write_bytes(csv_bytes(build_rows()))
            destination = directory / "canonical.csv"

            result = self.invoke(source, destination)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("", result.stderr)
            self.assertIn("promoted", result.stdout)
            self.assertEqual(source.read_bytes(), destination.read_bytes())

    def test_cli_failure_is_one_stderr_line_and_prints_no_summary(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            source = directory / "campaign.csv"
            source.write_bytes(csv_bytes(build_rows(20)))
            destination = directory / "canonical.csv"

            result = self.invoke(source, destination)

            self.assertNotEqual(0, result.returncode)
            self.assertEqual("", result.stdout)
            self.assertEqual(1, len(result.stderr.rstrip("\n").splitlines()))
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
