#!/usr/bin/env python3
import csv
import io
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
from validate_benchmark_campaign import CampaignSummary  # noqa: E402
import summarize_benchmark as summarizer  # noqa: E402


SUMMARIZER = SCRIPTS / "summarize_benchmark.py"
LEGACY_FIELDS = [
    "ts", "phase", "round", "strategy", "optimistic_max_attempts",
    "contention", "capacity", "contenders", "slot_id", "requests", "ok",
    "ko", "mean_ms", "p95_ms", "max_ms", "tps", "remaining", "confirmed",
    "overbooking", "duplicates", "retry_succeeded", "retry_exhausted",
    "version_conflicts", "deadlocks", "mean_attempts",
]
MEASUREMENT_FIELDS = FIELDS[17:37]


def build_phase(rounds=10, phase="a", campaign_id="campaign-001", slot_start=1,
                metric_offset=0):
    rows = []
    base_time = datetime(2026, 9, 24, 10, 0, 0)
    environment_sha256 = ("a" if phase == "a" else "b") * 64
    max_attempts = 5 if phase == "a" else 20
    for offset, entry in enumerate(build_schedule(rounds)):
        treatment = entry.treatment
        slot_id = slot_start + offset
        mean_ms = metric_offset + (treatment.treatment_id + 1) * 100 + entry.measured_round
        p95_ms = mean_ms + 50
        burst_wall_ms = 900 + treatment.treatment_id * 10 + entry.measured_round
        start_ms = 1_800_000_000_000 + slot_id * 2000
        confirmed = treatment.capacity
        payload = {
            "schema_version": 2,
            "campaign_id": campaign_id,
            "phase": phase,
            "app_start_id": "%s-phase-%s" % (campaign_id, phase),
            "environment_sha256": environment_sha256,
            "schedule_version": entry.schedule_version,
            "schedule_cycle": entry.schedule_cycle,
            "schedule_row": entry.schedule_row,
            "measured_round": entry.measured_round,
            "position_in_round": entry.position_in_round,
            "treatment_id": treatment.treatment_id,
            "ts": (base_time + timedelta(seconds=slot_id)).isoformat(),
            "strategy": treatment.strategy,
            "optimistic_max_attempts": max_attempts,
            "contention": treatment.contention,
            "capacity": treatment.capacity,
            "contenders": treatment.contenders,
            "slot_id": slot_id,
            "requests": treatment.contenders,
            "ok": treatment.contenders,
            "ko": 0,
            "mean_ms": mean_ms,
            "p95_ms": p95_ms,
            "max_ms": p95_ms + 50,
            "burst_start_epoch_ms": start_ms,
            "burst_end_epoch_ms": start_ms + burst_wall_ms,
            "burst_wall_ms": burst_wall_ms,
            "tps": round(treatment.contenders * 1000.0 / burst_wall_ms, 1),
            "remaining": treatment.capacity - confirmed,
            "confirmed": confirmed,
            "overbooking": 0,
            "duplicates": 0,
            "retry_succeeded": treatment.contenders if treatment.strategy == "optimistic" else 0,
            "retry_exhausted": 0,
            "version_conflicts": entry.measured_round if treatment.strategy == "optimistic" else 0,
            "deadlocks": 0,
            "mean_attempts": 1.5 if treatment.strategy == "optimistic" else 0,
            "run_status": "ok",
            "error_reason": "",
        }
        rows.append(normalize_and_validate_row(payload))
    return rows


def build_rows(rounds=10, campaign_id="campaign-001", metric_offset=0):
    phase_a = build_phase(
        rounds, "a", campaign_id, slot_start=1, metric_offset=metric_offset,
    )
    phase_b = build_phase(
        rounds, "b", campaign_id, slot_start=len(phase_a) + 1,
        metric_offset=metric_offset + 1000,
    )
    return phase_a + phase_b


def failed_row(row):
    result = dict(row)
    for field in MEASUREMENT_FIELDS:
        result[field] = ""
    result["run_status"] = "seed_failed"
    result["error_reason"] = "controlled failure"
    return result


