# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 이 프로젝트가 무엇인가

선착순 면접 예약 시스템. 취업용 포트폴리오 프로젝트다. 전체 계획은 `PROJECT_PLAN.md`에 있다.

이 프로젝트의 목적은 **예약 기능 자체가 아니다.** 통제된 실험이다. 부하를 걸어 동시성 버그(정원 초과, 중복 예약)를 실제로 재현하고, 여러 방어 수단을 구현해 서로 벤치마크한 뒤, 왜 그중 하나를 선택했는지 근거를 남기는 것이 목적이다. 코드는 그 논증의 증거를 만들어내기 위해 존재한다.

**이 사실은 여기서 코드를 고치는 방식에 직접적인 제약을 건다.** 1단계는 **의도적으로 락 없이** 작성한다. 그래야 레이스 컨디션을 재현하고 캡처할 수 있다. 정확성을 명분으로 1단계 코드에 락, `synchronized`, 뮤텍스 용도의 트랜잭션, `UNIQUE` 제약, 재시도 로직을 추가하지 말 것. 그렇게 하면 이 프로젝트가 만들어내려는 증거물 자체가 사라진다. 레이스처럼 보이는 코드를 발견하면, "고치기" 전에 그 코드가 몇 단계에 속하는지 먼저 확인할 것.

## 1단계 진행 상황

기능 구현(도메인·리포지토리·서비스·REST API)과 baseline 오버부킹 재현이 끝났다. 재현 결과, 간헐성의
원인, 데드락(FK의 S잠금 ↔ UPDATE의 X잠금 승격 충돌) 분석은 [docs/STEP1-BASELINE-OVERBOOKING.md](docs/STEP1-BASELINE-OVERBOOKING.md)에 정리돼 있다. **남은 1단계 작업은 Gatling HTTP 부하 테스트**(처리량·응답시간·실패율 측정, 2단계 before/after 벤치마크의 baseline 수치)다.

동시성 통합 테스트 `BaselineOverbookingProbeTest`는 **의도적으로 "간헐 재현 프로브"** 다. 실제 MySQL은
트랜잭션이 빨라 오버부킹이 매번 터지지 않으므로, 단언은 신뢰 가능한 불변식(확정 예약 ≥ 정원)에만 걸고
오버부킹 발생은 로그로 남긴다. 이 테스트를 "무조건 터진다"로 강화하거나 락으로 '고치지' 말 것 — 최악
인터리빙 강제와 방어 검증은 2단계 몫이다.

## 방어 계층

방어 수단은 **가벼운 것부터** 정해진 순서로 도입한다(UNIQUE 제약 → 조건부 UPDATE → 멱등성 키 → 락). 전체 목록과 근거는 `PROJECT_PLAN.md` 3장에 있으니 그쪽을 정본으로 삼고, 여기 다시 옮겨 적지 말 것. 코드를 추가할 때 그 순서를 유지한다 — 순서 자체가 포트폴리오의 논지다("가장 단순한 도구에서 시작했고, 부족해지는 지점에서만 더 무거운 도구를 꺼냈다").

락 3종(비관적 `@Lock(PESSIMISTIC_WRITE)`, 낙관적 `@Version` + 백오프, Redisson `RLock`)은 **조건부 UPDATE만으로 부족해지는 지점을 보여주기 위해** 구현하는 것이지 기본 해법이 아니다. 멱등성 키(같은 사용자의 재요청)와 데이터 레이스(정원 경쟁)는 **서로 다른 문제**이며 락은 전자를 해결하지 못한다.

## 명령어

`java`는 PATH에 있지만(Temurin 21), 빌드는 항상 래퍼로 실행한다. 래퍼가 Gradle 버전과 툴체인을 고정하기 때문이다.

```bash
docker compose up -d     # MySQL(:3306)·Redis(:6379) 기동 — bootRun/부하 테스트 전에 선행
./gradlew build          # 컴파일 + 테스트 (PowerShell/cmd에서는 gradlew.bat)
./gradlew test           # 테스트만 — Testcontainers가 MySQL을 띄우므로 Docker 데몬이 켜져 있어야 한다
./gradlew bootRun        # 앱 실행(:8080) — 위 docker compose 기동이 선행되어야 한다
./gradlew test --tests 'com.interview.reservation.ReservationApplicationTests'  # 단일 클래스
```

DB는 `reservation`(root, 비밀번호 없음), 앱은 `:8080`. 로컬 실행 설정은 `.claude/launch.json`에도
있다(`reservation-app`, `infra`).

## 커밋

커밋 단위 자체가 이 포트폴리오의 평가 항목이다(`PROJECT_PLAN.md` 5장). 한 번에 몰아서 커밋하지 말고 작고 의미 있는 단위로 나눌 것.

**커밋은 절대 자동으로 찍지 말 것.** 작업(코드 작성, 테스트, 수정)은 진행하되, `git commit`은 반드시 사용자에게 먼저 물어보고 명시적 승인을 받은 뒤에만 실행한다. 커밋 메시지 초안을 보여주고 확인을 받는 것을 기본으로 한다.
