package com.interview.reservation.web;

import com.interview.reservation.domain.Applicant;
import com.interview.reservation.service.ApplicantService;
import com.interview.reservation.web.dto.ApplicantCreateRequest;
import com.interview.reservation.web.dto.ApplicantResponse;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/applicants")
@RequiredArgsConstructor
public class ApplicantController {

    private final ApplicantService applicantService;

    @PostMapping
    public ResponseEntity<ApplicantResponse> register(@Valid @RequestBody ApplicantCreateRequest request) {
        Applicant applicant = applicantService.register(request.name(), request.email());
        return ResponseEntity.status(HttpStatus.CREATED).body(ApplicantResponse.from(applicant));
    }
}
