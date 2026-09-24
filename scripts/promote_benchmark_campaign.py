#!/usr/bin/env python3
"""Atomically promote one canonical methodology-v2 benchmark campaign."""

import argparse
import csv
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import BinaryIO

from validate_benchmark_campaign import CampaignSummary, validate_campaign


def _copy(source: BinaryIO, destination: BinaryIO) -> None:
    shutil.copyfileobj(source, destination)


def _flush(handle: BinaryIO) -> None:
    handle.flush()


def _close(handle: BinaryIO) -> None:
    handle.close()


def _remove_temporary(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def promote_campaign(source: Path, destination: Path) -> CampaignSummary:
    """Copy, validate, and exclusively publish exactly one 10-round campaign."""
    source = Path(source)
    destination = Path(destination)
    descriptor, name = tempfile.mkstemp(
        prefix=".promote-%s." % destination.name,
        suffix=".tmp",
        dir=str(destination.parent),
    )
    temporary = Path(name)
    handle = None
    try:
        # Keep the exclusive descriptor returned by mkstemp. Reopening the
        # pathname would let a replaced symlink redirect the copied bytes.
        handle = os.fdopen(descriptor, "wb")
        descriptor = None
        with source.open("rb") as source_handle:
            _copy(source_handle, handle)
        _flush(handle)
        os.fsync(handle.fileno())
        _close(handle)
        handle = None

        summary = validate_campaign(
            temporary, "complete", expected_rounds=10,
        )
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
        return summary


class _OneLineArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "%s: error: %s\n" % (Path(self.prog).name, _one_line(message)))


def _parse_args(argv=None):
    parser = _OneLineArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--to", required=True, type=Path, dest="destination")
    return parser.parse_args(argv)


def _one_line(value: object) -> str:
    return str(value).replace("\r", " ").replace("\n", " ")


def main(argv=None) -> int:
    args = _parse_args(argv)
    try:
        summary = promote_campaign(args.source, args.destination)
    except (OSError, ValueError, csv.Error, UnicodeError) as error:
        print(
            "promote_benchmark_campaign.py: error: %s" % _one_line(error),
            file=sys.stderr,
        )
        return 1

    print(
        "benchmark campaign promoted "
        "(campaign=%s, rounds=%d, destination=%s)"
        % (summary.campaign_id, summary.measured_rounds, args.destination)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
