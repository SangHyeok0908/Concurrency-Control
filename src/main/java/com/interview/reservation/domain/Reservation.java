package com.interview.reservation.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
import java.time.LocalDateTime;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

/**
 * 한 지원자가 한 슬롯을 예약한 기록.
 *
 * <p>2-1단계 ①(V2)에서 {@code UNIQUE(applicant_id, slot_id)} 를 붙였다. 같은 지원자가 같은
 * 슬롯에 두 행을 만드는 중복 예약을 DB 레벨에서 차단하는 최후 방어선이다. 1단계 오버부킹
 * 재현(서로 다른 지원자의 정원 경쟁)은 이 쌍이 늘 유일해 영향을 받지 않는다. 아래
 * {@code uniqueConstraints} 는 실제 제약을 만드는 것이 아니라(스키마는 Flyway 가 관리)
 * V2 마이그레이션과 엔티티 메타데이터를 일치시켜 의도를 명시하는 것이다.
 */
@Entity
@Table(name = "reservation",
        uniqueConstraints = @UniqueConstraint(
                name = "uk_reservation_applicant_slot",
                columnNames = {"applicant_id", "slot_id"}))
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Reservation {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "applicant_id", nullable = false)
    private Applicant applicant;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "slot_id", nullable = false)
    private InterviewSlot slot;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private ReservationStatus status;

    @Column(nullable = false)
    private LocalDateTime createdAt;

    private Reservation(Applicant applicant, InterviewSlot slot, ReservationStatus status) {
        this.applicant = applicant;
        this.slot = slot;
        this.status = status;
    }

    public static Reservation confirm(Applicant applicant, InterviewSlot slot) {
        return new Reservation(applicant, slot, ReservationStatus.CONFIRMED);
    }

    @PrePersist
    void onCreate() {
        this.createdAt = LocalDateTime.now();
    }
}
