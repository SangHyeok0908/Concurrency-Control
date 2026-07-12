package com.interview.reservation.service.strategy;

import com.interview.reservation.domain.Reservation;

/**
 * 하나의 동시성 방어 수단으로 예약을 처리하는 전략. 2단계의 각 방어(① UNIQUE, ② 조건부 UPDATE,
 * ④~⑥ 락 3종)를 <b>서로 나란히</b> 구현하기 위한 확장점이다.
 *
 * <p>설계 의도(docs/STEP2-3-BRANCH-STRATEGY.md 원칙 1): 방어는 baseline 을 덮어쓰지 않고
 * <b>추가</b>한다. 각 전략을 별도 구현체로 두고 {@code POST /api/reservations/{strategy}} 로
 * 골라 때릴 수 있어야, 같은 Gatling 부하로 방어별 before/after 를 비교할 수 있다.
 * 1단계 락 없는 경로는 {@code baseline} 전략으로 보존된다.
 */
public interface ReservationStrategy {

    /** 요청 경로 {@code /api/reservations/{strategy}} 와 벤치마크 리포트에서 이 전략을 가리키는 키. */
    String key();

    /** 이 전략의 방어 방식으로 예약을 확정한다. */
    Reservation reserve(Long applicantId, Long slotId);
}
