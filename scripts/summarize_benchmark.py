#!/usr/bin/env python3
"""Validate one methodology-v2 campaign and render its benchmark evidence."""

import argparse
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from benchmark_capacity import read_rows, validate_rows


STRATEGIES = ("baseline", "unique", "conditional", "pessimistic", "optimistic")
LABELS = {
    "baseline": "baseline (방어 없음)",
    "unique": "① UNIQUE",
    "conditional": "② 조건부 UPDATE",
    "pessimistic": "④ 비관적 락",
    "optimistic": "⑤ 낙관적 락",
}
SHORT_LABELS = {
    "baseline": "baseline",
    "unique": "① UNIQUE",
    "conditional": "② 조건부",
    "pessimistic": "④ 비관적",
    "optimistic": "⑤ 낙관",
}
CONTENTION_LEVELS = (
    ("low", "낮은 경합 `cap=100 cont=120`", "낮은 경합", "낮음"),
    ("extreme", "극단 경합 `cap=1 cont=200`", "극단 경합", "극단"),
)
PHASES = (("a", "A", 5), ("b", "B", 20))


def format_number(value) -> str:
    """Render small benchmark numbers without an unnecessary trailing .0."""
    if isinstance(value, float):
        return format(value, ".12g")
    return str(value)


def _median(values):
    return format_number(statistics.median(values))


def _median_range(values):
    return "%s <sub>(%s–%s)</sub>" % (
        _median(values),
        format_number(min(values)),
        format_number(max(values)),
    )


def _render_phase(out, grouped, phase, label, retry_cap):
    out.append("\n## Phase %s — optimisticMaxAttempts=%d\n" % (label, retry_cap))
    out.append(
        "이 표의 모든 전략은 같은 Phase의 검증된 애플리케이션 기동에서 측정됐다. "
        "각 성능 셀은 실행별 값의 중앙값과 min–max 범위다."
    )
    for contention, title, chart_title, _short in CONTENTION_LEVELS:
        out.append("\n### %s\n" % title)
        out.append(
            "| 전략 | 확정 예약 중앙값 | 오버부킹 최댓값 "
            "| KO 중앙값 (min–max) | 평균 응답 중앙값 (min–max) (ms) "
            "| p95 중앙값 (min–max) (ms) "
            "| 버스트 TPS 중앙값 (min–max) (requests/s) | n |"
        )
        out.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for strategy in STRATEGIES:
            runs = grouped[(phase, strategy, contention)]
            strategy_label = LABELS[strategy]
            if strategy == "optimistic":
                strategy_label += " (상한 %d)" % retry_cap
            out.append(
                "| %s | %s | %d | %s | %s | %s | %s | %d |" % (
                    strategy_label,
                    _median([int(row["confirmed"]) for row in runs]),
                    max(int(row["overbooking"]) for row in runs),
                    _median_range([int(row["ko"]) for row in runs]),
                    _median_range([int(row["mean_ms"]) for row in runs]),
                    _median_range([int(row["p95_ms"]) for row in runs]),
                    _median_range([float(row["tps"]) for row in runs]),
                    len(runs),
                )
            )

        chart_values = [
            statistics.median([
                int(row["mean_ms"])
                for row in grouped[(phase, strategy, contention)]
            ])
            for strategy in STRATEGIES
        ]
        out.append(
            "\n```mermaid\nxychart-beta\n"
            "    title \"평균 응답시간 중앙값 (ms) — Phase %s / %s\""
            % (label, chart_title)
        )
        out.append(
            "    x-axis ["
            + ", ".join('"%s"' % SHORT_LABELS[strategy] for strategy in STRATEGIES)
            + "]"
        )
        out.append("    y-axis \"ms\" 0 --> %d" % (int(max(chart_values) * 1.2) + 1))
        out.append(
            "    bar ["
            + ", ".join(format_number(value) for value in chart_values)
            + "]\n```"
        )


