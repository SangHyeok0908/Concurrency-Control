#!/usr/bin/env python3
"""Render validated benchmark evidence as Markdown tables and Mermaid charts."""

import argparse
import csv
import statistics
import sys
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

from benchmark_schema import FIELDS as V2_FIELDS
from validate_benchmark_campaign import validate_campaign, validate_rows


LEGACY_FIELDS = [
    "ts", "phase", "round", "strategy", "optimistic_max_attempts",
    "contention", "capacity", "contenders", "slot_id", "requests", "ok",
    "ko", "mean_ms", "p95_ms", "max_ms", "tps", "remaining", "confirmed",
    "overbooking", "duplicates", "retry_succeeded", "retry_exhausted",
    "version_conflicts", "deadlocks", "mean_attempts",
]

# Legacy labels and ordering are deliberately frozen for byte-for-byte v1 output.
LEGACY_LABELS = {
    ("baseline", 5): "baseline (방어 없음)",
    ("unique", 5): "① UNIQUE",
    ("conditional", 5): "② 조건부 UPDATE",
    ("pessimistic", 5): "④ 비관적 락",
    ("optimistic", 5): "⑤ 낙관적 락 (상한 5)",
    ("optimistic", 20): "⑤ 낙관적 락 (상한 20)",
}
LEGACY_ORDER = list(LEGACY_LABELS)
LEGACY_SHORT = {
    ("baseline", 5): "baseline",
    ("unique", 5): "① UNIQUE",
    ("conditional", 5): "② 조건부",
    ("pessimistic", 5): "④ 비관적",
    ("optimistic", 5): "⑤ 낙관@5",
    ("optimistic", 20): "⑤ 낙관@20",
}

STRATEGIES = ("baseline", "unique", "conditional", "pessimistic", "optimistic")
V2_LABELS = {
    "baseline": "baseline (방어 없음)",
    "unique": "① UNIQUE",
    "conditional": "② 조건부 UPDATE",
    "pessimistic": "④ 비관적 락",
    "optimistic": "⑤ 낙관적 락",
}
V2_SHORT = {
    "baseline": "baseline",
    "unique": "① UNIQUE",
    "conditional": "② 조건부",
    "pessimistic": "④ 비관적",
    "optimistic": "⑤ 낙관",
}
POINTS = (
    ("low", "낮은 경합 `cap=100 cont=120`", "낮은 경합", "낮음"),
    ("extreme", "극단 경합 `cap=1 cont=200`", "극단 경합", "극단"),
)
PHASES = (("a", "A", 5), ("b", "B", 20))


def med_range(values):
    """Legacy median/range formatting. Do not change: v1 output is immutable."""
    if not values:
        return "—", "—"
    med = statistics.median(values)
    med = f"{med:.0f}" if med == int(med) else f"{med:.1f}"
    lo, hi = min(values), max(values)
    rng = "—" if lo == hi else f"{lo:g}–{hi:g}"
    return med, rng


