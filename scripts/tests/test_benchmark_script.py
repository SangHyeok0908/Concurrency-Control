#!/usr/bin/env python3
"""Black-box contract tests for the methodology-v2 campaign runner."""

import csv
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "benchmark.sh"
V1_HASHES = {
    "docs/benchmark/raw-runs.csv": "7824f10229b460e911a49f47c46bb8d6338587de1401709c09a8e919ab1ed3c3",
    "docs/benchmark/2026-09-24-controlled-run.md": "6413ce6c1c1dc6d02b55d62a049cc21703e4caa1cff8a3d482b862648cc55814",
    "docs/benchmark/optimistic-attempt-distribution-cap20.json": "f193d7510ac6ce02934b99aa7c06232541f7917ba5639b9565a818d587535698",
}
DEPENDENCIES = (
    "benchmark_schedule.py",
    "benchmark_schema.py",
    "parse_optimistic_metrics.py",
    "validate_benchmark_environment.py",
    "validate_benchmark_campaign.py",
    "promote_benchmark_campaign.py",
)


def write_executable(path, source):
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    path.chmod(0o755)


class RunnerHarness:
    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.scripts = self.root / "scripts"
        self.fakebin = self.root / "fakebin"
        self.state = self.root / "state"
        self.scripts.mkdir()
        self.fakebin.mkdir()
        self.state.mkdir()
        (self.root / "docs" / "benchmark").mkdir(parents=True)
        (self.root / "build" / "reports" / "gatling").mkdir(parents=True)
        shutil.copy2(RUNNER, self.scripts / "benchmark.sh")
        for name in DEPENDENCIES:
            shutil.copy2(ROOT / "scripts" / name, self.scripts / name)
        self._install_report_parser()
        self._install_writer_wrapper()
        self._install_promoter_wrapper()
        self._install_command_shims()
        self.environment = os.environ.copy()
        self.environment.update({
            "PATH": str(self.fakebin) + os.pathsep + self.environment.get("PATH", ""),
            "BENCHMARK_GRADLEW": str(self.fakebin / "gradle-shim"),
            "FAKE_STATE_DIR": str(self.state),
            "FAKE_ENV_CAP": "5",
            "ENVIRONMENT_WAIT_SECONDS": "1",
            "BASE_URL": "http://127.0.0.1:1",
        })

    def close(self):
        self.temporary.cleanup()

    def _install_report_parser(self):
        write_executable(
            self.scripts / "parse_gatling_report.py",
            r'''
            #!/usr/bin/env python3
            import os
            import sys
            from pathlib import Path

            state = Path(os.environ["FAKE_STATE_DIR"])
            count_path = state / "parse_count"
            count = int(count_path.read_text() or "0") + 1 if count_path.exists() else 1
            count_path.write_text(str(count))
            with (state / "parsed_reports.log").open("a") as handle:
                handle.write(sys.argv[1] + "\n")
            if os.environ.get("FAKE_PARSE_FAIL_AT") == str(count):
                print("controlled parser failure", file=sys.stderr)
                raise SystemExit(1)
            if os.environ.get("FAKE_PARSE_MALFORMED_AT") == str(count):
                print("not,ten,fields")
                raise SystemExit(0)
            contenders = int(sys.argv[4])
            start = 1800000000000 + count * 2000
            wall = 1000
            print("{0},{0},0,10,20,30,{1},{2},{3},{4:.1f}".format(
                contenders, start, start + wall, wall, contenders * 1000.0 / wall))
            ''',
        )

    def _install_writer_wrapper(self):
        write_executable(
            self.scripts / "write_benchmark_row.py",
            r'''
            #!/usr/bin/env python3
            import csv
            import json
            import os
            import sys
            from pathlib import Path

            from benchmark_schema import FIELDS, normalize_and_validate_row

            state = Path(os.environ["FAKE_STATE_DIR"])
            count_path = state / "writer_count"
            count = int(count_path.read_text() or "0") + 1 if count_path.exists() else 1
            count_path.write_text(str(count))
            mode = sys.argv[1] if len(sys.argv) > 1 else "missing"
            with (state / "writer_modes.log").open("a") as handle:
                handle.write(mode + "\n")
            fail_at = os.environ.get("FAKE_WRITER_FAIL_AT")
            fail_mode = os.environ.get("FAKE_WRITER_FAIL_MODE")
            if (fail_at and fail_at == str(count)) or (fail_mode and fail_mode == mode):
                if len(sys.argv) > 2 and Path(sys.argv[2]).exists():
                    (state / "writer_failure_before.bin").write_bytes(Path(sys.argv[2]).read_bytes())
                print("controlled writer failure", file=sys.stderr)
                raise SystemExit(1)
            row = normalize_and_validate_row(json.load(sys.stdin))
            path = Path(sys.argv[2])
            if mode == "--create":
                with path.open("x", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
                    writer.writeheader()
                    writer.writerow(row)
            elif mode == "--append":
                with path.open("a", newline="", encoding="utf-8") as handle:
                    csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n").writerow(row)
            else:
                raise SystemExit("unexpected writer mode")
            ''',
        )

    def _install_promoter_wrapper(self):
        shutil.copy2(
            ROOT / "scripts" / "promote_benchmark_campaign.py",
            self.scripts / "promote_benchmark_campaign_real.py",
        )
        write_executable(
            self.scripts / "promote_benchmark_campaign.py",
            r'''
            #!/usr/bin/env python3
            import os
            import subprocess
            import sys
            from pathlib import Path

            state = Path(os.environ["FAKE_STATE_DIR"])
            with (state / "promoter.log").open("a") as handle:
                handle.write("called\n")
            if len(sys.argv) > 1 and Path(sys.argv[1]).exists():
                (state / "promoter_source.bin").write_bytes(Path(sys.argv[1]).read_bytes())
            if os.environ.get("FAKE_PROMOTER_FAIL") == "1":
                print("controlled promotion failure", file=sys.stderr)
                raise SystemExit(1)
            command = [sys.executable, str(Path(__file__).with_name("promote_benchmark_campaign_real.py"))]
            command.extend(sys.argv[1:])
            raise SystemExit(subprocess.run(command).returncode)
            ''',
        )

    def _install_command_shims(self):
        write_executable(
            self.fakebin / "curl",
            r'''
            #!/bin/sh
            state=$FAKE_STATE_DIR
            args="$*"
            case "$args" in
              *benchmark-environment*)
                printf 'environment\n' >> "$state/curl.log"
                if [ "${FAKE_ENV_MODE:-ok}" = connection ]; then exit 22; fi
                cap=${FAKE_ENV_CAP:-5}
                show=false
                total=100
                if [ "${FAKE_ENV_MODE:-ok}" = mismatch ]; then show=true; fi
                if [ "${FAKE_ENV_MODE:-ok}" = pool ]; then total=99; fi
                printf '{"activeProfiles":["benchmark"],"maximumPoolSize":100,"minimumIdle":100,"totalConnections":%s,"showSql":%s,"formatSql":false,"useSqlComments":false,"sqlLogLevel":"OFF","bindLogLevel":"OFF","rootLogLevel":"OFF","optimisticMaxAttempts":%s,"backoffBaseMillis":10,"backoffMaxMillis":200,"backoffPolicy":"exponential-jitter","extraEvidence":"phase-%s"}\n' "$total" "$show" "$cap" "$cap"
                ;;
              *optimistic-retries*)
                case "$args" in
                  *DELETE*)
                    file="$state/reset_count"; count=0; [ -f "$file" ] && count=$(cat "$file")
                    count=$((count + 1)); printf '%s' "$count" > "$file"
                    printf 'reset\n' >> "$state/curl.log"
                    if [ "${FAKE_RESET_FAIL_AT:-}" = "$count" ]; then exit 22; fi
                    ;;
                  *)
                    file="$state/retry_get_count"; count=0; [ -f "$file" ] && count=$(cat "$file")
                    count=$((count + 1)); printf '%s' "$count" > "$file"
                    printf 'retry-get\n' >> "$state/curl.log"
                    if [ "${FAKE_RETRY_GET_FAIL_AT:-}" = "$count" ]; then exit 22; fi
                    if [ "${FAKE_RETRY_JSON_MODE:-}" = malformed ]; then printf '[]\n'; exit 0; fi
                    printf '{"succeededByAttempts":{},"succeeded":0,"retryExhausted":0,"versionConflicts":0,"deadlocks":0,"meanAttemptsPerSuccess":0}\n'
                    ;;
                esac
                ;;
              *) exit 91 ;;
            esac
            ''',
        )
        write_executable(
            self.fakebin / "docker",
            r'''
            #!/bin/sh
            state=$FAKE_STATE_DIR
            query=
            previous=
            for argument in "$@"; do
              if [ "$previous" = -e ]; then query=$argument; break; fi
              previous=$argument
            done
            printf '%s\n' "$query" >> "$state/mysql.log"
            case "$query" in
              *'SELECT VERSION()'*) printf '8.4.0-fake\n' ;;
              *'@@max_connections'*) printf '300\n' ;;
              *'initial benchmark counts'*) printf '0\t0\n' ;;
              *'COALESCE(MAX(id), 0)'*)
                file="$state/max_query_count"; count=0; [ -f "$file" ] && count=$(cat "$file")
                count=$((count + 1)); printf '%s' "$count" > "$file"
                if [ "${FAKE_MAX_FAIL_AT:-}" = "$count" ]; then exit 1; fi
                if [ "${FAKE_MAX_MALFORMED_AT:-}" = "$count" ]; then printf 'bad\n'; exit 0; fi
                [ -f "$state/slot" ] && cat "$state/slot" || printf '0'
                printf '\n'
                ;;
              *'benchmark invariant tuple'*)
                file="$state/db_tuple_count"; count=0; [ -f "$file" ] && count=$(cat "$file")
                count=$((count + 1)); printf '%s' "$count" > "$file"
                if [ "${FAKE_DB_FAIL_AT:-}" = "$count" ]; then exit 1; fi
                if [ "${FAKE_DB_MALFORMED_AT:-}" = "$count" ]; then printf '0\t1\n'; exit 0; fi
                cap=1; [ -f "$state/current_capacity" ] && cap=$(cat "$state/current_capacity")
                printf '0\t%s\t0\n' "$cap"
                ;;
              *) printf 'unexpected query\n' >&2; exit 92 ;;
            esac
            ''',
        )
        write_executable(
            self.fakebin / "gradle-shim",
            r'''
            #!/bin/sh
            state=$FAKE_STATE_DIR
            if [ "${1:-}" = --version ]; then
              printf 'Gradle 8.14.3\n'
              exit 0
            fi
            case " $* " in *' gatlingRun '*) ;; *) printf 'unexpected gradle invocation\n' >&2; exit 93 ;; esac
            file="$state/gradle_count"; count=0; [ -f "$file" ] && count=$(cat "$file")
            count=$((count + 1)); printf '%s' "$count" > "$file"
            printf '%s\n' "$*" >> "$state/gradle.log"
            if [ "${FAKE_GRADLE_CONSUME_STDIN:-0}" = 1 ]; then
              while IFS= read -r _; do :; done
            fi
            if [ "${FAKE_GRADLE_FAIL_AT:-}" = "$count" ]; then exit 17; fi
            cap=1
            for argument in "$@"; do case "$argument" in -Dcapacity=*) cap=${argument#-Dcapacity=} ;; esac; done
            printf '%s' "$cap" > "$state/current_capacity"
            mode=normal
            if [ "${FAKE_REPORT_MODE_AT:-}" = "$count" ]; then mode=${FAKE_REPORT_MODE:-normal}; fi
            if [ "$mode" != no-seed ] && [ "$mode" != no-seed-no-report ]; then
              slot=0; [ -f "$state/slot" ] && slot=$(cat "$state/slot")
              slot=$((slot + 1)); printf '%s' "$slot" > "$state/slot"
            fi
            reports=build/reports/gatling
            if [ "$mode" != zero ] && [ "$mode" != no-seed-no-report ]; then
              directory=$(printf '%s/report-%04d' "$reports" "$count")
              mkdir -p "$directory/js"
              if [ "$mode" != missing-log ]; then : > "$directory/simulation.log"; fi
              if [ "$mode" != missing-stats ]; then : > "$directory/js/stats.js"; fi
              if [ "$mode" = multiple ]; then
                second=$(printf '%s/report-%04d-extra' "$reports" "$count")
                mkdir -p "$second/js"; : > "$second/simulation.log"; : > "$second/js/stats.js"
              fi
            fi
            ''',
        )
        write_executable(self.fakebin / "git", "#!/bin/sh\nprintf '0123456789abcdef0123456789abcdef01234567\\n'\n")
        write_executable(self.fakebin / "java", "#!/bin/sh\nprintf 'openjdk version \\\"21-fake\\\"\\n' >&2\n")
        write_executable(self.fakebin / "uname", "#!/bin/sh\nprintf 'FakeOS 1.0 x86_64\\n'\n")
        write_executable(
            self.fakebin / "jq",
            "#!/bin/sh\nprintf 'jq was called\\n' >> \"$FAKE_STATE_DIR/jq.log\"\nexit 99\n",
        )

    def run(self, *arguments, **overrides):
        environment = self.environment.copy()
        environment.update({key: str(value) for key, value in overrides.items()})
        return subprocess.run(
            ["/bin/bash", str(self.scripts / "benchmark.sh")] + list(arguments),
            cwd=str(self.root),
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def count(self, name):
        path = self.state / name
        return int(path.read_text() or "0") if path.exists() else 0

    def lines(self, name):
        path = self.state / name
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def campaign(self, campaign_id="campaign-001"):
        return self.root / "docs" / "benchmark" / "campaigns" / campaign_id

    def csv_rows(self, campaign_id="campaign-001"):
        path = self.campaign(campaign_id) / "raw-runs.csv"
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))


