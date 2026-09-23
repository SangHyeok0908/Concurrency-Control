#!/usr/bin/env python3
"""raw-runs.csv → 문서에 실을 (전략 × 경합) 2차원 표와 Mermaid 그래프.

**왜 중앙값과 범위인가.** ④는 같은 부하를 7쌍 재고도 "두 범위가 완전히 겹쳐 어느 쪽이 빠르다고
단정할 수 없다"로 끝났다(docs/STEP2-PESSIMISTIC-LOCK.md 4절). 노트북 한 대에서 Gatling·앱·MySQL 이
함께 도는 측정이라 실행 간 편차가 크고, 평균 하나만 적으면 그 편차가 숨는다. 그래서 이 스크립트는
**중앙값과 min–max 범위를 항상 함께** 낸다 — 범위가 겹치면 겹친다고 읽히도록.

정합성(오버부킹·중복)은 통계를 내지 않는다. 최댓값만 본다 — 5회 중 1회라도 오버부킹이 나면
그 방어는 오버부킹을 막지 못하는 것이므로, 평균을 내면 실패가 희석된다.
"""
import csv
import statistics
import sys
from collections import defaultdict

# 표에서 전략을 부르는 이름. 브랜치 전략 문서의 번호를 붙여야 어느 방어인지 즉시 읽힌다.
LABELS = {
    ("baseline", 5): "baseline (방어 없음)",
    ("unique", 5): "① UNIQUE",
    ("conditional", 5): "② 조건부 UPDATE",
    ("pessimistic", 5): "④ 비관적 락",
    ("optimistic", 5): "⑤ 낙관적 락 (상한 5)",
    ("optimistic", 20): "⑤ 낙관적 락 (상한 20)",
}
ORDER = list(LABELS)
# 그래프 x축용 짧은 이름. ⑤ 두 행이 "⑤ 낙관적 락"으로 뭉개지면 상한이 사라져 해석이 불가능해진다.
SHORT = {
    ("baseline", 5): "baseline",
    ("unique", 5): "① UNIQUE",
    ("conditional", 5): "② 조건부",
    ("pessimistic", 5): "④ 비관적",
    ("optimistic", 5): "⑤ 낙관@5",
    ("optimistic", 20): "⑤ 낙관@20",
}
POINTS = [("low", "낮은 경합 `cap=100 cont=120`"), ("extreme", "극단 경합 `cap=1 cont=200`")]


def med_range(values):
    """중앙값과 범위를 함께. 값이 하나뿐이면 범위는 생략한다."""
    if not values:
        return "—", "—"
    med = statistics.median(values)
    med = f"{med:.0f}" if med == int(med) else f"{med:.1f}"
    lo, hi = min(values), max(values)
    rng = "—" if lo == hi else f"{lo:g}–{hi:g}"
    return med, rng


