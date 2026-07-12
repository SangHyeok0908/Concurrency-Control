package com.interview.reservation.service;

import com.interview.reservation.domain.Applicant;
import com.interview.reservation.repository.ApplicantRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
public class ApplicantService {

    private final ApplicantRepository applicantRepository;

    @Transactional
    public Applicant register(String name, String email) {
        return applicantRepository.save(Applicant.of(name, email));
    }
}