class BenchmarkScriptTest(unittest.TestCase):
    def setUp(self):
        self.harness = RunnerHarness()

    def tearDown(self):
        self.harness.close()

    def assert_no_external_calls(self):
        self.assertEqual([], self.harness.lines("curl.log"))
        self.assertEqual([], self.harness.lines("gradle.log"))
        self.assertEqual([], self.harness.lines("mysql.log"))

    def test_print_plan_is_deterministic_isolated_and_supports_complete_blocks(self):
        arguments = ("--campaign-id", "plan-only", "--phase", "a", "--print-plan")
        minimal_path = self.harness.root / "print-plan-bin"
        minimal_path.mkdir()
        (minimal_path / "python3").symlink_to(shutil.which("python3"))
        (minimal_path / "dirname").symlink_to(shutil.which("dirname"))
        isolated = {"BENCHMARK_GRADLEW": "/missing/gradle", "PATH": str(minimal_path)}
        first = self.harness.run(*arguments, **isolated)
        second = self.harness.run(*arguments, **isolated)
        twenty = self.harness.run(
            "--campaign-id", "plan-only", "--phase", "a", "--rounds", "20",
            "--print-plan", **isolated,
        )

        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(first.stdout.encode(), second.stdout.encode())
        self.assertEqual(101, len(first.stdout.splitlines()))
        self.assertEqual(201, len(twenty.stdout.splitlines()))
        self.assertFalse(self.harness.campaign("plan-only").exists())
        self.assert_no_external_calls()

    def test_rejects_malformed_duplicate_unknown_and_removed_options_before_state(self):
        cases = (
            (),
            ("--campaign-id", "x", "--phase", "a", "--phase", "b"),
            ("--campaign-id", "x", "--campaign-id", "y", "--phase", "a"),
            ("--campaign-id", "x", "--phase"),
            ("--campaign-id", "bad/id", "--phase", "a"),
            ("--campaign-id", ".", "--phase", "a"),
            ("--campaign-id", "..", "--phase", "a"),
            ("--campaign-id", "x", "--phase", "c"),
            ("--campaign-id", "x", "--phase", "a", "--rounds", "9"),
            ("--campaign-id", "x", "--phase", "a", "--rounds", "11"),
            ("--campaign-id", "x", "--phase", "a", "--rounds", "0"),
            ("--campaign-id", "x", "--phase", "a", "--out", "x.csv"),
            ("--campaign-id", "x", "--phase", "a", "--strategies", "baseline"),
            ("--campaign-id", "x", "--phase", "a", "--unknown"),
            ("--campaign-id", "x", "--phase", "a", "--rounds", "10", "--rounds", "20"),
            ("--campaign-id", "x", "--phase", "a", "--campaign-root", "one", "--campaign-root", "two"),
            ("--campaign-id", "x", "--phase", "a", "--canonical-out", "one", "--canonical-out", "two"),
            ("--campaign-id", "x", "--phase", "a", "--print-plan", "--print-plan"),
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = self.harness.run(*arguments)
                self.assertEqual(2, result.returncode, result.stderr)
        self.assertFalse((self.harness.root / "docs" / "benchmark" / "campaigns").exists())
        self.assert_no_external_calls()

    def test_custom_root_with_spaces_and_removed_environment_outputs_are_isolated(self):
        custom_root = self.harness.root / "campaign root with spaces"
        old_csv = self.harness.root / "must-not-be-created.csv"
        old_distribution = self.harness.root / "must-not-be-created.json"
        result = self.harness.run(
            "--campaign-id", "custom-root", "--phase", "a",
            "--campaign-root", str(custom_root),
            FAKE_ENV_MODE="mismatch", OUT_CSV=str(old_csv),
            ATTEMPT_DISTRIBUTION_JSON=str(old_distribution),
        )

        self.assertNotEqual(0, result.returncode)
        manifest = custom_root / "custom-root" / "manifest.md"
        self.assertTrue(manifest.is_file())
        manifest_text = manifest.read_text()
        for key, target in (
            ("predecessor_raw_runs", self.harness.root / "docs" / "benchmark" / "raw-runs.csv"),
            (
                "predecessor_execution_record",
                self.harness.root / "docs" / "benchmark" / "2026-09-24-controlled-run.md",
            ),
        ):
            line = next(
                item for item in manifest_text.splitlines()
                if item.startswith("- %s: " % key)
            )
            link_target = line.rsplit("](", 1)[1][:-1]
            decoded_target = Path(unquote(link_target))
            self.assertFalse(decoded_target.is_absolute())
            self.assertEqual(
                target.resolve(),
                (manifest.parent / decoded_target).resolve(),
            )
        self.assertFalse(old_csv.exists())
        self.assertFalse(old_distribution.exists())

    def test_rejects_v1_canonical_aliases_before_campaign_state(self):
        v1 = self.harness.root / "docs" / "benchmark" / "raw-runs.csv"
        v1.write_bytes(b"immutable-v1")
        hardlink = self.harness.root / "v1-hardlink.csv"
        os.link(str(v1), str(hardlink))
        symlink = self.harness.root / "v1-symlink.csv"
        symlink.symlink_to(v1)
        spellings = (
            "docs/benchmark/raw-runs.csv",
            "docs/benchmark/../benchmark/raw-runs.csv",
            str(symlink),
            str(hardlink),
        )
        for index, spelling in enumerate(spellings):
            with self.subTest(spelling=spelling):
                result = self.harness.run(
                    "--campaign-id", "guard-%d" % index, "--phase", "a",
                    "--canonical-out", spelling,
                )
                self.assertEqual(2, result.returncode, result.stderr)
                self.assertFalse(self.harness.campaign("guard-%d" % index).exists())
        self.assertEqual(b"immutable-v1", v1.read_bytes())
        self.assert_no_external_calls()

    def test_phase_a_environment_failure_leaves_manifest_only_and_id_is_not_reusable(self):
        failed = self.harness.run(
            "--campaign-id", "env-fail", "--phase", "a", FAKE_ENV_MODE="mismatch",
        )
        campaign = self.harness.campaign("env-fail")
        retry = self.harness.run("--campaign-id", "env-fail", "--phase", "a")

        self.assertNotEqual(0, failed.returncode)
        self.assertTrue((campaign / "manifest.md").is_file())
        self.assertFalse((campaign / "raw-runs.csv").exists())
        self.assertFalse((campaign / "environment-phase-a.json").exists())
        self.assertIn("environment", failed.stderr.lower())
        manifest_text = (campaign / "manifest.md").read_text()
        self.assertIn("failure_reason", manifest_text)
        self.assertIn(
            "- predecessor_methodology: methodology-v1-fixed-order\n",
            manifest_text,
        )
        self.assertIn(
            "- predecessor_raw_runs: "
            "[docs/benchmark/raw-runs.csv](../../raw-runs.csv)\n",
            manifest_text,
        )
        self.assertIn(
            "- predecessor_execution_record: "
            "[docs/benchmark/2026-09-24-controlled-run.md]"
            "(../../2026-09-24-controlled-run.md)\n",
            manifest_text,
        )
        self.assertEqual(2, retry.returncode)
        self.assertEqual(0, self.harness.count("gradle_count"))
        self.assertEqual([], self.harness.lines("mysql.log"))

    def test_phase_a_warmup_failure_never_creates_csv(self):
        result = self.harness.run(
            "--campaign-id", "warmup-fail", "--phase", "a", FAKE_GRADLE_FAIL_AT="1",
        )
        campaign = self.harness.campaign("warmup-fail")

        self.assertNotEqual(0, result.returncode)
        self.assertTrue((campaign / "environment-phase-a.json").is_file())
        self.assertFalse((campaign / "raw-runs.csv").exists())
        self.assertIn("warmup", result.stderr.lower())
        self.assertIn("warmup", (campaign / "manifest.md").read_text().lower())

    def test_gatling_cannot_consume_the_schedule_loop_stdin(self):
        result = self.harness.run(
            "--campaign-id", "stdin-isolated", "--phase", "a",
            FAKE_GRADLE_CONSUME_STDIN="1",
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(110, self.harness.count("gradle_count"))
        self.assertEqual(110, self.harness.count("reset_count"))
        self.assertEqual(100, len(self.harness.csv_rows("stdin-isolated")))
        manifest = (self.harness.campaign("stdin-isolated") / "manifest.md").read_text()
        self.assertIn("- phase_a_warmup_status: complete", manifest)
        self.assertIn("- phase_a_measured_rows: 100", manifest)

    def test_report_selection_uses_only_the_new_directory_and_requires_exactly_one(self):
        stale = self.harness.root / "build" / "reports" / "gatling" / "stale-same-label"
        (stale / "js").mkdir(parents=True)
        (stale / "simulation.log").write_bytes(b"stale")
        (stale / "js" / "stats.js").write_bytes(b"stale")
        os.utime(stale, (4102444800, 4102444800))
        result = self.harness.run(
            "--campaign-id", "selection", "--phase", "a", FAKE_PARSE_FAIL_AT="2",
        )
        parsed = self.harness.lines("parsed_reports.log")
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(2, len(parsed))
        self.assertNotIn("stale-same-label", parsed[0])
        self.assertTrue(parsed[0].endswith("report-0011"), parsed[0])

        for mode in ("zero", "multiple", "missing-log", "missing-stats"):
            with self.subTest(mode=mode):
                harness = RunnerHarness()
                try:
                    failure = harness.run(
                        "--campaign-id", "report-" + mode, "--phase", "a",
                        FAKE_REPORT_MODE_AT="11", FAKE_REPORT_MODE=mode,
                    )
                    self.assertNotEqual(0, failure.returncode)
                    rows = harness.csv_rows("report-" + mode)
                    self.assertEqual(1, len(rows))
                    self.assertEqual("parse_failed", rows[0]["run_status"])
                    self.assertNotEqual("", rows[0]["slot_id"])
                    self.assertEqual("", rows[0]["requests"])
                finally:
                    harness.close()

    def test_slot_failure_takes_precedence_over_missing_report(self):
        result = self.harness.run(
            "--campaign-id", "slot-first", "--phase", "a",
            FAKE_REPORT_MODE_AT="11", FAKE_REPORT_MODE="no-seed-no-report",
        )
        rows = self.harness.csv_rows("slot-first")
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(1, len(rows))
        self.assertEqual("seed_failed", rows[0]["run_status"])
        self.assertTrue(all(rows[0][field] == "" for field in (
            "slot_id", "requests", "remaining", "retry_succeeded")))
        self.assertEqual(0, self.harness.count("parse_count"))

    def test_controlled_measurement_failures_write_one_typed_row_and_stop(self):
        cases = (
            ("reset", {"FAKE_RESET_FAIL_AT": "11"}, "retry_metrics_failed", "empty"),
            ("pre-slot", {"FAKE_MAX_FAIL_AT": "21"}, "db_read_failed", "empty"),
            ("pre-slot-malformed", {"FAKE_MAX_MALFORMED_AT": "21"}, "db_read_failed", "empty"),
            ("gatling", {"FAKE_GRADLE_FAIL_AT": "11"}, "gatling_failed", "empty"),
            ("post-slot", {"FAKE_MAX_FAIL_AT": "22"}, "db_read_failed", "empty"),
            ("post-slot-malformed", {"FAKE_MAX_MALFORMED_AT": "22"}, "db_read_failed", "empty"),
            ("parse", {"FAKE_PARSE_FAIL_AT": "1"}, "parse_failed", "slot"),
            ("parse-malformed", {"FAKE_PARSE_MALFORMED_AT": "1"}, "parse_failed", "slot"),
            ("db", {"FAKE_DB_FAIL_AT": "1"}, "db_read_failed", "report"),
            ("db-malformed", {"FAKE_DB_MALFORMED_AT": "1"}, "db_read_failed", "report"),
            ("retry-get", {"FAKE_RETRY_GET_FAIL_AT": "1"}, "retry_metrics_failed", "db"),
            ("retry-parse", {"FAKE_RETRY_JSON_MODE": "malformed"}, "retry_metrics_failed", "db"),
        )
        for name, environment, status, shape in cases:
            with self.subTest(name=name):
                harness = RunnerHarness()
                try:
                    result = harness.run(
                        "--campaign-id", "failure-" + name, "--phase", "a", **environment
                    )
                    self.assertNotEqual(0, result.returncode)
                    rows = harness.csv_rows("failure-" + name)
                    self.assertEqual(1, len(rows))
                    row = rows[0]
                    self.assertEqual(status, row["run_status"])
                    self.assertNotEqual("", row["error_reason"])
                    self.assertEqual("", row["retry_succeeded"])
                    if shape == "empty":
                        self.assertEqual("", row["slot_id"])
                        self.assertEqual("", row["requests"])
                    elif shape == "slot":
                        self.assertNotEqual("", row["slot_id"])
                        self.assertEqual("", row["requests"])
                    elif shape == "report":
                        self.assertNotEqual("", row["requests"])
                        self.assertEqual("", row["remaining"])
                    else:
                        self.assertNotEqual("", row["remaining"])
                    self.assertEqual(1, harness.count("writer_count"))
                finally:
                    harness.close()

    def test_create_and_append_writer_failures_are_not_recorded_recursively(self):
        create = self.harness.run(
            "--campaign-id", "writer-create", "--phase", "a", FAKE_WRITER_FAIL_AT="1",
        )
        self.assertNotEqual(0, create.returncode)
        self.assertFalse((self.harness.campaign("writer-create") / "raw-runs.csv").exists())
        self.assertEqual(1, self.harness.count("writer_count"))
        self.assertIn("writer", create.stderr.lower())

        harness = RunnerHarness()
        try:
            append = harness.run(
                "--campaign-id", "writer-append", "--phase", "a", FAKE_WRITER_FAIL_AT="2",
            )
            path = harness.campaign("writer-append") / "raw-runs.csv"
            rows = harness.csv_rows("writer-append")
            self.assertNotEqual(0, append.returncode)
            self.assertEqual(1, len(rows))
            self.assertEqual("ok", rows[0]["run_status"])
            self.assertEqual(2, harness.count("writer_count"))
            self.assertEqual(["--create", "--append"], harness.lines("writer_modes.log"))
            self.assertEqual(
                (harness.state / "writer_failure_before.bin").read_bytes(),
                path.read_bytes(),
            )
            self.assertEqual(12, harness.count("gradle_count"))
            self.assertEqual([], harness.lines("promoter.log"))
        finally:
            harness.close()

    def test_ten_round_happy_path_runs_both_phases_and_promotes_once(self):
        phase_a = self.harness.run("--campaign-id", "happy", "--phase", "a")
        self.assertEqual(0, phase_a.returncode, phase_a.stderr)
        before_a = (self.harness.campaign("happy") / "raw-runs.csv").read_bytes()
        manifest_a = (self.harness.campaign("happy") / "manifest.md").read_bytes()
        phase_b = self.harness.run(
            "--campaign-id", "happy", "--phase", "b", FAKE_ENV_CAP="20",
        )
        self.assertEqual(0, phase_b.returncode, phase_b.stderr)

        campaign = self.harness.campaign("happy")
        rows = self.harness.csv_rows("happy")
        self.assertEqual(200, len(rows))
        self.assertEqual(220, self.harness.count("gradle_count"))
        self.assertEqual(220, self.harness.count("reset_count"))
        self.assertEqual(["--create"] + ["--append"] * 199, self.harness.lines("writer_modes.log"))
        self.assertEqual({"a", "b"}, {row["phase"] for row in rows})
        self.assertEqual(
            {"baseline", "unique", "conditional", "pessimistic", "optimistic"},
            {row["strategy"] for row in rows if row["phase"] == "b"},
        )
        for phase, cap in (("a", "5"), ("b", "20")):
            snapshot = campaign / ("environment-phase-%s.json" % phase)
            self.assertEqual(int(cap), json.loads(snapshot.read_text())["optimisticMaxAttempts"])
            digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            phase_rows = [row for row in rows if row["phase"] == phase]
            self.assertEqual({digest}, {row["environment_sha256"] for row in phase_rows})
            self.assertEqual({cap}, {row["optimistic_max_attempts"] for row in phase_rows})
            self.assertEqual(
                {"happy-phase-%s" % phase}, {row["app_start_id"] for row in phase_rows},
            )
            self.assertIn(digest, (campaign / "manifest.md").read_text())
        final_manifest = (campaign / "manifest.md").read_bytes()
        self.assertTrue(final_manifest.startswith(manifest_a + b"\n## Phase B\n"))
        manifest_text = final_manifest.decode("utf-8")
        for heading in ("# Benchmark Campaign happy", "## Campaign", "## Phase A", "## Phase B"):
            self.assertIn(heading, manifest_text)
        for field in (
            "invocation", "git_head", "os", "java", "gradle", "gatling_version",
            "mysql", "max_connections", "initial_slot_count", "initial_reservation_count",
            "app_start_id", "environment_path", "environment_sha256",
            "expected_max_attempts", "started_at", "completed_at", "warmup_status",
            "measured_status", "measured_rows", "measurement_started_at",
            "measurement_completed_at", "failure_reason",
        ):
            self.assertIn(field, manifest_text)
        self.assertIn("docs/benchmark/campaigns/happy/environment-phase-a.json", manifest_text)
        self.assertEqual([], self.harness.lines("jq.log"))

        def command_treatment(command):
            values = {}
            for argument in command.split():
                for key in ("strategy", "capacity", "contenders"):
                    prefix = "-D%s=" % key
                    if argument.startswith(prefix):
                        values[key] = argument[len(prefix):]
            return values["strategy"], values["capacity"], values["contenders"]

        commands = [command_treatment(line) for line in self.harness.lines("gradle.log")]
        expected_a = [(row["strategy"], row["capacity"], row["contenders"])
                      for row in rows if row["phase"] == "a"]
        expected_b = [(row["strategy"], row["capacity"], row["contenders"])
                      for row in rows if row["phase"] == "b"]
        self.assertEqual(expected_a[:10], commands[:10])
        self.assertEqual(expected_a, commands[10:110])
        self.assertEqual(expected_b[:10], commands[110:120])
        self.assertEqual(expected_b, commands[120:220])
        canonical = self.harness.root / "docs" / "benchmark" / "raw-runs-v2.csv"
        self.assertEqual((campaign / "raw-runs.csv").read_bytes(), canonical.read_bytes())
        self.assertEqual(["called"], self.harness.lines("promoter.log"))
        self.assertEqual(before_a, (campaign / "raw-runs.csv").read_bytes()[:len(before_a)])

    def test_phase_b_environment_and_warmup_failures_preserve_phase_a_bytes(self):
        for failure_name, overrides in (
            ("environment", {"FAKE_ENV_CAP": "5"}),
            ("warmup", {"FAKE_ENV_CAP": "20", "FAKE_GRADLE_FAIL_AT": "111"}),
        ):
            with self.subTest(failure=failure_name):
                harness = RunnerHarness()
                try:
                    phase_a = harness.run("--campaign-id", "b-" + failure_name, "--phase", "a")
                    self.assertEqual(0, phase_a.returncode, phase_a.stderr)
                    csv_path = harness.campaign("b-" + failure_name) / "raw-runs.csv"
                    original = csv_path.read_bytes()
                    result = harness.run(
                        "--campaign-id", "b-" + failure_name, "--phase", "b", **overrides
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertEqual(original, csv_path.read_bytes())
                    self.assertEqual([], harness.lines("promoter.log"))
                    second = harness.run(
                        "--campaign-id", "b-" + failure_name, "--phase", "b",
                        FAKE_ENV_CAP="20",
                    )
                    self.assertEqual(2, second.returncode)
                    self.assertEqual(original, csv_path.read_bytes())
                finally:
                    harness.close()

    def test_phase_b_preflight_rejects_snapshot_hash_mismatch_before_current_app(self):
        phase_a = self.harness.run("--campaign-id", "bad-a", "--phase", "a")
        self.assertEqual(0, phase_a.returncode, phase_a.stderr)
        campaign = self.harness.campaign("bad-a")
        (campaign / "environment-phase-a.json").write_bytes(b"tampered\n")
        curl_before = list(self.harness.lines("curl.log"))
        gradle_before = self.harness.count("gradle_count")

        phase_b = self.harness.run(
            "--campaign-id", "bad-a", "--phase", "b", FAKE_ENV_CAP="20",
        )
        self.assertNotEqual(0, phase_b.returncode)
        self.assertEqual(curl_before, self.harness.lines("curl.log"))
        self.assertEqual(gradle_before, self.harness.count("gradle_count"))
        self.assertFalse((campaign / "environment-phase-b.json").exists())

    def test_phase_b_preflight_rejects_every_invalid_boundary_without_app_access(self):
        campaign_id = "boundary"
        phase_a = self.harness.run("--campaign-id", campaign_id, "--phase", "a")
        self.assertEqual(0, phase_a.returncode, phase_a.stderr)
        campaign = self.harness.campaign(campaign_id)
        with tempfile.TemporaryDirectory() as backup_name:
            backup = Path(backup_name) / campaign_id
            shutil.copytree(campaign, backup)

            def restore():
                if campaign.exists() or campaign.is_symlink():
                    if campaign.is_symlink():
                        campaign.unlink()
                    else:
                        shutil.rmtree(campaign)
                shutil.copytree(backup, campaign)

            def read_rows():
                with (campaign / "raw-runs.csv").open(newline="", encoding="utf-8") as handle:
                    return list(csv.DictReader(handle))

            def write_rows(rows):
                with (campaign / "raw-runs.csv").open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, fieldnames=rows[0].keys(), lineterminator="\n")
                    writer.writeheader()
                    writer.writerows(rows)

            def incomplete():
                rows = read_rows()
                write_rows(rows[:-1])

            def failure_row():
                rows = read_rows()
                for field in (
                    "slot_id", "requests", "ok", "ko", "mean_ms", "p95_ms", "max_ms",
                    "burst_start_epoch_ms", "burst_end_epoch_ms", "burst_wall_ms", "tps",
                    "remaining", "confirmed", "overbooking", "duplicates", "retry_succeeded",
                    "retry_exhausted", "version_conflicts", "deadlocks", "mean_attempts",
                ):
                    rows[-1][field] = ""
                rows[-1]["run_status"] = "seed_failed"
                rows[-1]["error_reason"] = "controlled boundary failure"
                write_rows(rows)

            def mutate_field(field, value, all_rows=False):
                rows = read_rows()
                targets = rows if all_rows else rows[:1]
                for row in targets:
                    row[field] = value
                    if field == "campaign_id":
                        row["app_start_id"] = value + "-phase-a"
                write_rows(rows)

            def add_b_row():
                rows = read_rows()
                row = dict(rows[0])
                row.update({
                    "phase": "b", "app_start_id": campaign_id + "-phase-b",
                    "optimistic_max_attempts": "20", "environment_sha256": "b" * 64,
                    "slot_id": "999999",
                })
                write_rows(rows + [row])

            def prior_manifest_attempt():
                with (campaign / "manifest.md").open("a", encoding="utf-8") as handle:
                    handle.write("\n## Phase B\n\n- phase_b_attempted: true\n")

            def existing_b(kind):
                path = campaign / "environment-phase-b.json"
                if kind == "regular":
                    path.write_bytes(b"prior\n")
                elif kind == "symlink":
                    path.symlink_to(campaign / "environment-phase-a.json")
                else:
                    path.symlink_to(campaign / "missing-environment.json")

            cases = (
                ("rounds-mismatch", lambda: None, ("--rounds", "20")),
                ("incomplete", incomplete, ()),
                ("failure-row", failure_row, ()),
                ("wrong-cap", lambda: mutate_field("optimistic_max_attempts", "20"), ()),
                ("wrong-app-start", lambda: mutate_field("app_start_id", "wrong-phase-a"), ()),
                ("wrong-hash", lambda: mutate_field("environment_sha256", "f" * 64), ()),
                ("wrong-campaign", lambda: mutate_field("campaign_id", "other", True), ()),
                ("b-row", add_b_row, ()),
                ("manifest-attempt", prior_manifest_attempt, ()),
                ("b-environment", lambda: existing_b("regular"), ()),
                ("b-environment-symlink", lambda: existing_b("symlink"), ()),
                ("b-environment-dangling", lambda: existing_b("dangling"), ()),
            )
            for name, mutate, extra_args in cases:
                with self.subTest(case=name):
                    restore()
                    mutate()
                    csv_before = (campaign / "raw-runs.csv").read_bytes()
                    manifest_before = (campaign / "manifest.md").read_bytes()
                    curl_before = list(self.harness.lines("curl.log"))
                    gradle_before = self.harness.count("gradle_count")
                    result = self.harness.run(
                        "--campaign-id", campaign_id, "--phase", "b", *extra_args,
                        FAKE_ENV_CAP="20",
                    )
                    self.assertEqual(2, result.returncode, result.stderr)
                    self.assertEqual(csv_before, (campaign / "raw-runs.csv").read_bytes())
                    self.assertEqual(manifest_before, (campaign / "manifest.md").read_bytes())
                    self.assertEqual(curl_before, self.harness.lines("curl.log"))
                    self.assertEqual(gradle_before, self.harness.count("gradle_count"))

    def test_promotion_failure_preserves_the_complete_campaign(self):
        phase_a = self.harness.run("--campaign-id", "promotion-fail", "--phase", "a")
        self.assertEqual(0, phase_a.returncode, phase_a.stderr)
        phase_b = self.harness.run(
            "--campaign-id", "promotion-fail", "--phase", "b",
            FAKE_ENV_CAP="20", FAKE_PROMOTER_FAIL="1",
        )
        campaign_csv = self.harness.campaign("promotion-fail") / "raw-runs.csv"

        self.assertNotEqual(0, phase_b.returncode)
        self.assertEqual(200, len(self.harness.csv_rows("promotion-fail")))
        self.assertEqual(
            (self.harness.state / "promoter_source.bin").read_bytes(),
            campaign_csv.read_bytes(),
        )
        self.assertFalse((self.harness.root / "docs" / "benchmark" / "raw-runs-v2.csv").exists())
        self.assertEqual(["called"], self.harness.lines("promoter.log"))
        self.assertIn(
            "phase_b_status: promotion_failed",
            (self.harness.campaign("promotion-fail") / "manifest.md").read_text(),
        )

    def test_twenty_round_campaign_is_complete_noncanonical_and_never_promoted(self):
        canonical = self.harness.root / "custom-canonical.csv"
        canonical.write_bytes(b"existing canonical\n")
        phase_a = self.harness.run(
            "--campaign-id", "long", "--phase", "a", "--rounds", "20",
            "--canonical-out", str(canonical),
        )
        self.assertEqual(0, phase_a.returncode, phase_a.stderr)
        phase_b = self.harness.run(
            "--campaign-id", "long", "--phase", "b", "--rounds", "20",
            "--canonical-out", str(canonical), FAKE_ENV_CAP="20",
        )
        self.assertEqual(0, phase_b.returncode, phase_b.stderr)
        self.assertEqual(400, len(self.harness.csv_rows("long")))
        self.assertEqual(420, self.harness.count("gradle_count"))
        self.assertEqual(b"existing canonical\n", canonical.read_bytes())
        self.assertEqual([], self.harness.lines("promoter.log"))
        self.assertIn("complete_noncanonical", (self.harness.campaign("long") / "manifest.md").read_text())
        self.assertIn(str(self.harness.campaign("long") / "raw-runs.csv"), phase_b.stdout)

    def test_v1_evidence_hashes_remain_exact(self):
        for relative, expected in V1_HASHES.items():
            with self.subTest(path=relative):
                actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
                self.assertEqual(expected, actual)


if __name__ == "__main__":
    unittest.main()
