package com.interview.reservation.domain;

/**
 * 예약 상태. 1단계에서는 예약이 생성되면 곧바로 {@link #CONFIRMED} 다. {@link #CANCELED}
 * 는 스키마와 도메인 모델의 완결성을 위해 정의해 두지만, 취소 API 는 이후 단계에서 다룬다.
 */
public enum ReservationStatus {
    CONFIRMED,
    CANCELED
}
