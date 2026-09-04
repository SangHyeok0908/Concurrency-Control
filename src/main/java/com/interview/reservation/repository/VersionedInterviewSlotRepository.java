package com.interview.reservation.repository;

import com.interview.reservation.domain.VersionedInterviewSlot;
import org.springframework.data.jpa.repository.JpaRepository;

/**
 * 2-2단계 ⑤ 전용 리포지터리. {@link InterviewSlotRepository} 와 <b>같은 테이블</b>을 다루지만
 * {@link VersionedInterviewSlot}({@code @Version} 이 달린 매핑)을 통해 접근한다.
 *
 * <p>메서드가 {@code findById} 하나뿐인 것이 요점이다 — 낙관적 락은 <b>조회 쪽에 아무것도
 * 얹지 않는다.</b> ②는 전용 UPDATE 문이 필요했고 ④는 락 힌트가 필요했지만, ⑤의 방어는
 * 리포지터리가 아니라 <b>엔티티 매핑과 커밋 시점</b>에 있다. 평범한 조회 뒤에 평범한 dirty
 * check UPDATE 가 나가고, Hibernate 가 거기에 {@code AND version = ?} 를 덧붙일 뿐이다.
 * 그래서 이 인터페이스에는 방어의 흔적이 보이지 않는다.
 */
public interface VersionedInterviewSlotRepository extends JpaRepository<VersionedInterviewSlot, Long> {
}
