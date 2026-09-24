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
 * 2-2단계 ④: <b>비관적 쓰기 락</b>({@code @Lock(PESSIMISTIC_WRITE)} → {@code SELECT ... FOR UPDATE})
 * 으로 임계 구역을 보호하는 방어 전략. 스키마 변경이 없다.
 *
 * <p><b>②와 무엇이 다른가.</b> {@link ConditionalUpdateReservationStrategy 조건부 UPDATE} 는
 * 읽고-검사-쓰기를 DB 한 문장으로 <i>접어</i> 레이스가 존재할 자리를 없앴다. 이 전략은 정반대
 * 접근이다 — baseline 과 <b>똑같은</b> read→check→decrease 구조를 그대로 두고, 슬롯을 읽을 때
 * 배타 락을 함께 걸어 <b>그 구간을 직렬화</b>한다. 락은 트랜잭션이 끝날 때까지 유지되므로,
 * 검사와 감소 사이에 다른 트랜잭션이 끼어들 수 없다. 결과는 같다(오버부킹 0). 방식이 다르다.
 *
 * <p><b>이 도메인에서 이 전략의 위치.</b> 정합성은 조건부 UPDATE 와 같다. 통제된 HTTP 측정에서는
 * 낮은 경합의 응답 중앙값이 둘 다 136ms 였고, 극단 경합에서는 오히려 이 전략이 빨랐다. 따라서
 * "락이라서 더 느리다"고 단정하지 않는다. 다만 이 도메인의 불변식은 한 행의
 * {@code remaining > 0} 조건과 감소 한 문장으로 이미 표현되므로, 애플리케이션에 별도
 * read→check→decrease 임계 구역을 만들 이유가 없다. 현재 조건부 UPDATE 를 채택한 근거는
 * 성능 우위가 아니라 문제를 표현하는 메커니즘이 더 작다는 점이다. 자세한 통제값과 짝비교는
 * docs/STEP2-DEFENSE-BENCHMARK.md 6절에 있다.
 *
 * <p><b>흐름.</b> 지원자를 먼저 확인(404)한 뒤 슬롯을 {@code FOR UPDATE} 로 읽는다. 그 시점부터
 * 트랜잭션 끝까지 이 슬롯 행은 이 트랜잭션의 것이므로, {@code isFull()} 검사와 {@code decrease()}
 * 는 안전하다. 중복 <i>예약</i>은 락이 막지 못하며 ①(V2 UNIQUE)이 계속 DB 레벨에서 막는다 —
 * 락은 데이터 레이스를 다루지 요청 중복을 다루지 않는다.
 */
@Component
@RequiredArgsConstructor
public class PessimisticLockReservationStrategy implements ReservationStrategy {

    private final ApplicantRepository applicantRepository;
    private final InterviewSlotRepository slotRepository;
    private final ReservationRepository reservationRepository;

    @Override
    public String key() {
        return "pessimistic";
    }

    @Override
    @Transactional
    public Reservation reserve(Long applicantId, Long slotId) {
        Applicant applicant = applicantRepository.findById(applicantId)
                .orElseThrow(() -> new NotFoundException("지원자를 찾을 수 없습니다: id=" + applicantId));

        // 여기서 SELECT ... FOR UPDATE 가 나간다. 이 줄 이후 트랜잭션이 끝날 때까지 슬롯 행은
        // 이 트랜잭션이 독점하므로, 아래 검사-감소는 임계 구역 안에 있다.
        InterviewSlot slot = slotRepository.findByIdForUpdate(slotId)
                .orElseThrow(() -> new NotFoundException("슬롯을 찾을 수 없습니다: id=" + slotId));

        if (slot.isFull()) {
            throw new SlotFullException("남은 자리가 없습니다: slotId=" + slotId);
        }
        slot.decrease();

        return reservationRepository.save(Reservation.confirm(applicant, slot));
    }
}
