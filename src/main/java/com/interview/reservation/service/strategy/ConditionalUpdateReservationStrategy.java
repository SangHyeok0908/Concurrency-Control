package com.interview.reservation.service.strategy;

import com.interview.reservation.domain.Applicant;
import com.interview.reservation.domain.InterviewSlot;
import com.interview.reservation.domain.Reservation;
import com.interview.reservation.exception.NotFoundException;
import com.interview.reservation.exception.SlotFullException;
import com.interview.reservation.repository.ApplicantRepository;
import com.interview.reservation.repository.InterviewSlotRepository;
import com.interview.reservation.repository.ReservationRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * 2-1단계 ②: 원자적 <b>조건부 UPDATE</b> 로 오버부킹을 없애는 방어 전략. 스키마 변경이 없다.
 *
 * <p><b>baseline·unique 와 무엇이 다른가.</b> 두 전략은 슬롯을 메모리로 읽어
 * ({@code findById}) {@code isFull()} 로 검사한 뒤 {@code decrease()} 하는 check-then-act 라,
 * 두 요청이 같은 "자리 있음"을 읽고 둘 다 감소시키는 레이스에 노출된다(오버부킹). 이 전략은
 * 그 읽고-검사-쓰기를 {@link InterviewSlotRepository#decrementRemaining 한 문장}으로 접어
 * DB 에 넘긴다. {@code WHERE remaining > 0} 조건과 감소가 원자적으로 함께 일어나므로,
 * InnoDB 가 행 잠금으로 직렬화하고 마지막 자리는 정확히 한 요청만 가져간다.
 *
 * <p><b>흐름.</b> 지원자 존재를 먼저 확인(404)한 뒤 조건부 UPDATE 를 던진다. 갱신 행이
 * {@code 1} 이면 자리를 확보한 것이므로 예약을 INSERT 한다. {@code 0} 이면 만석이거나 없는
 * 슬롯이다 — {@code existsById} 로 둘을 구분해 각각 {@link SlotFullException}(만석)과
 * {@link NotFoundException}(없는 슬롯)으로 번역한다. 슬롯은 예약의 FK 로만 필요하므로
 * {@code getReferenceById} 프록시를 써서 불필요한 SELECT 를 피한다(조건부 UPDATE 가 이미
 * 자리를 확정했으니 엔티티 상태를 메모리로 읽어올 이유가 없다).
 *
 * <p><b>이 전략이 다루지 않는 것.</b> 같은 지원자의 중복 <i>예약</i>은 여전히 ①(V2 UNIQUE)이
 * DB 레벨에서 막는다 — 이 전략은 오버부킹만 겨냥한다. 중복 <i>요청</i> 역시 그 UNIQUE 자연 키가
 * 덮으므로 별도의 멱등성 키는 도입하지 않는다(docs/STEP2-3-BRANCH-STRATEGY.md 의 ③ 참고).
 */
@Component
@RequiredArgsConstructor
public class ConditionalUpdateReservationStrategy implements ReservationStrategy {

    private final ApplicantRepository applicantRepository;
    private final InterviewSlotRepository slotRepository;
    private final ReservationRepository reservationRepository;

    @Override
    public String key() {
        return "conditional";
    }

    @Override
    @Transactional
    public Reservation reserve(Long applicantId, Long slotId) {
        Applicant applicant = applicantRepository.findById(applicantId)
                .orElseThrow(() -> new NotFoundException("지원자를 찾을 수 없습니다: id=" + applicantId));

        int seatsTaken = slotRepository.decrementRemaining(slotId);
        if (seatsTaken == 0) {
            if (!slotRepository.existsById(slotId)) {
                throw new NotFoundException("슬롯을 찾을 수 없습니다: id=" + slotId);
            }
            throw new SlotFullException("남은 자리가 없습니다: slotId=" + slotId);
        }

        // 자리는 조건부 UPDATE 가 이미 원자적으로 확정했다. 슬롯은 예약의 FK 로만 쓰이므로
        // 프록시(getReferenceById)로 참조만 걸어 SELECT 한 번을 아낀다.
        InterviewSlot slot = slotRepository.getReferenceById(slotId);
        return reservationRepository.save(Reservation.confirm(applicant, slot));
    }
}