def csv_text(rows, fields=FIELDS):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def legacy_pair_rows():
    base = dict.fromkeys(LEGACY_FIELDS, "0")
    base.update({
        "ts": "2026-09-24T10:00:00",
        "phase": "a",
        "round": "1",
        "optimistic_max_attempts": "5",
        "contention": "low",
        "capacity": "100",
        "contenders": "120",
        "slot_id": "1",
        "requests": "120",
        "ok": "120",
        "mean_ms": "136",
        "p95_ms": "190",
        "max_ms": "200",
        "tps": "600.0",
        "confirmed": "100",
    })
    rows = []
    for index, strategy in enumerate(("conditional", "pessimistic"), start=1):
        row = dict(base)
        row["strategy"] = strategy
        row["slot_id"] = str(index)
        rows.append(row)
    return rows


LEGACY_PAIR_GOLDEN = """
### 낮은 경합 `cap=100 cont=120`

| 전략 | 확정 예약 | 오버부킹 | 중복 | KO(실패) | 평균 응답 (중앙값) | p95 | TPS | n |
|---|---|---|---|---|---|---|---|---|
| ② 조건부 UPDATE | 100 | 0 | 0 | 0 | 136 <sub>(—)</sub> | 190 | 600 | 1 |
| ④ 비관적 락 | 100 | 0 | 0 | 0 | 136 <sub>(—)</sub> | 190 | 600 | 1 |

### 극단 경합 `cap=1 cont=200`

| 전략 | 확정 예약 | 오버부킹 | 중복 | KO(실패) | 평균 응답 (중앙값) | p95 | TPS | n |
|---|---|---|---|---|---|---|---|---|

### ② vs ④ — 라운드별 짝비교 (평균 응답, ms)

인터리브로 쟀으므로 **같은 라운드의 두 값은 거의 같은 머신 상태**에서 나왔다.\x20
실행 간 편차가 상쇄되어 전략 간 차이만 남는다 — 중앙값 표의 범위가 겹칠 때 방향을 가리는 근거다.

| 경합 | 라운드 | ② 조건부 | ④ 비관적 | 차(②−④) | 빠른 쪽 |
|---|---|---|---|---|---|
| 낮음 | 1 | 136 | 136 | +0 | **동률** |
| **낮은 경합 합계** | | | | | **② 0승 / ④ 0승 / 동률 1** |
| **극단 경합 합계** | | | | | **② 0승 / ④ 0승** |

### ⑤ 낙관적 락 — 재시도 분포

| 상한 | 경합 | 성공 | 재시도 소진(503) | 버전 충돌 | 데드락 | 성공당 평균 시도 |
|---|---|---|---|---|---|---|

```mermaid
xychart-beta
    title "평균 응답시간 중앙값 (ms) — 낮은 경합"
    x-axis ["② 조건부", "④ 비관적"]
    y-axis "ms" 0 --> 164
    bar [136, 136]
```
"""


class SummarizerHarness:

    def __init__(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)

    def close(self):
        self._temporary.cleanup()

    def write(self, name, rows, fields=FIELDS):
        path = self.root / name
        path.write_text(csv_text(rows, fields), encoding="utf-8")
        return path

    def run(self, path, *arguments):
        return subprocess.run(
            [sys.executable, str(SUMMARIZER), str(path), *arguments],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )


class LegacySummaryRegressionTest(unittest.TestCase):

    def setUp(self):
        self.harness = SummarizerHarness()

    def tearDown(self):
        self.harness.close()

    def test_legacy_mode_preserves_the_existing_renderer_byte_for_byte(self):
        path = self.harness.write("legacy.csv", legacy_pair_rows(), LEGACY_FIELDS)

        result = self.harness.run(path, "--legacy-v1")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(LEGACY_PAIR_GOLDEN, result.stdout)
        self.assertIn("② 0승 / ④ 0승 / 동률 1", result.stdout)

    def test_legacy_and_v2_inputs_require_their_explicit_modes(self):
        legacy = self.harness.write("legacy.csv", legacy_pair_rows(), LEGACY_FIELDS)
        v2 = self.harness.write("v2.csv", build_rows())

        default_legacy = self.harness.run(legacy)
        legacy_v2 = self.harness.run(v2, "--legacy-v1")
        incompatible_options = self.harness.run(
            legacy, "--legacy-v1", "--campaign-id", "campaign-001",
        )

        for result in (default_legacy, legacy_v2, incompatible_options):
            self.assertNotEqual(0, result.returncode)
            self.assertEqual("", result.stdout)
            self.assertIn("error", result.stderr.lower())


