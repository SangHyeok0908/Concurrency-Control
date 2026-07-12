package com.interview.reservation.exception;

/**
 * 존재하지 않는 리소스(지원자, 슬롯 등)를 참조했을 때. HTTP 404 로 변환된다.
 */
public class NotFoundException extends RuntimeException {

    public NotFoundException(String message) {
        super(message);
    }
}
