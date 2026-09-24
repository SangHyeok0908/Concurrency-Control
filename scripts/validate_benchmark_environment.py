#!/usr/bin/env python3
"""Fail fast when an HTTP benchmark is pointed at an uncontrolled app."""

import argparse
import json
import sys


EXPECTED = {
    "activeProfiles": ["benchmark"],
    "maximumPoolSize": 100,
    "minimumIdle": 100,
    "showSql": False,
    "formatSql": False,
    "useSqlComments": False,
    "sqlLogLevel": "OFF",
    "bindLogLevel": "OFF",
    "rootLogLevel": "OFF",
    "backoffBaseMillis": 10,
    "backoffMaxMillis": 200,
    "backoffPolicy": "exponential-jitter",
}


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-max-attempts", required=True, type=int)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        actual = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        print("invalid benchmark environment JSON: %s" % error, file=sys.stderr)
        return 1

    expected = dict(EXPECTED)
    expected["optimisticMaxAttempts"] = args.expected_max_attempts
    mismatches = [
        "%s: expected %r, actual %r" % (key, value, actual.get(key))
        for key, value in expected.items()
        if actual.get(key) != value
    ]

    if mismatches:
        print("benchmark environment mismatch:", file=sys.stderr)
        for mismatch in mismatches:
            print("- " + mismatch, file=sys.stderr)
        return 1

    if actual.get("totalConnections") != 100:
        print(
            "benchmark pool is not ready: totalConnections: expected 100, actual %r"
            % actual.get("totalConnections"),
            file=sys.stderr,
        )
        return 2

    print(
        "benchmark environment verified "
        "(pool=100, logs=OFF, maxAttempts=%d)" % args.expected_max_attempts
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
