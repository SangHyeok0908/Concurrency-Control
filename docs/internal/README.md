# 내부 문서 안내

`docs/internal/`은 공개 정본이 아닌 작업 중 계획과 완료된 내부 기록을 분리해 보관한다.

- `roadmap/`에는 승인됐지만 아직 구현되지 않은 설계와 구현 계획을 둔다.
- roadmap 문서는 구현 상태나 공개 벤치마크의 근거로 인용하지 않는다.
- `archive/`에는 구현을 마친 작업의 당시 설계·계획 기록을 둔다.
- archive 문서는 의사결정과 구현 경위를 확인하는 용도이며, 최신 실행 결과의 정본이 아니다.

## 문서 목록

- archive: [벤치마크 도구 단순화 설계](archive/2026-09-25-benchmark-tooling-simplification-design.md) · [구현 계획](archive/2026-09-25-benchmark-tooling-simplification.md)
- 방법론 v2의 이전 계획은 구현과 도구 단순화로 대체되어 삭제했다. 현재 정본은 공개 벤치마크 문서다.
- archive: [동일 요청 검증 설계](archive/2026-09-25-duplicate-request-benchmark-design.md) · [구현 계획](archive/2026-09-25-duplicate-request-benchmark.md)

공개 벤치마크의 기준 문서는 [STEP2-DEFENSE-BENCHMARK.md](../STEP2-DEFENSE-BENCHMARK.md)다.
동일 요청 검증의 실행 정본은 [2026-09-25-duplicate-request-run.md](../benchmark/2026-09-25-duplicate-request-run.md)다.
기존 공개 원시 결과와 실행 기록은 `docs/benchmark/` 아래에서 관리한다.

새 내부 문서는 상태와 공개 정본 링크를 문서 상단에 명시한다.
공개 결론을 변경할 때는 이 디렉터리가 아니라 해당 공개 정본을 갱신한다.