def main():
    rows = list(csv.DictReader(open(sys.argv[1] if len(sys.argv) > 1 else "docs/benchmark/raw-runs.csv")))
    g = defaultdict(list)
    for r in rows:
        g[(r["strategy"], int(r["optimistic_max_attempts"]), r["contention"])].append(r)

    out = []
    for point_key, point_title in POINTS:
        out.append(f"\n### {point_title}\n")
        out.append("| 전략 | 확정 예약 | 오버부킹 | 중복 | KO(실패) | 평균 응답 (중앙값) | p95 | TPS | n |")
        out.append("|---|---|---|---|---|---|---|---|---|")
        for key in ORDER:
            runs = g.get((key[0], key[1], point_key))
            if not runs:
                continue
            n = len(runs)
            confirmed, _ = med_range([int(r["confirmed"]) for r in runs])
            # 정합성은 최댓값. 한 번이라도 터지면 막지 못한 것이다.
            overbook = max(int(r["overbooking"]) for r in runs)
            dup = max(int(r["duplicates"]) for r in runs)
            ko_med, ko_rng = med_range([int(r["ko"]) for r in runs])
            mean_med, mean_rng = med_range([int(r["mean_ms"]) for r in runs])
            p95_med, _ = med_range([int(r["p95_ms"]) for r in runs])
            tps_med, _ = med_range([float(r["tps"]) for r in runs])
            ko_cell = f"**{ko_med}**" if ko_med != "0" else "0"
            ob_cell = f"**{overbook}**" if overbook else "0"
            out.append(
                f"| {LABELS[key]} | {confirmed} | {ob_cell} | {dup} | {ko_cell} "
                f"| {mean_med} <sub>({mean_rng})</sub> | {p95_med} | {tps_med} | {n} |")

    out.append("\n### ② vs ④ — 라운드별 짝비교 (평균 응답, ms)\n")
    out.append("인터리브로 쟀으므로 **같은 라운드의 두 값은 거의 같은 머신 상태**에서 나왔다. ")
    out.append("실행 간 편차가 상쇄되어 전략 간 차이만 남는다 — 중앙값 표의 범위가 겹칠 때 방향을 가리는 근거다.\n")
    out.append("| 경합 | 라운드 | ② 조건부 | ④ 비관적 | 차(②−④) | 빠른 쪽 |")
    out.append("|---|---|---|---|---|---|")
    for point_key, point_title in POINTS:
        wins = 0
        lines = []
        rounds = sorted({int(r["round"]) for r in rows if r["contention"] == point_key})
        for rd in rounds:
            def pick(strategy, cap=5):
                m = [r for r in g.get((strategy, cap, point_key), []) if int(r["round"]) == rd]
                return int(m[0]["mean_ms"]) if m else None
            c, pz = pick("conditional"), pick("pessimistic")
            if c is None or pz is None:
                continue
            diff = c - pz
            wins += 1 if diff < 0 else 0
            faster = "②" if diff < 0 else "④"
            label = "낮음" if point_key == "low" else "극단"
            lines.append(f"| {label} | {rd} | {c} | {pz} | {diff:+d} | **{faster}** |")
        out.extend(lines)
        label = "낮은 경합" if point_key == "low" else "극단 경합"
        out.append(f"| **{label} 합계** | | | | | **② {wins}승 / ④ {len(lines) - wins}승** |")

    out.append("\n### ⑤ 낙관적 락 — 재시도 분포\n")
    out.append("| 상한 | 경합 | 성공 | 재시도 소진(503) | 버전 충돌 | 데드락 | 성공당 평균 시도 |")
    out.append("|---|---|---|---|---|---|---|")
    for cap in (5, 20):
        for point_key, point_title in POINTS:
            runs = g.get(("optimistic", cap, point_key))
            if not runs:
                continue
            succ, _ = med_range([int(r["retry_succeeded"]) for r in runs])
            exh, exh_rng = med_range([int(r["retry_exhausted"]) for r in runs])
            conf, conf_rng = med_range([int(r["version_conflicts"]) for r in runs])
            dl = max(int(r["deadlocks"]) for r in runs)
            att, _ = med_range([float(r["mean_attempts"]) for r in runs])
            label = "낮음 `cap=100`" if point_key == "low" else "극단 `cap=1`"
            out.append(f"| {cap} | {label} | {succ} | {exh} <sub>({exh_rng})</sub> "
                       f"| {conf} <sub>({conf_rng})</sub> | {dl} | {att} |")

    # Mermaid xychart 는 GitHub 이 그대로 렌더링한다 — 이미지 파일도 플로팅 의존성도 필요 없다.
    for point_key, point_title in POINTS:
        names, values = [], []
        for key in ORDER:
            runs = g.get((key[0], key[1], point_key))
            if not runs:
                continue
            names.append(SHORT[key])
            values.append(statistics.median([int(r["mean_ms"]) for r in runs]))
        if not values:
            continue
        out.append(f"\n```mermaid\nxychart-beta\n    title \"평균 응답시간 중앙값 (ms) — {point_title.split('`')[0].strip()}\"")
        out.append("    x-axis [" + ", ".join(f'"{n}"' for n in names) + "]")
        out.append(f"    y-axis \"ms\" 0 --> {int(max(values) * 1.2) + 1}")
        out.append("    bar [" + ", ".join(f"{v:g}" for v in values) + "]\n```")

    print("\n".join(out))


if __name__ == "__main__":
    main()
