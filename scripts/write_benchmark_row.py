#!/usr/bin/env python3
"""Atomically create or append one validated methodology-v2 CSV row."""

import argparse
import csv
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, TextIO

from benchmark_schema import FIELDS, normalize_and_validate_row, validate_csv_row


def write_new(path: Path, row: Dict[str, str]) -> None:
    """Exclusively publish a new CSV containing its header and one row."""
    validated = validate_csv_row(row)
    temporary = _write_temporary(path, [validated])
    try:
        os.link(str(temporary), str(path))
    finally:
        _remove_temporary(temporary)


def append(path: Path, row: Dict[str, str]) -> None:
    """Validate the complete CSV and atomically append one row by replacement."""
    validated = validate_csv_row(row)
    existing = _read_validated_rows(path)
    temporary = _write_temporary(path, existing + [validated])
    try:
        os.replace(str(temporary), str(path))
    finally:
        _remove_temporary(temporary)


def _read_validated_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != FIELDS:
            raise ValueError("CSV header does not exactly match methodology v2")
        rows = []
        for line_number, decoded in enumerate(reader, start=2):
            if None in decoded or any(value is None for value in decoded.values()):
                raise ValueError("malformed CSV row at line %d" % line_number)
            try:
                rows.append(validate_csv_row(decoded))
            except ValueError as error:
                raise ValueError("invalid CSV row at line %d: %s" % (line_number, error)) from error
        return rows


def _write_temporary(path: Path, rows: List[Dict[str, str]]) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=".%s." % path.name,
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(name)
    handle = None
    try:
        handle = os.fdopen(descriptor, "w", newline="", encoding="utf-8")
        descriptor = None
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        _flush(handle)
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        return temporary
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


def _flush(handle: TextIO) -> None:
    handle.flush()


def _remove_temporary(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _decode_exact_object(source: str) -> object:
    decoder = json.JSONDecoder()
    stripped = source.lstrip()
    if not stripped:
        raise ValueError("stdin must contain one JSON object")
    try:
        payload, end = decoder.raw_decode(stripped)
    except json.JSONDecodeError as error:
        raise ValueError("invalid JSON input: %s" % error.msg) from error
    if stripped[end:].strip():
        raise ValueError("stdin must contain exactly one JSON object")
    return payload


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--create", action="store_true")
    mode.add_argument("--append", action="store_true")
    parser.add_argument("path", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        payload = _decode_exact_object(sys.stdin.read())
        row = normalize_and_validate_row(payload)
        if args.create:
            write_new(args.path, row)
        else:
            append(args.path, row)
    except (OSError, ValueError, csv.Error, UnicodeError) as error:
        message = str(error).replace("\r", " ").replace("\n", " ")
        print("write_benchmark_row.py: error: %s" % message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
