# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 이 프로젝트가 무엇인가

선착순 면접 예약 시스템. 취업용 포트폴리오 프로젝트다. 전체 계획은 `PROJECT_PLAN.md`에 있다.

이 프로젝트의 목적은 **예약 기능 자체가 아니다.** 통제된 실험이다. 부하를 걸어 동시성 버그(정원 초과, 중복 예약)를 실제로 재현하고, 여러 방어 수단을 구현해 서로 벤치마크한 뒤, 왜 그중 하나를 선택했는지 근거를 남기는 것이 목적이다. 코드는 그 논증의 증거를 만들어내기 위해 존재한다.

**이 사실은 여기서 코드를 고치는 방식에 직접적인 제약을 건다.** 1단계는 **의도적으로 락 없이** 작성한다. 그래야 레이스 컨디션을 재현하고 캡처할 수 있다. 정확성을 명분으로 1단계 코드에 락, `synchronized`, 뮤텍스 용도의 트랜잭션, `UNIQUE` 제약, 재시도 로직을 추가하지 말 것. 그렇게 하면 이 프로젝트가 만들어내려는 증거물 자체가 사라진다. 레이스처럼 보이는 코드를 발견하면, "고치기" 전에 그 코드가 몇 단계에 속하는지 먼저 확인할 것.

## 방어 계층

방어 수단은 **가벼운 것부터** 정해진 순서로 도입한다(UNIQUE 제약 → 조건부 UPDATE → 멱등성 키 → 락). 전체 목록과 근거는 `PROJECT_PLAN.md` 3장에 있으니 그쪽을 정본으로 삼고, 여기 다시 옮겨 적지 말 것. 코드를 추가할 때 그 순서를 유지한다 — 순서 자체가 포트폴리오의 논지다("가장 단순한 도구에서 시작했고, 부족해지는 지점에서만 더 무거운 도구를 꺼냈다").

락 3종(비관적 `@Lock(PESSIMISTIC_WRITE)`, 낙관적 `@Version` + 백오프, Redisson `RLock`)은 **조건부 UPDATE만으로 부족해지는 지점을 보여주기 위해** 구현하는 것이지 기본 해법이 아니다. 멱등성 키(같은 사용자의 재요청)와 데이터 레이스(정원 경쟁)는 **서로 다른 문제**이며 락은 전자를 해결하지 못한다.

## 명령어

`java`는 PATH에 있지만(Temurin 21), 빌드는 항상 래퍼로 실행한다. 래퍼가 Gradle 버전과 툴체인을 고정하기 때문이다.

```bash
./gradlew build          # 컴파일 + 테스트 (PowerShell/cmd에서는 gradlew.bat)
./gradlew test           # 테스트만 — Testcontainers가 MySQL을 띄우므로 Docker 데몬이 켜져 있어야 한다
./gradlew bootRun        # 앱 실행 — 아래 DB 기동이 선행되어야 한다
./gradlew test --tests 'com.interview.reservation.ReservationApplicationTests'  # 단일 클래스
```

## 커밋

커밋 단위 자체가 이 포트폴리오의 평가 항목이다(`PROJECT_PLAN.md` 5장). 한 번에 몰아서 커밋하지 말고 작고 의미 있는 단위로 나눌 것.
