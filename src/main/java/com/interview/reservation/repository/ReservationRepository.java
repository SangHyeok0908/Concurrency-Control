package com.interview.reservation.repository;

import com.interview.reservation.domain.Reservation;
import com.interview.reservation.domain.ReservationStatus;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ReservationRepository extends JpaRepository<Reservation, Long> {

    /**
     * 부하 테스트 후 검증용. 한 슬롯에 실제로 몇 건의 예약이 확정됐는지 세어
     * {@code capacity} 와 비교하면 오버부킹 규모를 정량화할 수 있다.
     */
    long countBySlotIdAndStatus(Long slotId, ReservationStatus status);

    /**
     * 중복 예약 검증용. 같은 지원자가 같은 슬롯에 몇 행을 만들었는지 센다.
     */
    long countByApplicantIdAndSlotId(Long applicantId, Long slotId);
}