class MethodologyV2SummaryTest(unittest.TestCase):

    def setUp(self):
        self.harness = SummarizerHarness()

    def tearDown(self):
        self.harness.close()

    def test_renders_phase_local_tables_ranges_pairs_and_charts(self):
        rows = build_rows()
        for row in rows:
            if (row["phase"], row["strategy"], row["contention"]) == (
                    "b", "unique", "extreme"):
                row["mean_ms"] = "777"
            elif (row["phase"], row["strategy"], row["contention"]) == (
                    "a", "optimistic", "low"):
                row["mean_attempts"] = "1.00000000000000001"
        path = self.harness.write("complete.csv", rows)

        result = self.harness.run(path)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("캠페인 `campaign-001`", result.stdout)
        self.assertIn("## Phase A — optimisticMaxAttempts=5", result.stdout)
        self.assertIn("## Phase B — optimisticMaxAttempts=20", result.stdout)
        self.assertEqual(2, result.stdout.count("### 낮은 경합 `cap=100 cont=120`"))
        self.assertEqual(2, result.stdout.count("### 극단 경합 `cap=1 cont=200`"))

        phase_a = result.stdout.split("## Phase A", 1)[1].split("## Phase B", 1)[0]
        phase_b = result.stdout.split("## Phase B", 1)[1].split("## ② vs ④", 1)[0]
        for section in (phase_a, phase_b):
            for label in ("baseline (방어 없음)", "① UNIQUE", "② 조건부 UPDATE",
                          "④ 비관적 락", "⑤ 낙관적 락"):
                self.assertIn(label, section)

        self.assertIn("실행별 평균 응답의 중앙값 (min–max)", result.stdout)
        self.assertIn("실행별 p95의 중앙값 (min–max)", result.stdout)
        self.assertIn("버스트 TPS의 중앙값 (min–max)", result.stdout)
        self.assertIn("실행별 평균 응답의 중앙값 (min–max) (ms)", result.stdout)
        self.assertIn("실행별 p95의 중앙값 (min–max) (ms)", result.stdout)
        self.assertIn("버스트 TPS의 중앙값 (min–max) (requests/s)", result.stdout)
        self.assertIn("105.5 <sub>(101–110)</sub>", phase_a)
        self.assertIn("155.5 <sub>(151–160)</sub>", phase_a)
        self.assertIn("132.55 <sub>(131.9–133.2)</sub>", phase_a)
        self.assertIn("777 <sub>(777–777)</sub>", phase_b)

        self.assertIn("| A | 낮음 | 1 | 4 | 301 | 6 | 401 | -100 |", result.stdout)
        self.assertIn("| B | 낮음 | 1 | 4 | 1301 | 6 | 1401 | -100 |", result.stdout)
        self.assertNotIn("거의 같은 머신 상태", result.stdout)
        self.assertIn("직렬 위치와 실행 시점 차이가 남는다", result.stdout)
        self.assertIn("앱 기동 간 민감도 분석", result.stdout)
        self.assertIn("짝비교가 아니다", result.stdout)
        self.assertIn(
            "1.00000000000000001 <sub>(1.00000000000000001–1.00000000000000001)</sub>",
            result.stdout,
        )

        self.assertEqual(4, result.stdout.count("```mermaid"))
        self.assertEqual(
            4,
            result.stdout.count(
                'x-axis ["baseline", "① UNIQUE", "② 조건부", "④ 비관적", "⑤ 낙관"]'
            ),
        )
        for phase in ("Phase A", "Phase B"):
            for contention in ("낮은 경합", "극단 경합"):
                self.assertIn(
                    "평균 응답시간 중앙값 (ms) — %s / %s" % (phase, contention),
                    result.stdout,
                )

    def test_campaign_selection_filters_before_validation_and_aggregation(self):
        alpha = build_rows(campaign_id="alpha", metric_offset=0)
        alpha[-1] = failed_row(alpha[-1])
        beta = build_rows(campaign_id="beta", metric_offset=8000)
        path = self.harness.write("diagnostic.csv", alpha + beta)

        result = self.harness.run(path, "--campaign-id", "beta")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("캠페인 `beta`", result.stdout)
        self.assertNotIn("캠페인 `alpha`", result.stdout)
        self.assertIn("8105.5 <sub>(8101–8110)</sub>", result.stdout)
        self.assertNotIn("105.5 <sub>(101–110)</sub>", result.stdout)

    def test_accepts_a_complete_twenty_round_campaign_without_promotion_claims(self):
        path = self.harness.write("twenty.csv", build_rows(rounds=20))

        result = self.harness.run(path)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Phase당 측정 라운드: 20", result.stdout)
        self.assertEqual(4, result.stdout.count("```mermaid"))
        self.assertRegex(
            result.stdout,
            r"\| baseline \(방어 없음\).*\| 20 \|",
        )
        self.assertNotIn("canonical", result.stdout.lower())
        self.assertNotIn("정본 승격", result.stdout)

    def test_preserves_large_integer_metrics_without_float_rounding_or_overflow(self):
        rows = build_rows()
        above_float_precision = "9007199254740993"
        above_float_precision_p95 = "9007199254740995"
        huge_latency = "1" + "0" * 400
        for row in rows:
            group = (row["phase"], row["strategy"], row["contention"])
            if group == ("a", "baseline", "low"):
                row["mean_ms"] = above_float_precision
                row["p95_ms"] = above_float_precision_p95
            elif group == ("a", "unique", "low"):
                row["mean_ms"] = huge_latency
                row["p95_ms"] = huge_latency
        path = self.harness.write("large-metrics.csv", rows)

        result = self.harness.run(path)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(
            "%s <sub>(%s–%s)</sub>" % (
                above_float_precision, above_float_precision, above_float_precision,
            ),
            result.stdout,
        )
        self.assertIn(
            "%s <sub>(%s–%s)</sub>" % (
                above_float_precision_p95,
                above_float_precision_p95,
                above_float_precision_p95,
            ),
            result.stdout,
        )
        self.assertIn(
            "%s <sub>(%s–%s)</sub>" % (
                huge_latency, huge_latency, huge_latency,
            ),
            result.stdout,
        )

    def test_single_campaign_revalidates_the_exact_rows_it_will_render(self):
        path = self.harness.write("snapshot.csv", build_rows(campaign_id="alpha"))
        other_summary = CampaignSummary(
            campaign_id="beta",
            phases=["a", "b"],
            measured_rounds=10,
            rows_per_phase={"a": 100, "b": 100},
            environment_sha256_by_phase={"a": "a" * 64, "b": "b" * 64},
        )

        with mock.patch.object(
                summarizer, "validate_campaign", return_value=other_summary):
            with self.assertRaisesRegex(ValueError, "campaign_id mismatch"):
                summarizer._load_validated_v2(path, None)