def render_legacy(rows):
    """Return the original methodology-v1 renderer output without modification."""
    grouped = defaultdict(list)
    for row in rows:
        grouped[(
            row["strategy"], int(row["optimistic_max_attempts"]), row["contention"],
        )].append(row)

    out = []
    for point_key, point_title, _chart_title, _short_title in POINTS:
        out.append(f"\n### {point_title}\n")
        out.append("| 전략 | 확정 예약 | 오버부킹 | 중복 | KO(실패) | 평균 응답 (중앙값) | p95 | TPS | n |")
        out.append("|---|---|---|---|---|---|---|---|---|")
        for key in LEGACY_ORDER:
            runs = grouped.get((key[0], key[1], point_key))
            if not runs:
                continue
            n = len(runs)
            confirmed, _ = med_range([int(row["confirmed"]) for row in runs])
            overbook = max(int(row["overbooking"]) for row in runs)
            duplicates = max(int(row["duplicates"]) for row in runs)
            ko_med, _ko_range = med_range([int(row["ko"]) for row in runs])
            mean_med, mean_range = med_range([int(row["mean_ms"]) for row in runs])
            p95_med, _ = med_range([int(row["p95_ms"]) for row in runs])
            tps_med, _ = med_range([float(row["tps"]) for row in runs])
            ko_cell = f"**{ko_med}**" if ko_med != "0" else "0"
            overbook_cell = f"**{overbook}**" if overbook else "0"
            out.append(
                f"| {LEGACY_LABELS[key]} | {confirmed} | {overbook_cell} | {duplicates} | {ko_cell} "
                f"| {mean_med} <sub>({mean_range})</sub> | {p95_med} | {tps_med} | {n} |"
            )

    out.append("\n### ② vs ④ — 라운드별 짝비교 (평균 응답, ms)\n")
    out.append("인터리브로 쟀으므로 **같은 라운드의 두 값은 거의 같은 머신 상태**에서 나왔다. ")
    out.append("실행 간 편차가 상쇄되어 전략 간 차이만 남는다 — 중앙값 표의 범위가 겹칠 때 방향을 가리는 근거다.\n")
    out.append("| 경합 | 라운드 | ② 조건부 | ④ 비관적 | 차(②−④) | 빠른 쪽 |")
    out.append("|---|---|---|---|---|---|")
    for point_key, _point_title, _chart_title, _short_title in POINTS:
        conditional_wins = 0
        pessimistic_wins = 0
        ties = 0
        lines = []
        rounds = sorted({
            int(row["round"]) for row in rows if row["contention"] == point_key
        })
        for measured_round in rounds:
            def pick(strategy, cap=5):
                matches = [
                    row for row in grouped.get((strategy, cap, point_key), [])
                    if int(row["round"]) == measured_round
                ]
                return int(matches[0]["mean_ms"]) if matches else None

            conditional, pessimistic = pick("conditional"), pick("pessimistic")
            if conditional is None or pessimistic is None:
                continue
            difference = conditional - pessimistic
            if difference < 0:
                conditional_wins += 1
                faster = "②"
            elif difference > 0:
                pessimistic_wins += 1
                faster = "④"
            else:
                ties += 1
                faster = "동률"
            label = "낮음" if point_key == "low" else "극단"
            lines.append(
                f"| {label} | {measured_round} | {conditional} | {pessimistic} "
                f"| {difference:+d} | **{faster}** |"
            )
        out.extend(lines)
        label = "낮은 경합" if point_key == "low" else "극단 경합"
        score = f"② {conditional_wins}승 / ④ {pessimistic_wins}승"
        if ties:
            score += f" / 동률 {ties}"
        out.append(f"| **{label} 합계** | | | | | **{score}** |")

    out.append("\n### ⑤ 낙관적 락 — 재시도 분포\n")
    out.append("| 상한 | 경합 | 성공 | 재시도 소진(503) | 버전 충돌 | 데드락 | 성공당 평균 시도 |")
    out.append("|---|---|---|---|---|---|---|")
    for cap in (5, 20):
        for point_key, _point_title, _chart_title, _short_title in POINTS:
            runs = grouped.get(("optimistic", cap, point_key))
            if not runs:
                continue
            succeeded, _ = med_range([int(row["retry_succeeded"]) for row in runs])
            exhausted, exhausted_range = med_range([
                int(row["retry_exhausted"]) for row in runs
            ])
            conflicts, conflicts_range = med_range([
                int(row["version_conflicts"]) for row in runs
            ])
            deadlocks = max(int(row["deadlocks"]) for row in runs)
            attempts, _ = med_range([float(row["mean_attempts"]) for row in runs])
            label = "낮음 `cap=100`" if point_key == "low" else "극단 `cap=1`"
            out.append(
                f"| {cap} | {label} | {succeeded} | {exhausted} <sub>({exhausted_range})</sub> "
                f"| {conflicts} <sub>({conflicts_range})</sub> | {deadlocks} | {attempts} |"
            )

    for point_key, point_title, _chart_title, _short_title in POINTS:
        names, values = [], []
        for key in LEGACY_ORDER:
            runs = grouped.get((key[0], key[1], point_key))
            if not runs:
                continue
            names.append(LEGACY_SHORT[key])
            values.append(statistics.median([int(row["mean_ms"]) for row in runs]))
        if not values:
            continue
        out.append(
            f"\n```mermaid\nxychart-beta\n    title \"평균 응답시간 중앙값 (ms) — "
            f"{point_title.split('`')[0].strip()}\""
        )
        out.append("    x-axis [" + ", ".join(f'\"{name}\"' for name in names) + "]")
        out.append(f"    y-axis \"ms\" 0 --> {int(max(values) * 1.2) + 1}")
        out.append("    bar [" + ", ".join(f"{value:g}" for value in values) + "]\n```")

    return "\n".join(out)


def _as_fraction(value):
    if isinstance(value, Fraction):
        return value
    return Fraction(str(value))


