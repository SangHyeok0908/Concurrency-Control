package com.interview.reservation.web.dto;

import com.interview.reservation.domain.InterviewSlot;
import java.time.LocalDateTime;

public record SlotResponse(
        Long id,
        LocalDateTime startAt,
        LocalDateTime endAt,
        int capacity,
        int remaining) {

    public static SlotResponse from(InterviewSlot slot) {
        return new SlotResponse(
                slot.getId(),
                slot.getStartAt(),
                slot.getEndAt(),
                slot.getCapacity(),
                slot.getRemaining());
    }
}
