package com.interview.reservation.repository;

import com.interview.reservation.domain.InterviewSlot;
import jakarta.persistence.LockModeType;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

/**
 * 1단계 baseline 은 평범한 {@code findById} 조회만 썼다 — check-then-act 사이의 레이스를
 * 그대로 드러내기 위해서였다. 2-1단계 ②에서 <b>원자적 조건부 UPDATE</b>({@link #decrementRemaining}),
 * 2-2단계 ④에서 <b>비관적 쓰기 락</b>({@link #findByIdForUpdate})을 추가한다. baseline 의
 * {@code findById} 경로는 그대로 두고 이 메서드들을 나란히 두어, 같은 부하로 여러 방식을
 * 벤치마크한다.
 */
public interface InterviewSlotRepository extends JpaRepository<InterviewSlot, Long> {

    /**
     * 2-1단계 ②: 남은 자리를 <b>원자적으로</b> 하나 줄인다.
     *
     * <p>{@code WHERE ... AND remaining > 0} 이 감소와 정원 검사를 <b>DB 한 번의 문장 안에서</b>
     * 함께 수행한다. 애플리케이션이 {@code remaining} 을 메모리로 읽어와 검사하지 않으므로
     * check-then-act 레이스가 존재할 자리 자체가 없다 — InnoDB 가 이 UPDATE 의 행 잠금을
     * 직렬화하고, 자리가 없으면 조건이 거짓이 되어 <b>0행</b>을 갱신한다. 락/데드락 없이
     * 오버부킹이 사라지는 지점이다.
     *
     * @return 갱신된 행 수. {@code 1} 이면 자리를 확보한 것, {@code 0} 이면 만석(또는 없는 슬롯)이다.
     */
    @Modifying
    @Query("UPDATE InterviewSlot s SET s.remaining = s.remaining - 1 "
            + "WHERE s.id = :slotId AND s.remaining > 0")
    int decrementRemaining(@Param("slotId") Long slotId);

    /**
     * 2-2단계 ④: 슬롯 행을 <b>배타 락</b>과 함께 읽는다 — {@code SELECT ... FOR UPDATE}.
     *
     * <p>{@link #decrementRemaining 조건부 UPDATE} 가 읽고-검사-쓰기를 DB 한 문장으로 <i>접어</i>
     * 레이스를 없앴다면, 이쪽은 반대로 <b>레이스가 일어날 구간을 그대로 둔 채 그 구간을 잠근다.</b>
     * 락은 호출 트랜잭션이 커밋/롤백될 때까지 유지되므로, 그 사이 같은 행을 읽으려는 다른
     * 트랜잭션은 <b>대기</b>한다. 덕분에 뒤따르는 {@code isFull()} 검사와 {@code decrease()} 는
     * 임계 구역 안에서 직렬화된다.
     *
     * <p>대가는 명시적이다 — 경쟁자들이 락을 기다리며 <b>줄을 서고</b>, 락 보유 구간이
     * 트랜잭션 전체로 늘어난다. 이 도메인의 임계 구역은 카운터 하나를 줄이는 게 전부라
     * 그 대가를 상쇄할 이득이 없다(docs/STEP2-PESSIMISTIC-LOCK.md).
     */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT s FROM InterviewSlot s WHERE s.id = :slotId")
    Optional<InterviewSlot> findByIdForUpdate(@Param("slotId") Long slotId);
}
