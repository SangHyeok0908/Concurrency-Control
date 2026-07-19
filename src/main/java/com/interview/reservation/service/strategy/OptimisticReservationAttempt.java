package com.interview.reservation.service.strategy;

import com.interview.reservation.domain.Applicant;
import com.interview.reservation.domain.InterviewSlot;
import com.interview.reservation.domain.Reservation;
import com.interview.reservation.domain.VersionedInterviewSlot;
import com.interview.reservation.exception.NotFoundException;
import com.interview.reservation.exception.SlotFullException;
import com.interview.reservation.repository.ApplicantRepository;
import com.interview.reservation.repository.InterviewSlotRepository;
import com.interview.reservation.repository.ReservationRepository;
import com.interview.reservation.repository.VersionedInterviewSlotRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/**
 * 낙관적 락 예약의 <b>단 한 번의 시도</b>. 재시도 루프는 여기 없다 —
 * {@link OptimisticLockReservationStrategy} 가 이 빈을 <b>바깥에서</b> 반복 호출한다.
 *
 * <p><b>왜 재시도와 시도를 다른 빈으로 쪼갰나 (이 브랜치의 트러블슈팅 소재).</b>
 * 낙관적 락 충돌은 메서드 본문에서 터지지 않는다. Hibernate 는 갱신을 모아뒀다가
 * <b>커밋 직전 flush</b> 에서 UPDATE 를 날리므로, 버전 충돌 예외는 {@code @Transactional}
 * 프록시가 커밋을 시도하는 순간 — 즉 <b>이 메서드가 이미 정상 반환한 뒤</b> — 발생한다.
 * 그래서 재시도 루프를 이 메서드 <i>안</i>에 두면 잡히지 않는다. 잡히더라도 소용이 없다:
 * 충돌한 트랜잭션은 이미 rollback-only 로 표시돼 있어, 같은 트랜잭션 안에서 다시 시도한
 * 갱신은 무슨 짓을 해도 커밋되지 않는다.
 *
 * <p>따라서 <b>재시도는 반드시 트랜잭션 경계 밖</b>이어야 하고, 시도 한 번이 곧 트랜잭션
 * 하나여야 한다. 스프링의 {@code @Transactional} 은 프록시 기반이라 자기 호출(self-invocation)
 * 로는 경계가 생기지 않으므로, 경계를 확실히 만들려면 <b>다른 빈</b>이어야 한다. 이 클래스가
 * 분리돼 있는 이유가 그것이다.
 *
 * <p><b>{@link Propagation#REQUIRES_NEW} 는 지금 아무 일도 하지 않는다 — 일부러 그렇다.</b>
 * 현재 호출 경로(컨트롤러 → 전략 → 이 빈)에는 활성 트랜잭션이 없으므로 기본값
 * {@code REQUIRED} 로 바꿔도 동작이 <b>완전히 같다</b>. 시도마다 새 트랜잭션이 열리는 것은
 * propagation 덕분이 아니라 <b>위에 적은 빈 분리</b> 덕분이다(호출자에 트랜잭션이 없고,
 * 매 호출이 프록시를 통과한다).
 *
 * <p>그럼 왜 붙여 두는가 — <b>미래의 오용을 막는 가드</b>다. 누군가 {@code reserve()} 를
 * {@code @Transactional} 메서드 안에서 부르는 순간, {@code REQUIRED} 였다면 각 시도가 바깥
 * 트랜잭션에 <b>합류</b>한다. 그러면 첫 충돌이 바깥 트랜잭션을 rollback-only 로 마킹해
 * 이후 재시도는 성공해도 커밋되지 않고 끝내 {@code UnexpectedRollbackException} 이 난다 —
 * <b>재시도 루프 전체가 조용히 무력화된다.</b> {@code REQUIRES_NEW} 면 각 시도가 독립
 * 트랜잭션이라 루프가 그대로 작동한다.
 *
 * <p>공짜는 아니다. 바깥 트랜잭션이 있으면 그것이 suspend 되면서 <b>커넥션을 2개</b> 점유하므로,
 * 풀이 빠듯하면 자기 자신을 기다리는 교착이 될 수 있다. 지금은 바깥 트랜잭션이 없어 해당 없다.
 */
