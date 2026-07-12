package com.interview.reservation.service.strategy;

import com.interview.reservation.domain.Applicant;
import com.interview.reservation.domain.InterviewSlot;
import com.interview.reservation.domain.Reservation;
import com.interview.reservation.exception.DuplicateReservationException;
import com.interview.reservation.exception.NotFoundException;
import com.interview.reservation.exception.SlotFullException;
import com.interview.reservation.repository.ApplicantRepository;
import com.interview.reservation.repository.InterviewSlotRepository;
import com.interview.reservation.repository.ReservationRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * 2-1단계 ①: {@code UNIQUE(applicant_id, slot_id)}(V2)를 활용하는 방어 전략.
 *
 * <p><b>이 전략이 막는 것과 못 막는 것.</b> 같은 지원자가 같은 슬롯을 두 번 예약하는
 * <b>중복 예약</b>은 DB 제약이 원천 차단한다. 반면 <b>오버부킹</b>(서로 다른 지원자들이 정원을
 * 넘겨 경쟁)은 이 전략도 <b>여전히 허용한다</b> — read→check→decrease 는 baseline 과 똑같이
 * 락이 없기 때문이다. 오버부킹 방지는 ②(조건부 UPDATE)의 몫이며, 방어를 가벼운 것부터
 * 순서대로 쌓는다는 포트폴리오 논지를 이 분리가 드러낸다.
 *
 * <p><b>예외 번역.</b> {@code save} 를 {@code saveAndFlush} 로 바꿔 UNIQUE 검사를 트랜잭션
 * 커밋이 아니라 이 메서드 <b>안</b>에서 강제로 일으킨다. 그래야 DB 가 던지는
 * {@link DataIntegrityViolationException} 을 여기서 잡아 도메인 예외
 * {@link DuplicateReservationException}(HTTP 409)으로 번역할 수 있다. 예외가 밖으로 나가면
 * 트랜잭션 전체가 롤백되므로 {@link InterviewSlot#decrease() 좌석 감소}도 되돌아간다 —
 * 중복 요청이 자리를 축내지 않는다.
 */
@Component
@RequiredArgsConstructor
public class UniqueConstraintReservationStrategy implements ReservationStrategy {

    private final ApplicantRepository applicantRepository;
    private final InterviewSlotRepository slotRepository;
    private final ReservationRepository reservationRepository;

    @Override
    public String key() {
        return "unique";
    }

    @Override
    @Transactional
    public Reservation reserve(Long applicantId, Long slotId) {
        Applicant applicant = applicantRepository.findById(applicantId)
                .orElseThrow(() -> new NotFoundException("지원자를 찾을 수 없습니다: id=" + applicantId));
        InterviewSlot slot = slotRepository.findById(slotId)
                .orElseThrow(() -> new NotFoundException("슬롯을 찾을 수 없습니다: id=" + slotId));

        // 오버부킹 방어는 아직 없다(①은 중복만 다룬다). baseline 과 동일한 check-then-act.
        if (slot.isFull()) {
            throw new SlotFullException("남은 자리가 없습니다: slotId=" + slotId);
        }
        slot.decrease();

        try {
            // saveAndFlush 로 UNIQUE 검사를 지금 강제한다(커밋까지 미루지 않는다).
            return reservationRepository.saveAndFlush(Reservation.confirm(applicant, slot));
        } catch (DataIntegrityViolationException e) {
            throw new DuplicateReservationException(
                    "이미 예약한 슬롯입니다: applicantId=" + applicantId + ", slotId=" + slotId);
        }
    }
}