def _exact_median(values):
    ordered = sorted(_as_fraction(value) for value in values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _format_number(value):
    numeric = _as_fraction(value)
    if numeric.denominator == 1:
        return str(numeric.numerator)

    denominator = numeric.denominator
    twos = 0
    fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        return "%d/%d" % (numeric.numerator, numeric.denominator)

    places = max(twos, fives)
    decimal_scale = 10 ** places
    scaled = abs(numeric.numerator) * (decimal_scale // numeric.denominator)
    whole, fraction = divmod(scaled, decimal_scale)
    fraction_text = str(fraction).zfill(places).rstrip("0")
    sign = "-" if numeric.numerator < 0 else ""
    return "%s%d.%s" % (sign, whole, fraction_text)


def _median_range_cell(values):
    exact_values = [_as_fraction(value) for value in values]
    median = _exact_median(exact_values)
    return "%s <sub>(%s–%s)</sub>" % (
        _format_number(median),
        _format_number(min(exact_values)),
        _format_number(max(exact_values)),
    )


def _median(values):
    return _format_number(_exact_median(values))


def _render_v2_phase(out, grouped, phase, phase_label, cap):
    out.append("\n## Phase %s — optimisticMaxAttempts=%d\n" % (phase_label, cap))
    out.append(
        "이 표의 모든 전략은 같은 Phase의 검증된 애플리케이션 기동에서 측정됐다. "
        "각 성능 셀은 실행별 값의 중앙값과 min–max 범위다."
    )
    for contention, point_title, chart_title, _short_title in POINTS:
        out.append("\n### %s\n" % point_title)
        out.append(
            "| 전략 | 확정 예약 중앙값 | 오버부킹 최댓값 | 중복 최댓값 | KO 중앙값 "
            "| 실행별 평균 응답의 중앙값 (min–max) (ms) "
            "| 실행별 p95의 중앙값 (min–max) (ms) "
            "| 버스트 TPS의 중앙값 (min–max) (requests/s) | n |"
        )
        out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for strategy in STRATEGIES:
            runs = grouped[(phase, strategy, contention)]
            label = V2_LABELS[strategy]
            if strategy == "optimistic":
                label += " (상한 %d)" % cap
            out.append(
                "| %s | %s | %d | %d | %s | %s | %s | %s | %d |" % (
                    label,
                    _median([int(row["confirmed"]) for row in runs]),
                    max(int(row["overbooking"]) for row in runs),
                    max(int(row["duplicates"]) for row in runs),
                    _median([int(row["ko"]) for row in runs]),
                    _median_range_cell([int(row["mean_ms"]) for row in runs]),
                    _median_range_cell([int(row["p95_ms"]) for row in runs]),
                    _median_range_cell([row["tps"] for row in runs]),
                    len(runs),
                )
            )

        names = [V2_SHORT[strategy] for strategy in STRATEGIES]
        values = [
            _exact_median([
                int(row["mean_ms"]) for row in grouped[(phase, strategy, contention)]
            ])
            for strategy in STRATEGIES
        ]
        out.append(
            "\n```mermaid\nxychart-beta\n"
            "    title \"평균 응답시간 중앙값 (ms) — Phase %s / %s\""
            % (phase_label, chart_title)
        )
        out.append("    x-axis [" + ", ".join('"%s"' % name for name in names) + "]")
        y_axis_max = (max(values) * 6) // 5 + 1
        out.append("    y-axis \"ms\" 0 --> %d" % y_axis_max)
        out.append("    bar [" + ", ".join(_format_number(value) for value in values) + "]\n```")


def _render_v2_pairs(out, rows):
    pairs = defaultdict(dict)
    for row in rows:
        if row["strategy"] not in ("conditional", "pessimistic"):
            continue
        key = (
            row["campaign_id"], row["phase"], int(row["measured_round"]),
            row["contention"],
        )
        pairs[key][row["strategy"]] = row

    out.append("\n## ② vs ④ — Phase 내부 라운드별 비교 (평균 응답, ms)\n")
    out.append(
        "같은 캠페인·Phase·측정 라운드·경합으로만 연결한다. 두 실행은 서로 다른 직렬 위치에서 "
        "수행되므로 직렬 위치와 실행 시점 차이가 남는다."
    )
    out.append("\n| Phase | 경합 | 라운드 | ② 위치 | ② 조건부 | ④ 위치 | ④ 비관적 | 차(②−④) |")
    out.append("|---|---|---:|---:|---:|---:|---:|---:|")
    campaign_id = rows[0]["campaign_id"]
    for phase, phase_label, _cap in PHASES:
        for contention, _point_title, _chart_title, short_title in POINTS:
            rounds = sorted({
                int(row["measured_round"])
                for row in rows
                if row["phase"] == phase and row["contention"] == contention
            })
            for measured_round in rounds:
                pair = pairs[(campaign_id, phase, measured_round, contention)]
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


def _render_v2_retry(out, grouped):
    out.append("\n## ⑤ 낙관적 락 — Phase별 재시도 분포\n")
    out.append("| Phase | 상한 | 경합 | 성공 중앙값 | 소진 중앙값 (min–max) | 버전 충돌 중앙값 (min–max) | 데드락 최댓값 | 성공당 평균 시도 중앙값 (min–max) |")
    out.append("|---|---:|---|---:|---:|---:|---:|---:|")
    for phase, phase_label, cap in PHASES:
        for contention, _point_title, _chart_title, short_title in POINTS:
            runs = grouped[(phase, "optimistic", contention)]
            out.append(
                "| %s | %d | %s | %s | %s | %s | %d | %s |" % (
                    phase_label,
                    cap,
                    short_title,
                    _median([int(row["retry_succeeded"]) for row in runs]),
                    _median_range_cell([int(row["retry_exhausted"]) for row in runs]),
                    _median_range_cell([int(row["version_conflicts"]) for row in runs]),
                    max(int(row["deadlocks"]) for row in runs),
                    _median_range_cell([row["mean_attempts"] for row in runs]),
                )
            )
    out.append("\n### 상한 5/20 비교 해석\n")
    out.append(
        "Phase A의 상한 5와 Phase B의 상한 20은 서로 다른 app_start_id에서 얻은 "
        "**앱 기동 간 민감도 분석**이다. 같은 프로세스에서 얻은 짝비교가 아니다. "
        "각 상한은 반드시 해당 Phase의 대조 전략과 비교한다."
    )


def render_v2(rows, summary):
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["phase"], row["strategy"], row["contention"])].append(row)

    out = [
        "# 벤치마크 방법론 v2 요약 — 캠페인 `%s`" % summary.campaign_id,
        "",
        "- Phase당 측정 라운드: %d" % summary.measured_rounds,
        "- 집계 단위: 각 셀은 개별 실행값의 중앙값과 min–max 범위",
    ]
    for phase, phase_label, cap in PHASES:
        _render_v2_phase(out, grouped, phase, phase_label, cap)
    _render_v2_pairs(out, rows)
    _render_v2_retry(out, grouped)
    return "\n".join(out)


