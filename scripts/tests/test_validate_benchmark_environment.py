import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts" / "validate_benchmark_environment.py"


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

    def validate(self, environment, expected_max_attempts=5):
        return subprocess.run(
            [
                sys.executable,
                str(VALIDATOR),
                "--expected-max-attempts",
                str(expected_max_attempts),
            ],
            input=json.dumps(environment),
            text=True,
            capture_output=True,
            check=False,
        )

    def test_accepts_the_controlled_environment(self):
        result = self.validate(self.valid_environment())

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("benchmark environment verified", result.stdout)

    def test_rejects_a_mixed_profile(self):
        environment = self.valid_environment()
        environment["activeProfiles"] = ["test", "benchmark"]

        result = self.validate(environment)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("activeProfiles", result.stderr)

    def test_rejects_a_pool_that_is_not_ready(self):
        environment = self.valid_environment()
        environment["totalConnections"] = 73

        result = self.validate(environment)

        self.assertEqual(2, result.returncode)
        self.assertIn("totalConnections", result.stderr)

    def test_rejects_the_wrong_optimistic_retry_cap(self):
        result = self.validate(self.valid_environment(), expected_max_attempts=20)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("optimisticMaxAttempts", result.stderr)

    def test_rejects_sql_instrumentation(self):
        environment = self.valid_environment()
        environment["showSql"] = True

        result = self.validate(environment)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("showSql", result.stderr)

    def test_rejects_application_logging(self):
        environment = self.valid_environment()
        environment["rootLogLevel"] = "INFO"

        result = self.validate(environment)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("rootLogLevel", result.stderr)


if __name__ == "__main__":
    unittest.main()