class MethodologyV2SummaryRejectionTest(unittest.TestCase):

    def setUp(self):
        self.harness = SummarizerHarness()

    def tearDown(self):
        self.harness.close()

    def assert_rejected_without_stdout(self, name, rows, *arguments):
        path = self.harness.write(name, rows)
        result = self.harness.run(path, *arguments)
        self.assertNotEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stdout)
        self.assertIn("error", result.stderr.lower())

    def test_rejects_incomplete_failed_and_unbalanced_campaigns(self):
        incomplete = build_rows()
        del incomplete[-1]

        failed = build_rows()
        failed[-1] = failed_row(failed[-1])

        unbalanced = build_rows()
        replacement = dict(unbalanced[0])
        replacement["slot_id"] = "999999"
        unbalanced[1] = replacement

        for name, rows in (
            ("incomplete.csv", incomplete),
            ("failed.csv", failed),
            ("unbalanced.csv", unbalanced),
        ):
            with self.subTest(name=name):
                self.assert_rejected_without_stdout(name, rows)

    def test_rejects_unselected_multiple_and_unknown_selected_campaigns(self):
        rows = build_rows(campaign_id="alpha") + build_rows(campaign_id="beta")
        path = self.harness.write("diagnostic.csv", rows)

        unselected = self.harness.run(path)
        unknown = self.harness.run(path, "--campaign-id", "missing")

        for result in (unselected, unknown):
            self.assertNotEqual(0, result.returncode)
            self.assertEqual("", result.stdout)
            self.assertIn("error", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
