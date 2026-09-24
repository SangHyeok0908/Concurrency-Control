import hashlib
import importlib.util
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
VALIDATOR = ROOT / "scripts" / "validate_benchmark_environment.py"
SPEC = importlib.util.spec_from_file_location("validate_benchmark_environment", VALIDATOR)
VALIDATOR_MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(VALIDATOR_MODULE)


class ValidateBenchmarkEnvironmentTest(unittest.TestCase):

    def valid_environment(self):
        return {
            "activeProfiles": ["benchmark"],
            "maximumPoolSize": 100,
            "minimumIdle": 100,
            "totalConnections": 100,
            "showSql": False,
            "formatSql": False,
            "useSqlComments": False,
            "sqlLogLevel": "OFF",
            "bindLogLevel": "OFF",
            "rootLogLevel": "OFF",
            "optimisticMaxAttempts": 5,
            "backoffBaseMillis": 10,
            "backoffMaxMillis": 200,
            "backoffPolicy": "exponential-jitter",
        }

    def validate(
        self,
        environment,
        expected_max_attempts=5,
        canonical_output=None,
        input_text=None,
    ):
        command = [
            sys.executable,
            str(VALIDATOR),
            "--expected-max-attempts",
            str(expected_max_attempts),
        ]
        if canonical_output is not None:
            command.extend(["--canonical-output", str(canonical_output)])
        return subprocess.run(
            command,
            input=json.dumps(environment) if input_text is None else input_text,
            text=True,
            capture_output=True,
            check=False,
        )

    def temporary_files(self, destination):
        return list(destination.parent.glob(".%s.*.tmp" % destination.name))

    def test_accepts_the_controlled_environment(self):
        result = self.validate(self.valid_environment())

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("benchmark environment verified", result.stdout)

    def test_success_without_output_still_reports_the_canonical_hash(self):
        environment = self.valid_environment()
        canonical = (
            json.dumps(
                environment,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            + b"\n"
        )

        result = self.validate(environment)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            [
                "benchmark environment verified (pool=100, logs=OFF, maxAttempts=5)",
                "environment_sha256=" + hashlib.sha256(canonical).hexdigest(),
            ],
            result.stdout.splitlines(),
        )

    def test_writes_the_complete_object_as_stable_compact_utf8_json(self):
        environment = self.valid_environment()
        environment["diagnosticMessage"] = "풀 준비 완료"
        environment["nestedDiagnostic"] = {"z": 1, "a": ["측정", True]}
        expected = (
            json.dumps(
                environment,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            + b"\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "environment.json"

            result = self.validate(environment, canonical_output=destination)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(expected, destination.read_bytes())
            self.assertEqual(1, expected.count(b"\n"))
            self.assertTrue(expected.endswith(b"\n"))
            self.assertIn("풀 준비 완료".encode("utf-8"), expected)
            self.assertEqual(
                "environment_sha256=" + hashlib.sha256(expected).hexdigest(),
                result.stdout.splitlines()[1],
            )

    def test_rejects_a_mixed_profile(self):
        environment = self.valid_environment()
        environment["activeProfiles"] = ["test", "benchmark"]

        result = self.validate(environment)

        self.assertEqual(1, result.returncode)
        self.assertIn("activeProfiles", result.stderr)

    def test_rejects_a_pool_that_is_not_ready(self):
        environment = self.valid_environment()
        environment["totalConnections"] = 73

        result = self.validate(environment)

        self.assertEqual(2, result.returncode)
        self.assertIn("totalConnections", result.stderr)

    def test_rejects_the_wrong_optimistic_retry_cap(self):
        result = self.validate(self.valid_environment(), expected_max_attempts=20)

        self.assertEqual(1, result.returncode)
        self.assertIn("optimisticMaxAttempts", result.stderr)

    def test_rejects_sql_instrumentation(self):
        environment = self.valid_environment()
        environment["showSql"] = True

        result = self.validate(environment)

        self.assertEqual(1, result.returncode)
        self.assertIn("showSql", result.stderr)

    def test_rejects_application_logging(self):
        environment = self.valid_environment()
        environment["rootLogLevel"] = "INFO"

        result = self.validate(environment)

        self.assertEqual(1, result.returncode)
        self.assertIn("rootLogLevel", result.stderr)

    def test_rejects_invalid_json_and_non_object_json(self):
        invalid = self.validate(None, input_text="{")
        non_object = self.validate(None, input_text="[]")

        self.assertEqual(1, invalid.returncode)
        self.assertIn("invalid benchmark environment JSON", invalid.stderr)
        self.assertEqual(1, non_object.returncode)
        self.assertIn("must be an object", non_object.stderr)

    def test_mismatch_does_not_publish_a_new_snapshot(self):
        environment = self.valid_environment()
        environment["showSql"] = True
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "environment.json"

            result = self.validate(environment, canonical_output=destination)

            self.assertEqual(1, result.returncode)
            self.assertFalse(destination.exists())
            self.assertEqual([], self.temporary_files(destination))

    def test_pool_not_ready_does_not_publish_a_new_snapshot(self):
        environment = self.valid_environment()
        environment["totalConnections"] = 99
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "environment.json"

            result = self.validate(environment, canonical_output=destination)

            self.assertEqual(2, result.returncode)
            self.assertFalse(destination.exists())
            self.assertEqual([], self.temporary_files(destination))

    def test_validation_failures_preserve_an_existing_snapshot_byte_for_byte(self):
        cases = (("mismatch", 1), ("pool-not-ready", 2))
        for case, expected_exit in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / "environment.json"
                original = b"existing snapshot must stay byte-identical\n"
                destination.write_bytes(original)
                environment = self.valid_environment()
                if case == "mismatch":
                    environment["showSql"] = True
                else:
                    environment["totalConnections"] = 17

                result = self.validate(environment, canonical_output=destination)

                self.assertEqual(expected_exit, result.returncode)
                self.assertEqual("", result.stdout)
                self.assertEqual(original, destination.read_bytes())
                self.assertEqual([], self.temporary_files(destination))

    def test_existing_regular_destination_is_preserved_and_temporary_is_cleaned(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "environment.json"
            original = b"previous snapshot\n"
            destination.write_bytes(original)

            result = self.validate(self.valid_environment(), canonical_output=destination)

            self.assertEqual(1, result.returncode)
            self.assertIn("already exists", result.stderr)
            self.assertEqual("", result.stdout)
            self.assertEqual(original, destination.read_bytes())
            self.assertEqual([], self.temporary_files(destination))

    def test_existing_symlink_and_its_target_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_bytes(b"do not replace\n")
            destination = root / "environment.json"
            destination.symlink_to(target)
            original_link = os.readlink(destination)

            result = self.validate(self.valid_environment(), canonical_output=destination)

            self.assertEqual(1, result.returncode)
            self.assertEqual("", result.stdout)
            self.assertTrue(destination.is_symlink())
            self.assertEqual(original_link, os.readlink(destination))
            self.assertEqual(b"do not replace\n", target.read_bytes())
            self.assertEqual([], self.temporary_files(destination))

    def test_existing_dangling_symlink_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing_target = root / "missing-target.json"
            destination = root / "environment.json"
            destination.symlink_to(missing_target)

            result = self.validate(self.valid_environment(), canonical_output=destination)

            self.assertEqual(1, result.returncode)
            self.assertIn("already exists", result.stderr)
            self.assertEqual("", result.stdout)
            self.assertTrue(os.path.lexists(destination))
            self.assertTrue(destination.is_symlink())
            self.assertEqual(str(missing_target), os.readlink(destination))
            self.assertFalse(missing_target.exists())
            self.assertEqual([], self.temporary_files(destination))

    def test_existing_directory_destination_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "environment.json"
            destination.mkdir()

            result = self.validate(self.valid_environment(), canonical_output=destination)

            self.assertEqual(1, result.returncode)
            self.assertIn("already exists", result.stderr)
            self.assertEqual("", result.stdout)
            self.assertTrue(destination.is_dir())
            self.assertEqual([], list(destination.iterdir()))
            self.assertEqual([], self.temporary_files(destination))

    def test_serialization_failure_does_not_publish_a_snapshot(self):
        environment = self.valid_environment()
        environment["diagnosticMessage"] = "\ud800"
        encoded_input = json.dumps(environment)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "environment.json"

            result = self.validate(
                None,
                canonical_output=destination,
                input_text=encoded_input,
            )

            self.assertEqual(1, result.returncode)
            self.assertIn("cannot canonicalize", result.stderr)
            self.assertEqual("", result.stdout)
            self.assertFalse(destination.exists())
            self.assertEqual([], self.temporary_files(destination))

    def test_missing_output_directory_is_reported_without_a_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "missing" / "environment.json"

            result = self.validate(self.valid_environment(), canonical_output=destination)

            self.assertEqual(1, result.returncode)
            self.assertIn("cannot write canonical benchmark environment", result.stderr)
            self.assertEqual("", result.stdout)
            self.assertFalse(destination.exists())

    def test_write_flush_fsync_and_close_failures_leave_no_destination_or_temporary(self):
        payload = b'{}\n'
        for operation in ("write", "flush", "fsync", "close"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / "environment.json"
                descriptor, name = tempfile.mkstemp(dir=directory)
                if operation in ("write", "flush", "close"):
                    patcher = mock.patch.object(
                        VALIDATOR_MODULE,
                        "_" + operation,
                        side_effect=OSError("forced %s failure" % operation),
                    )
                else:
                    patcher = mock.patch.object(
                        VALIDATOR_MODULE.os,
                        "fsync",
                        side_effect=OSError("forced fsync failure"),
                    )
                with mock.patch.object(
                    VALIDATOR_MODULE.tempfile,
                    "mkstemp",
                    return_value=(descriptor, name),
                ), patcher, self.assertRaises(OSError):
                    VALIDATOR_MODULE.write_canonical_output(destination, payload)

                with self.assertRaises(OSError):
                    os.fstat(descriptor)
                self.assertFalse(Path(name).exists())
                self.assertFalse(destination.exists())
                self.assertEqual([], self.temporary_files(destination))

    def test_late_link_competitor_is_preserved_and_temporary_is_cleaned(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "environment.json"
            competitor = b"won publication race\n"
            real_link = os.link
            descriptor, name = tempfile.mkstemp(dir=directory)

            def publish_competitor_then_link(source, target):
                Path(target).write_bytes(competitor)
                return real_link(source, target)

            with mock.patch.object(
                VALIDATOR_MODULE.tempfile,
                "mkstemp",
                return_value=(descriptor, name),
            ), mock.patch.object(
                VALIDATOR_MODULE.os,
                "link",
                side_effect=publish_competitor_then_link,
            ), self.assertRaises(FileExistsError):
                VALIDATOR_MODULE.write_canonical_output(destination, b'{}\n')

            with self.assertRaises(OSError):
                os.fstat(descriptor)
            self.assertFalse(Path(name).exists())
            self.assertEqual(competitor, destination.read_bytes())
            self.assertEqual([], self.temporary_files(destination))

    def test_main_prints_no_success_lines_when_publication_fails(self):
        standard_output = io.StringIO()
        standard_error = io.StringIO()
        source = io.StringIO(json.dumps(self.valid_environment()))
        arguments = [
            str(VALIDATOR),
            "--expected-max-attempts",
            "5",
            "--canonical-output",
            "environment.json",
        ]

        with mock.patch.object(sys, "argv", arguments), mock.patch.object(
            sys, "stdin", source
        ), mock.patch.object(sys, "stdout", standard_output), mock.patch.object(
            sys, "stderr", standard_error
        ), mock.patch.object(
            VALIDATOR_MODULE,
            "write_canonical_output",
            side_effect=OSError("forced publication failure"),
        ):
            result = VALIDATOR_MODULE.main()

        self.assertEqual(1, result)
        self.assertEqual("", standard_output.getvalue())
        self.assertIn("cannot write canonical benchmark environment", standard_error.getvalue())

    def test_fdopen_failure_closes_descriptor_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "environment.json"
            descriptor, name = tempfile.mkstemp(dir=directory)
            with mock.patch.object(
                VALIDATOR_MODULE.tempfile,
                "mkstemp",
                return_value=(descriptor, name),
            ), mock.patch.object(
                VALIDATOR_MODULE.os,
                "fdopen",
                side_effect=OSError("forced fdopen failure"),
            ), self.assertRaises(OSError):
                VALIDATOR_MODULE.write_canonical_output(destination, b'{}\n')

            with self.assertRaises(OSError):
                os.fstat(descriptor)
            self.assertFalse(Path(name).exists())
            self.assertFalse(destination.exists())

    def test_writes_through_original_descriptor_if_temporary_name_is_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "environment.json"
            victim = root / "victim.json"
            victim.write_bytes(b"unrelated data\n")
            descriptor, name = tempfile.mkstemp(dir=directory)
            Path(name).unlink()
            Path(name).symlink_to(victim)

            with mock.patch.object(
                VALIDATOR_MODULE.tempfile,
                "mkstemp",
                return_value=(descriptor, name),
            ), mock.patch.object(
                VALIDATOR_MODULE.os,
                "link",
                side_effect=OSError("stop before publication"),
            ), self.assertRaises(OSError):
                VALIDATOR_MODULE.write_canonical_output(destination, b'{"safe":true}\n')

            self.assertEqual(b"unrelated data\n", victim.read_bytes())
            self.assertFalse(os.path.lexists(name))
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
