package com.interview.reservation.web.dto;

import jakarta.validation.constraints.Future;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import java.time.LocalDateTime;

/**
 * 부하 테스트 준비용 슬롯 생성 요청. 계획서의 1단계 API 목록(지원자 등록 / 슬롯 조회 /
 * 예약) 을 실제로 굴리려면 슬롯을 만들 수단이 필요하므로 함께 둔다.
 */
public record SlotCreateRequest(
        @NotNull @Future LocalDateTime startAt,
        @NotNull @Future LocalDateTime endAt,
        @Positive int capacity) {
}