def _render_pairs(out, rows):
    pairs = defaultdict(dict)
    for row in rows:
        if row["strategy"] not in ("conditional", "pessimistic"):
            continue
        key = (row["phase"], int(row["measured_round"]), row["contention"])
        pairs[key][row["strategy"]] = row

    out.append("\n## ② vs ④ — Phase 내부 라운드별 비교 (평균 응답, ms)\n")
    out.append(
        "같은 캠페인·Phase·측정 라운드·경합으로 연결한다. 두 실행의 직렬 위치도 함께 남겨 "
        "순서 차이를 숨기지 않는다."
    )
    out.append("\n| Phase | 경합 | 라운드 | ② 위치 | ② 조건부 | ④ 위치 | ④ 비관적 | 차(②−④) |")
    out.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for phase, phase_label, _retry_cap in PHASES:
        for contention, _title, _chart_title, short_title in CONTENTION_LEVELS:
            rounds = sorted({
                int(row["measured_round"])
                for row in rows
                if row["phase"] == phase and row["contention"] == contention
            })
            for measured_round in rounds:
                pair = pairs[(phase, measured_round, contention)]
                conditional = pair["conditional"]
                pessimistic = pair["pessimistic"]
                conditional_mean = int(conditional["mean_ms"])
                pessimistic_mean = int(pessimistic["mean_ms"])
                out.append(
                    "| %s | %s | %d | %s | %d | %s | %d | %+d |" % (
                        phase_label,
                        short_title,
                        measured_round,
                        conditional["position_in_round"],
                        conditional_mean,
                        pessimistic["position_in_round"],
                        pessimistic_mean,
                        conditional_mean - pessimistic_mean,
                    )
                )


def _render_retry(out, grouped):
    out.append("\n## ⑤ 낙관적 락 — Phase별 재시도 분포\n")
    out.append(
        "| Phase | 상한 | 경합 | 성공 중앙값 | 소진 중앙값 (min–max) "
        "| 버전 충돌 중앙값 (min–max) | 데드락 최댓값 "
        "| 성공당 평균 시도 중앙값 (min–max) |"
    )
    out.append("|---|---:|---|---:|---:|---:|---:|---:|")
    for phase, phase_label, retry_cap in PHASES:
        for contention, _title, _chart_title, short_title in CONTENTION_LEVELS:
            runs = grouped[(phase, "optimistic", contention)]
            out.append(
                "| %s | %d | %s | %s | %s | %s | %d | %s |" % (
                    phase_label,
                    retry_cap,
                    short_title,
                    _median([int(row["retry_succeeded"]) for row in runs]),
                    _median_range([int(row["retry_exhausted"]) for row in runs]),
                    _median_range([int(row["version_conflicts"]) for row in runs]),
                    max(int(row["deadlocks"]) for row in runs),
                    _median_range([float(row["mean_attempts"]) for row in runs]),
                )
            )
    out.append("\nPhase A의 상한 5와 Phase B의 상한 20은 서로 다른 앱 기동의 민감도 분석이다.")


def render(rows: list[dict[str, str]]) -> str:
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["phase"], row["strategy"], row["contention"])].append(row)
    rounds = max(int(row["measured_round"]) for row in rows if row["phase"] == "a")
    out = [
        "# 벤치마크 방법론 v2 요약 — 캠페인 `%s`" % rows[0]["campaign_id"],
        "",
        "- Phase당 측정 라운드: %d" % rounds,
        "- 집계 단위: 실행값의 중앙값과 min–max 범위",
        "",
        "이 표는 **서로 다른 지원자의 정원 경쟁** 결과다. 동일 `(applicant, slot)` 재요청은 "
        "별도 [`duplicate-runs.csv`](benchmark/duplicate-runs.csv)에서 검증한다.",
    ]
    for phase, label, retry_cap in PHASES:
        _render_phase(out, grouped, phase, label, retry_cap)
    _render_pairs(out, rows)
    _render_retry(out, grouped)
    return "\n".join(out)


def _parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    return parser.parse_args(argv)


def _one_line(value):
    return " ".join(str(value).replace("\r", " ").replace("\n", " ").split())


def main(argv=None):
    args = _parse_args(argv)
    try:
        rows = read_rows(args.csv_path)
        phase_a_rounds = [
            int(row["measured_round"]) for row in rows if row["phase"] == "a"
        ]
        if not phase_a_rounds:
            raise ValueError("complete campaign requires Phase A rows")
        rounds = max(phase_a_rounds)
        validate_rows(rows, "complete", rounds)
        output = render(rows)
    except (OSError, ValueError, csv.Error, UnicodeError) as error:
        print("summarize_benchmark.py: error: " + _one_line(error), file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