def _read_exact_csv(path, expected_fields, mode_name):
    with Path(path).open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, strict=True)
        if reader.fieldnames != expected_fields:
            raise ValueError("CSV header does not exactly match %s" % mode_name)
        rows = []
        for line_number, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError("malformed CSV row at line %d" % line_number)
            rows.append(row)
        return rows


class _OneLineArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "%s: error: %s\n" % (Path(self.prog).name, _one_line(message)))


def _parse_args(argv=None):
    parser = _OneLineArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--campaign-id")
    parser.add_argument("--legacy-v1", action="store_true")
    args = parser.parse_args(argv)
    if args.legacy_v1 and args.campaign_id is not None:
        parser.error("--campaign-id cannot be used with --legacy-v1")
    return args


def _one_line(value):
    return str(value).replace("\r", " ").replace("\n", " ")


def _load_validated_v2(path, selected_campaign_id):
    rows = _read_exact_csv(path, V2_FIELDS, "methodology v2")
    if selected_campaign_id is not None:
        selected = [
            row for row in rows if row["campaign_id"] == selected_campaign_id
        ]
        summary = validate_rows(
            selected,
            requirement="complete",
            campaign_id=selected_campaign_id,
        )
        return selected, summary

    campaign_ids = {row["campaign_id"] for row in rows}
    if len(campaign_ids) > 1:
        raise ValueError("multiple campaign_id values require --campaign-id")
    file_summary = validate_campaign(path, requirement="complete")
    snapshot_summary = validate_rows(
        rows,
        requirement="complete",
        campaign_id=file_summary.campaign_id,
    )
    return rows, snapshot_summary


def main(argv=None):
    args = _parse_args(argv)
    try:
        if args.legacy_v1:
            rows = _read_exact_csv(args.csv_path, LEGACY_FIELDS, "methodology v1")
            output = render_legacy(rows)
        else:
            rows, summary = _load_validated_v2(args.csv_path, args.campaign_id)
            output = render_v2(rows, summary)
    except (OSError, ValueError, csv.Error, UnicodeError) as error:
        print("summarize_benchmark.py: error: %s" % _one_line(error), file=sys.stderr)
        return 1

    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
