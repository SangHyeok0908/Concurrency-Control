package com.interview.reservation.exception;

/**
 * 같은 지원자가 같은 슬롯에 이미 예약이 있는데 또 예약을 시도했을 때. HTTP 409 로 변환된다.
 *
 * <p>2-1단계 ①에서 도입한 {@code UNIQUE(applicant_id, slot_id)} 위반을 애플리케이션 예외로
 * 번역한 것이다. DB 가 던지는 {@link org.springframework.dao.DataIntegrityViolationException}
 * 은 구현 세부(제약 이름·SQLState)를 흘리므로, 방어 전략 계층에서 이 도메인 예외로 감싸
 * 클라이언트에게는 "이미 예약된 슬롯"이라는 의미만 전달한다.
 *
 * <p>주의: 이 예외는 <b>중복 요청</b>을 막는 것이지 오버부킹(정원 경쟁)을 막지 않는다. 서로 다른
 * 지원자들이 정원을 넘겨 예약하는 문제는 ②(조건부 UPDATE) 이후에서 다룬다.
 */
public class DuplicateReservationException extends RuntimeException {

    public DuplicateReservationException(String message) {
        super(message);
    }
}
