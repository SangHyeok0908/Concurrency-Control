package com.interview.reservation.service;

import com.interview.reservation.domain.Applicant;
import com.interview.reservation.domain.InterviewSlot;
import com.interview.reservation.domain.Reservation;
import com.interview.reservation.exception.NotFoundException;
import com.interview.reservation.exception.SlotFullException;
import com.interview.reservation.repository.ApplicantRepository;
import com.interview.reservation.repository.InterviewSlotRepository;
import com.interview.reservation.repository.ReservationRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 선착순 예약의 1단계(baseline) 구현. <b>의도적으로 락이 없다.</b>
 *
 * <p>이 클래스는 이 포트폴리오가 재현하려는 버그의 핵심 증거물이다. 정확성을 명분으로
 * {@code @Lock(PESSIMISTIC_WRITE)}, 조건부 UPDATE({@code WHERE remaining > 0}),
 * {@code @Version}, 재시도, {@code synchronized} 를 여기 추가하지 말 것. 그렇게 하면
 * 재현하려는 레이스 컨디션이 사라진다. 방어 수단은 2단계에서 별도 구현으로 도입해 이
 * baseline 과 벤치마크한다. (근거: PROJECT_PLAN.md 4장, CLAUDE.md)
 */
@Service
@RequiredArgsConstructor
public class ReservationService {

    private final ApplicantRepository applicantRepository;
    private final InterviewSlotRepository slotRepository;
    private final ReservationRepository reservationRepository;

    /**
     * 선착순 예약. 아래 흐름의 read → check → decrease 사이에는 아무런 동시성 제어가 없다.
     *
     * <pre>
     *   1. 슬롯을 읽는다            (remaining 을 애플리케이션 메모리로 가져온다)
     *   2. remaining > 0 인지 검사   ← 여러 스레드가 같은 값을 읽고 모두 통과할 수 있다
     *   3. remaining 을 하나 줄인다   (dirty checking 으로 UPDATE ... SET remaining = ? 발행)
     *   4. reservation 을 저장한다
     * </pre>
     *
     * <p>동시에 마지막 한 자리를 두고 N개의 요청이 경쟁하면, 모두 2번을 통과한 뒤 각자
     * 3~4번을 수행해 <b>정원을 초과한 예약</b>이 만들어진다. 3번의 UPDATE 는
     * {@code remaining = remaining - 1} 이 아니라 {@code remaining = <읽은 값 - 1>} 이므로
     * lost update 도 함께 발생한다.
     */
    @Transactional
    public Reservation reserve(Long applicantId, Long slotId) {
        Applicant applicant = applicantRepository.findById(applicantId)
                .orElseThrow(() -> new NotFoundException("지원자를 찾을 수 없습니다: id=" + applicantId));
        InterviewSlot slot = slotRepository.findById(slotId)
                .orElseThrow(() -> new NotFoundException("슬롯을 찾을 수 없습니다: id=" + slotId));

        // check-then-act: 이 검사와 아래 decrease() 사이가 임계 구역이지만, 1단계에는 그것을
        // 보호하는 장치가 없다.
        if (slot.isFull()) {
            throw new SlotFullException("남은 자리가 없습니다: slotId=" + slotId);
        }
        slot.decrease();

        return reservationRepository.save(Reservation.confirm(applicant, slot));
    }
}
