package com.interview.reservation.web.dto;

import jakarta.validation.constraints.NotNull;

public record ReservationRequest(
        @NotNull Long applicantId,
        @NotNull Long slotId) {
}
