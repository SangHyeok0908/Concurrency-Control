#!/usr/bin/env python3
"""Gatling 리포트에서 이번 실행의 `reserve` 요청 통계만 뽑는다.

왜 이름표로 찾는가. 리포트 디렉터리 이름은 `<시뮬레이션 클래스명>-<타임스탬프>` 뿐이라 어느
전략의 측정인지 알 수 없다. ④가 요청 이름에 실행 조건을 박아 둔 것(`reserve [전략 cap=N cont=M]`)이
바로 이 파서를 위한 것이다 — 인자로 받은 조건과 리포트 안의 이름이 정확히 일치할 때만 값을
돌려주므로, 엉뚱한 리포트를 읽어 표에 잘못된 행이 실리는 일이 구조적으로 막힌다.

**TPS 정의.** Gatling 의 `meanNumberOfRequestsPerSecond` 는 쓰지 않는다. 그 값은 시뮬레이션
전체 구간(지원자 120~200명을 만드는 **시드 구간 포함**)으로 나눈 평균이라, 1초 안에 끝나는
버스트의 처리율이 십수 초짜리 시드에 희석돼 버린다(위 확인: 10요청/2초 → 5rps).
이 실험의 부하는 `atOnceUsers` 버스트라 전원이 t=0 에 발사되고 마지막 요청이 최대 응답시간에
끝난다. 따라서 **버스트가 배수되는 속도 = 요청 수 / 최대 응답시간**이 이 구도에서 TPS 의 정확한
정의이며, 그렇게 계산한다. 이 정의는 문서에도 명시한다.
"""
import json
import re
import sys
from pathlib import Path

# stats.js 는 JSON 이 아니다 — 따옴표 없는 키(type/name/path/pathFormatted/stats/contents)가
# 따옴표 있는 키와 섞여 있다. 그 여섯 개만 따옴표를 채워 JSON 으로 만든 뒤 표준 파서에 넘긴다.
BARE_KEY = re.compile(r'^(\s*)(type|name|path|pathFormatted|stats|contents):', re.MULTILINE)


def load_stats(report_dir: Path) -> dict:
    src = (report_dir / "js" / "stats.js").read_text()
    src = src[src.index("{"):src.rindex("}", 0, src.index("function fillStats")) + 1]
    return json.loads(BARE_KEY.sub(r'\1"\2":', src))


def main() -> int:
    report_dir, strategy, capacity, contenders = Path(sys.argv[1]), *sys.argv[2:5]
    label = f"reserve [{strategy} cap={capacity} cont={contenders}]"

    root = load_stats(report_dir)
    entry = next((e for e in root["contents"].values() if e["stats"]["name"] == label), None)
    if entry is None:
        print(f"이름표 '{label}' 를 리포트에서 찾지 못했습니다: {report_dir}", file=sys.stderr)
        return 1

    s = entry["stats"]
    requests = int(s["numberOfRequests"]["total"])
    ok = int(s["numberOfRequests"]["ok"])
    ko = int(s["numberOfRequests"]["ko"])
    mean_ms = int(s["meanResponseTime"]["total"])
    p95_ms = int(s["percentiles3"]["total"])   # percentiles3 == 95th
    max_ms = int(s["maxResponseTime"]["total"])
    tps = round(requests / (max_ms / 1000.0), 1) if max_ms > 0 else 0.0

    print(f"{requests},{ok},{ko},{mean_ms},{p95_ms},{max_ms},{tps}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
