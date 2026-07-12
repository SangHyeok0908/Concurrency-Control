package com.interview.reservation.web;

import com.interview.reservation.domain.InterviewSlot;
import com.interview.reservation.service.InterviewSlotService;
import com.interview.reservation.web.dto.SlotCreateRequest;
import com.interview.reservation.web.dto.SlotResponse;
import jakarta.validation.Valid;
import java.util.List;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/slots")
@RequiredArgsConstructor
public class InterviewSlotController {

    private final InterviewSlotService slotService;

    @PostMapping
    public ResponseEntity<SlotResponse> create(@Valid @RequestBody SlotCreateRequest request) {
        InterviewSlot slot = slotService.create(request.startAt(), request.endAt(), request.capacity());
        return ResponseEntity.status(HttpStatus.CREATED).body(SlotResponse.from(slot));
    }

    @GetMapping
    public List<SlotResponse> findAll() {
        return slotService.findAll().stream().map(SlotResponse::from).toList();
    }

    @GetMapping("/{id}")
    public SlotResponse findById(@PathVariable Long id) {
        return SlotResponse.from(slotService.findById(id));
    }
}
