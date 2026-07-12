package com.interview.reservation.repository;

import com.interview.reservation.domain.InterviewSlot;
import org.springframework.data.jpa.repository.JpaRepository;

/**
 * 1단계에서는 의도적으로 평범한 조회만 제공한다. {@code @Lock(PESSIMISTIC_WRITE)} 나
 * 조건부 UPDATE 쿼리는 여기 없다 — 그것들은 2단계에서 도입해 baseline 과 벤치마크한다.
 */
public interface InterviewSlotRepository extends JpaRepository<InterviewSlot, Long> {
}
