package com.interview.reservation.exception;

/**
 * 낙관적 락(⑤)이 <b>재시도 상한까지 쓰고도</b> 예약을 확정하지 못했을 때 던진다.
 *
 * <p><b>이 예외는 {@link SlotFullException} 과 성격이 완전히 다르다.</b> 만석 거절은 요청이
 * 정상적으로 처리된 결과이지만(자리가 없었다), 재시도 소진은 <b>자리가 있었을 수도 있는데
 * 경합에 밀려 확인조차 못 한</b> 상태다. 그래서 HTTP 상태도 409 가 아니라 503 으로 번역한다
 * ({@code GlobalExceptionHandler}).
 *
 * <p>구분을 굳이 만든 이유: Gatling 시나리오는 {@code status().in(201, 409)} 를 OK 로 세므로,
 * 이 실패를 409 에 섞으면 <b>낙관적 락의 고유 실패 모드가 리포트에서 사라진다.</b> 503 으로
 * 두면 KO 카운트에 그대로 잡혀, ⑦ 벤치마크의 "실패율" 열이 경합에 따른 재시도 폭주를
 * 정직하게 드러낸다. 다른 전략은 이 실패 모드 자체가 없다 — 그게 측정하려는 차이다.
 */
public class RetryExhaustedException extends RuntimeException {

    public RetryExhaustedException(String message) {
        super(message);
    }
}
