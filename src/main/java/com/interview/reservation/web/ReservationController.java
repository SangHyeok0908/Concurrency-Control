package com.interview.reservation.web;

import com.interview.reservation.domain.Reservation;
import com.interview.reservation.service.ReservationService;
import com.interview.reservation.service.strategy.ReservationStrategyResolver;
import com.interview.reservation.web.dto.ReservationRequest;
import com.interview.reservation.web.dto.ReservationResponse;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/reservations")
@RequiredArgsConstructor
public class ReservationController {

    private final ReservationService reservationService;
    private final ReservationStrategyResolver strategyResolver;

    /**
     * 1단계 baseline 경로. 락 없는 {@link ReservationService#reserve}를 그대로 부른다 —
     * step1 Gatling 시뮬레이션이 이 경로를 때리므로 동작을 바꾸지 않는다.
     */
    @PostMapping
    public ResponseEntity<ReservationResponse> reserve(@Valid @RequestBody ReservationRequest request) {
        Reservation reservation = reservationService.reserve(request.applicantId(), request.slotId());
        return ResponseEntity.status(HttpStatus.CREATED).body(ReservationResponse.from(reservation));
    }

    /**
     * 2단계 방어 경로. {@code {strategy}} 키로 방어 전략을 골라 예약한다
     * (예: {@code unique}, {@code baseline}). 같은 Gatling 부하를 방어별로 인가해
     * before/after 를 비교하기 위한 확장점이다.
     */
    @PostMapping("/{strategy}")
    public ResponseEntity<ReservationResponse> reserveWith(
            @PathVariable String strategy,
            @Valid @RequestBody ReservationRequest request) {
        Reservation reservation = strategyResolver.resolve(strategy)
                .reserve(request.applicantId(), request.slotId());
        return ResponseEntity.status(HttpStatus.CREATED).body(ReservationResponse.from(reservation));
    }
}
