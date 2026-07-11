package com.interview.reservation.service;

import com.interview.reservation.domain.InterviewSlot;
import com.interview.reservation.exception.NotFoundException;
import com.interview.reservation.repository.InterviewSlotRepository;
import java.time.LocalDateTime;
import java.util.List;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
public class InterviewSlotService {

    private final InterviewSlotRepository slotRepository;

    @Transactional
    public InterviewSlot create(LocalDateTime startAt, LocalDateTime endAt, int capacity) {
        return slotRepository.save(InterviewSlot.of(startAt, endAt, capacity));
    }

    @Transactional(readOnly = true)
    public List<InterviewSlot> findAll() {
        return slotRepository.findAll();
    }

    @Transactional(readOnly = true)
    public InterviewSlot findById(Long id) {
        return slotRepository.findById(id)
                .orElseThrow(() -> new NotFoundException("슬롯을 찾을 수 없습니다: id=" + id));
    }
}
