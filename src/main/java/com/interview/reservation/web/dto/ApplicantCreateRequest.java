package com.interview.reservation.web.dto;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record ApplicantCreateRequest(
        @NotBlank @Size(max = 50) String name,
        @NotBlank @Email String email) {
}
