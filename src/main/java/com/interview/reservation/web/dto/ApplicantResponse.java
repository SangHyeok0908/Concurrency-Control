package com.interview.reservation.web.dto;

import com.interview.reservation.domain.Applicant;
import java.time.LocalDateTime;

public record ApplicantResponse(
        Long id,
        String name,
        String email,
        LocalDateTime createdAt) {

    public static ApplicantResponse from(Applicant applicant) {
        return new ApplicantResponse(
                applicant.getId(),
                applicant.getName(),
                applicant.getEmail(),
                applicant.getCreatedAt());
    }
}
