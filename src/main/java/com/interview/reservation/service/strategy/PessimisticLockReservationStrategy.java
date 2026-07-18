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
 * <p><b>이 도메인에서 이 전략의 위치.</b> 여기서 비관적 락은 조건부 UPDATE 를 <b>이기지 못한다.</b>
 * 임계 구역이 카운터 하나를 줄이는 것뿐이라, 락이 지켜줄 다단계 구간 자체가 없기 때문이다.
 * 그런데도 락 획득 왕복이 한 번 더 들고, 경쟁자들은 락을 기다리며 줄을 선다 — 정합성은 같은데
 * 비용만 는다. 비관적 락이 값을 하는 조건(임계 구역이 여러 행·여러 문장에 걸쳐 재시도 비용이
 * 큰 경우)과 이 도메인에 그 조건이 없는 이유는 docs/STEP2-PESSIMISTIC-LOCK.md 에 있다.
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
