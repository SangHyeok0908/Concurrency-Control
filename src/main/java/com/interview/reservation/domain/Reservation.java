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
import java.time.LocalDateTime;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

/**
 * 한 지원자가 한 슬롯을 예약한 기록.
 *
 * <p>1단계에는 {@code UNIQUE(applicant_id, slot_id)} 가 <b>없다.</b> 같은 지원자가 같은
 * 슬롯에 두 행을 만드는 중복 예약을 재현해야 하기 때문이다. 이 제약은 2-1단계(V2)에서 붙인다.
 */
@Entity
@Table(name = "reservation")
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
