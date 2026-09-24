#!/usr/bin/env python3
"""Fail fast when an HTTP benchmark is pointed at an uncontrolled app."""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import BinaryIO


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
    parser.add_argument("--canonical-output", type=Path)
    return parser.parse_args()


def _write(handle: BinaryIO, payload: bytes) -> None:
    handle.write(payload)


def _flush(handle: BinaryIO) -> None:
    handle.flush()


def _close(handle: BinaryIO) -> None:
    handle.close()


def _remove_temporary(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def write_canonical_output(destination: Path, payload: bytes) -> None:
    """Publish payload beside destination without replacing any existing entry."""
    descriptor, name = tempfile.mkstemp(
        prefix=".%s." % destination.name,
        suffix=".tmp",
        dir=str(destination.parent),
    )
    temporary = Path(name)
    handle = None
    try:
        # Keep using the exclusive descriptor returned by mkstemp. Reopening
        # temporary by name would allow a swapped symlink to redirect writes.
        handle = os.fdopen(descriptor, "wb")
        descriptor = None
        _write(handle, payload)
        _flush(handle)
        os.fsync(handle.fileno())
        _close(handle)
        handle = None
        os.link(str(temporary), str(destination))
    except BaseException:
        error = sys.exc_info()
        if handle is not None:
            try:
                handle.close()
            except BaseException:
                pass
        elif descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException:
                pass
        try:
            _remove_temporary(temporary)
        except BaseException:
            pass
        raise error[1].with_traceback(error[2])
    else:
        _remove_temporary(temporary)


def _reject_non_finite(value):
    raise ValueError("non-finite JSON number: %s" % value)


def main():
    args = parse_args()
    try:
        actual = json.load(sys.stdin, parse_constant=_reject_non_finite)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
        print("invalid benchmark environment JSON: %s" % error, file=sys.stderr)
        return 1
    if not isinstance(actual, dict):
        print("invalid benchmark environment JSON: top-level value must be an object", file=sys.stderr)
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

    try:
        canonical = (
            json.dumps(
                actual,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        print("cannot canonicalize benchmark environment: %s" % error, file=sys.stderr)
        return 1

    environment_sha256 = hashlib.sha256(canonical).hexdigest()
    if args.canonical_output is not None:
        try:
            write_canonical_output(args.canonical_output, canonical)
        except FileExistsError:
            print(
                "canonical benchmark environment already exists: %s"
                % args.canonical_output,
                file=sys.stderr,
            )
            return 1
        except OSError as error:
            print(
                "cannot write canonical benchmark environment %s: %s"
                % (args.canonical_output, error),
                file=sys.stderr,
            )
            return 1

    print(
        "benchmark environment verified "
        "(pool=100, logs=OFF, maxAttempts=%d)" % args.expected_max_attempts
    )
    print("environment_sha256=" + environment_sha256)
    return 0


if __name__ == "__main__":
    sys.exit(main())
