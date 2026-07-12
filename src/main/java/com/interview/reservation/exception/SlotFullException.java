package com.interview.reservation.exception;

/**
 * 남은 자리가 없는 슬롯에 예약을 시도했을 때. HTTP 409 로 변환된다.
 *
 * <p>주의: 1단계에서 이 예외가 던져진다고 해서 오버부킹이 막혔다는 뜻은 아니다. 이 검사는
 * check-then-act 의 "check" 일 뿐이고, 동시 요청은 이 검사를 <b>모두 통과한</b> 뒤 정원을
 * 넘겨 예약을 생성한다. 즉 이 예외는 순차 요청에서만 신뢰할 수 있다.
 */
public class SlotFullException extends RuntimeException {

    public SlotFullException(String message) {
        super(message);
    }
}
