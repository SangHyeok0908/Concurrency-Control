package com.interview.reservation.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.Version;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

/**
 * 2-2단계 ⑤ 전용 매핑. {@link InterviewSlot} 과 <b>같은 {@code interview_slot} 테이블</b>을
 * 가리키지만, 이쪽에만 {@link Version @Version} 이 달려 있다.
 *
 * <p><b>왜 엔티티를 하나 더 두는가 — 이 브랜치의 핵심 설계 판단.</b>
 * 지금까지의 방어(② 조건부 UPDATE, ④ 비관적 락)는 전부 <b>호출하는 쪽</b>에 붙었다. 리포지터리
 * 메서드를 고르거나 락 힌트를 얹는 식이라, 그 메서드를 부르지 않는 전략은 아무 영향을 받지 않았다.
 * <b>낙관적 락만 다르다.</b> {@code @Version} 은 전략이 아니라 <b>엔티티에</b> 걸리고, Hibernate 는
 * 그 엔티티의 <i>모든</i> dirty check UPDATE 에 자동으로 {@code AND version = ?} 를 덧붙인다.
 *
 * <p>그래서 기존 {@link InterviewSlot} 에 {@code @Version} 을 달면 그 엔티티를 읽어-고쳐-쓰는
 * <b>baseline 과 unique 경로의 오버부킹이 함께 사라진다</b> — 두 경로는 방어를 넣은 적이 없는데도
 * 갑자기 안전해지고, 대신 {@code OptimisticLockException} 을 뱉는다. 1단계가 재현해 둔 증거물과
 * ①문서의 "unique 경로는 오버부킹을 막지 못한다"가 동시에 거짓이 된다(설계 원칙 2 위반).
 *
 * <p>매핑을 쪼개면 그 전이가 끊긴다. {@code @Version} 의 효력 범위가 <b>이 클래스를 로드한
 * 트랜잭션</b>으로 한정되므로, ⑤만 낙관적 락을 쓰고 나머지 네 경로는 바이트 단위로 보존된다.
 * ⑦ 벤치마크의 각 행이 같은 실험의 결과로 남기 위한 전제이기도 하다.
 *
 * <p><b>대가(면접 대비).</b> 한 테이블에 두 매핑이 있으면 <b>버전 검사를 우회하는 경로가 남는다</b> —
 * {@code InterviewSlot} 으로 이 행을 갱신하면 {@code version} 이 그대로라, 그 갱신은 동시에 열려
 * 있던 ⑤ 트랜잭션에게 <i>보이지 않는다</i>(lost update). 이 프로젝트에서는 전략을 한 번에 하나씩
 * 인가하므로 실측에 영향이 없지만, 운영 코드였다면 이건 받아들일 수 없는 구멍이다. 운영이라면
 * 매핑을 쪼개는 대신 <b>엔티티 하나에 {@code @Version} 을 달고 전 경로가 그걸 따르게</b> 해야
 * 한다 — 여기서 쪼갠 유일한 이유는 이 저장소가 <b>방어 없는 경로를 증거물로 보존해야 하는
 * 실험실</b>이기 때문이다.
 *
 * <p>필드는 ⑤가 실제로 쓰는 것만 매핑한다({@code startAt}·{@code capacity} 등은 읽지 않는다).
 * 이 엔티티로는 INSERT 하지 않으므로({@code InterviewSlotService} 가 {@link InterviewSlot} 로
 * 생성한다) 매핑되지 않은 {@code NOT NULL} 컬럼이 있어도 무방하다.
 */
@Entity
@Table(name = "interview_slot")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class VersionedInterviewSlot {

    @Id
    private Long id;

    /**
     * 낙관적 락의 표식. Hibernate 가 이 엔티티를 갱신할 때 {@code WHERE id = ? AND version = ?}
     * 를 붙이고 값을 1 올린다. 내가 읽은 뒤 누군가 먼저 커밋했다면 갱신 행이 0이 되고,
     * Hibernate 는 이를 {@code OptimisticLockException}(스프링에서는
     * {@code ObjectOptimisticLockingFailureException})으로 번역한다.
     *
     * <p>락을 잡지 않으므로 <b>대기가 없다.</b> 대신 충돌을 사후에 발견하므로 <b>재시도가
     * 필요하다</b> — 비관적 락과의 교환 조건이 이 필드 하나에 압축돼 있다.
     */
    @Version
    private Long version;

    @Column(nullable = false)
    private int remaining;

    public boolean isFull() {
        return remaining <= 0;
    }

    /**
     * 남은 자리를 하나 줄인다. {@link InterviewSlot#decrease()} 와 마찬가지로 가드가 없다 —
     * 오버부킹을 막는 것은 이 메서드가 아니라 커밋 시점의 <b>버전 검사</b>다. 그래서 코드
     * 모양은 baseline 의 check-then-act 와 똑같은데 결과만 안전해진다(④ 비관적 락과 같은
     * 성질이며, 레이스 구간을 <i>없앤</i> ② 조건부 UPDATE 와 갈리는 지점이다).
     */
    public void decrease() {
        this.remaining--;
    }
}
