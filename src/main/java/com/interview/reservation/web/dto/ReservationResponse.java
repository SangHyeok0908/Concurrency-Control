package com.interview.reservation.web.dto;

import com.interview.reservation.domain.Reservation;
import com.interview.reservation.domain.ReservationStatus;
import java.time.LocalDateTime;

public record ReservationResponse(
        Long id,
        Long applicantId,
        Long slotId,
        ReservationStatus status,
        LocalDateTime createdAt) {

    public static ReservationResponse from(Reservation reservation) {
        return new ReservationResponse(
                reservation.getId(),
                reservation.getApplicant().getId(),
                reservation.getSlot().getId(),
                reservation.getStatus(),
                reservation.getCreatedAt());
    }
}