@Component
@RequiredArgsConstructor
public class OptimisticReservationAttempt {

    private final ApplicantRepository applicantRepository;
    private final VersionedInterviewSlotRepository versionedSlotRepository;
    private final InterviewSlotRepository slotRepository;
    private final ReservationRepository reservationRepository;

    /**
     * 예약을 한 번 시도한다. 성공하면 확정된 예약을, 자리가 없으면 {@link SlotFullException} 을
     * 던진다.
     *
     * <p>버전 충돌은 아래 명시적 {@code flush()} 지점에서
     * {@code ObjectOptimisticLockingFailureException} 으로 터진다. 그 flush 가 없다면 충돌은
     * 이 메서드가 <b>반환한 뒤</b> 커밋 시점에야 드러났을 것이다 — 어느 쪽이든 <b>재시도는 이
     * 메서드 안에서 할 수 없다.</b> 충돌한 트랜잭션은 이미 rollback-only 로 표시돼 있어 같은
     * 트랜잭션 안의 재시도는 무슨 짓을 해도 커밋되지 않기 때문이다(클래스 javadoc 참고).
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public Reservation attempt(Long applicantId, Long slotId) {
        Applicant applicant = applicantRepository.findById(applicantId)
                .orElseThrow(() -> new NotFoundException("지원자를 찾을 수 없습니다: id=" + applicantId));

        // 락 힌트도 조건부 UPDATE 도 없는 평범한 조회다. baseline 과 코드 모양이 같다 —
        // 이 시점의 version 값이 "내가 읽은 시점"의 표식으로 엔티티에 실려 온다.
        VersionedInterviewSlot slot = versionedSlotRepository.findById(slotId)
                .orElseThrow(() -> new NotFoundException("슬롯을 찾을 수 없습니다: id=" + slotId));

        if (slot.isFull()) {
            throw new SlotFullException("남은 자리가 없습니다: slotId=" + slotId);
        }
        slot.decrease();

        // ⚠️ 이 flush 를 빼면 안 된다 — 데드락을 부르는 자리다(실측: 20건 중 19건 데드락).
        //
        // 이 감소는 UPDATE ... SET remaining=?, version=? WHERE id=? AND version=? 로 나가고,
        // 그러려면 슬롯 행의 *배타* 락이 필요하다. 그런데 아래 예약 INSERT 는 FK 검사 때문에
        // 같은 슬롯 행의 *공유* 락을 잡는다. Hibernate 는 IDENTITY 전략이라 INSERT 를 즉시
        // 실행하고 UPDATE 는 커밋 시점까지 미루므로, 그냥 두면 순서가 이렇게 된다.
        //
        //     [모든 스레드] 공유 락 획득(INSERT) → 배타 락 요청(UPDATE) → 서로를 기다림 → 데드락
        //
        // 공유 락을 여럿이 함께 쥔 상태에서 전원이 배타 락으로 승격하려 드는, 교과서적인
        // lock upgrade 데드락이다. 여기서 미리 flush 해 배타 락을 먼저 잡으면 순서가
        // 뒤집혀(배타 → 공유) 승격이 사라지고 데드락도 사라진다.
        //
        // 같은 메커니즘이 1단계 baseline 을 죽였다(HTTP 부하에서 요청의 77%). ④ 비관적 락에
        // 이 문제가 없는 이유도 같다 — FOR UPDATE 가 이미 배타 락을 먼저 잡아 놓기 때문이다.
        // 근거와 실측은 docs/STEP2-OPTIMISTIC-LOCK.md 3절.
        versionedSlotRepository.flush();

        // 예약의 FK 로만 필요하므로 프록시로 참조만 건다(②와 같은 이유로 SELECT 한 번을 아낀다).
        // 여기서 InterviewSlot 을 *읽어오면* 같은 행을 두 매핑으로 들고 있게 되므로 피한다.
        InterviewSlot slotRef = slotRepository.getReferenceById(slotId);
        return reservationRepository.save(Reservation.confirm(applicant, slotRef));
    }
}
