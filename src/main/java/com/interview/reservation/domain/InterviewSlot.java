package com.interview.reservation.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;
import java.time.LocalDateTime;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

/**
 * 정원 N명짜리 면접 시간대. 이 실험의 진앙지다.
 *
 * <p>{@code remaining} 은 남은 자리 수를 들고 있는 카운터다. 1단계 예약 로직은 이 값을
 * 읽어서(메모리에서) 검사하고 다시 쓰는 check-then-act 를 <b>일부러</b> 수행한다. 그래서
 * 동시에 여러 요청이 들어오면 정원 초과가 발생한다.
 *
 * <p>{@link #decrease()} 는 음수를 막지 않는다. 오버부킹을 예외가 아니라 "조용히 깨진
 * 데이터"(remaining 이 음수, reservation 행 수 &gt; capacity)로 관찰하는 것이 1단계의
 * 산출물이기 때문이다.
 */
@Entity
@Table(name = "interview_slot")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class InterviewSlot {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false)
    private LocalDateTime startAt;

    @Column(nullable = false)
    private LocalDateTime endAt;

    @Column(nullable = false)
    private int capacity;

    @Column(nullable = false)
    private int remaining;

    @Column(nullable = false)
    private LocalDateTime createdAt;

    private InterviewSlot(LocalDateTime startAt, LocalDateTime endAt, int capacity) {
        this.startAt = startAt;
        this.endAt = endAt;
        this.capacity = capacity;
        this.remaining = capacity;
    }

    public static InterviewSlot of(LocalDateTime startAt, LocalDateTime endAt, int capacity) {
        return new InterviewSlot(startAt, endAt, capacity);
    }

    public boolean isFull() {
        return remaining <= 0;
    }

    /**
     * 남은 자리를 하나 줄인다. 가드가 없다 — 호출 측의 check-then-act 사이에 다른 스레드가
     * 끼어들면 이 감소는 정원을 넘어 계속 진행된다. 이것이 1단계에서 재현하려는 버그다.
     */
    public void decrease() {
        this.remaining--;
    }

    @PrePersist
    void onCreate() {
        this.createdAt = LocalDateTime.now();
    }
}
