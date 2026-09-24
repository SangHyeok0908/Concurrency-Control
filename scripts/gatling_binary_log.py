"""Decode Gatling 3.13.5 binary logs and measure an exact request-label burst."""

import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class RunHeader:
    version: str
    simulation_class: str
    run_start_epoch_ms: int
    description: str


@dataclass(frozen=True)
class RequestEvent:
    name: str
    start_epoch_ms: int
    end_epoch_ms: int
    ok: bool
    message: str


@dataclass(frozen=True)
class BurstMetrics:
    requests: int
    ok: int
    ko: int
    burst_start_epoch_ms: int
    burst_end_epoch_ms: int
    burst_wall_ms: int
    tps: float


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.position = 0
        self.cache: Dict[int, str] = {}

    def take(self, size: int) -> bytes:
        if size < 0 or size > len(self.data) - self.position:
            raise ValueError("truncated or invalid field at byte {}".format(self.position))
        result = self.data[self.position:self.position + size]
        self.position += size
        return result

    def byte(self) -> int:
        return self.take(1)[0]

    def i32(self) -> int:
        return struct.unpack(">i", self.take(4))[0]

    def i64(self) -> int:
        return struct.unpack(">q", self.take(8))[0]

    def count(self) -> int:
        value = self.i32()
        if value < 0:
            raise ValueError("negative count or length at byte {}".format(self.position - 4))
        return value

    def boolean(self) -> bool:
        value = self.byte()
        if value not in (0, 1):
            raise ValueError("invalid boolean: {}".format(value))
        return value == 1

    def string(self) -> str:
        size = self.count()
        if size == 0:
            return ""
        data = self.take(size)
        coder = self.byte()
        if coder == 0:
            return data.decode("latin-1")
        if coder == 1:
            encoding = "utf-16-le" if sys.byteorder == "little" else "utf-16-be"
            return data.decode(encoding)
        raise ValueError("unsupported string coder: {}".format(coder))

    def cached_string(self) -> str:
        index = self.i32()
        if index < 0:
            if -index not in self.cache:
                raise ValueError("missing cached string: {}".format(-index))
            return self.cache[-index]
        if index in self.cache:
            raise ValueError("duplicate cached string: {}".format(index))
        value = self.string()
        self.cache[index] = value
        return value

    def groups(self) -> None:
        for _ in range(self.count()):
            self.cached_string()

    def interval(self) -> Tuple[int, int]:
        start, end = self.i32(), self.i32()
        if end < start:
            raise ValueError("record end precedes start: {} < {}".format(end, start))
        return start, end


def read_request_events(log_path: Path) -> Tuple[RunHeader, List[RequestEvent]]:
    """Decode every record, rejecting unsupported formats and incomplete fields."""
    reader = _Reader(log_path.read_bytes())
    if reader.byte() != 0:
        raise ValueError("first record must be Run")
    version = reader.string()
    if version != "3.13.5":
        raise ValueError("unsupported Gatling version: {}".format(version))
    header = RunHeader(version, reader.string(), reader.i64(), reader.string())
    for _ in range(reader.count()):
        reader.string()  # Scenario names are not part of the cached-string table.
    for _ in range(reader.count()):
        reader.take(reader.count())  # Each assertion is a length-prefixed opaque byte block.

    events: List[RequestEvent] = []
    while reader.position < len(reader.data):
        tag = reader.byte()
        if tag == 1:
            reader.groups()
            name = reader.cached_string()
            start, end = reader.interval()
            ok = reader.boolean()
            message = reader.cached_string()
            events.append(RequestEvent(name, header.run_start_epoch_ms + start,
                                       header.run_start_epoch_ms + end, ok, message))
        elif tag == 2:
            reader.i32()  # Scenario index.
            reader.boolean()
            reader.i32()  # Timestamp offset.
        elif tag == 3:
            reader.groups()
            reader.interval()
            reader.i32()  # Cumulative response time.
            reader.boolean()
        elif tag == 4:
            reader.cached_string()
            reader.i32()  # Timestamp offset.
        elif tag == 0:
            raise ValueError("second Run record is invalid")
        else:
            raise ValueError("unsupported record tag: {}".format(tag))
    return header, events


def burst_metrics(log_path: Path, exact_label: str,
                  expected_requests: Optional[int] = None) -> BurstMetrics:
    """Count matching OK/KO requests over their first-start to last-end interval."""
    _, events = read_request_events(log_path)
    matching = [event for event in events if event.name == exact_label]
    requests = len(matching)
    if expected_requests is not None and requests != expected_requests:
        raise ValueError("request count mismatch: expected {}, log has {}".format(expected_requests, requests))
    if not matching:
        raise ValueError("no requests match label: {}".format(exact_label))
    start = min(event.start_epoch_ms for event in matching)
    end = max(event.end_epoch_ms for event in matching)
    wall = end - start
    if wall <= 0:
        raise ValueError("burst wall interval must be positive")
    ok = sum(event.ok for event in matching)
    return BurstMetrics(requests, ok, requests - ok, start, end, wall,
                        round(requests * 1000.0 / wall, 1))
