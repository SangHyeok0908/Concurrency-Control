package com.interview.reservation.web;

import com.interview.reservation.exception.DuplicateReservationException;
import com.interview.reservation.exception.NotFoundException;
import com.interview.reservation.exception.RetryExhaustedException;
import com.interview.reservation.exception.SlotFullException;
import java.time.LocalDateTime;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@RestControllerAdvice
public class GlobalExceptionHandler {

    public record ErrorResponse(int status, String message, LocalDateTime timestamp) {
        static ErrorResponse of(HttpStatus status, String message) {
            return new ErrorResponse(status.value(), message, LocalDateTime.now());
        }
    }

    @ExceptionHandler(NotFoundException.class)
    public ResponseEntity<ErrorResponse> handleNotFound(NotFoundException e) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(ErrorResponse.of(HttpStatus.NOT_FOUND, e.getMessage()));
    }

    @ExceptionHandler(SlotFullException.class)
    public ResponseEntity<ErrorResponse> handleSlotFull(SlotFullException e) {
        return ResponseEntity.status(HttpStatus.CONFLICT)
                .body(ErrorResponse.of(HttpStatus.CONFLICT, e.getMessage()));
    }

    @ExceptionHandler(DuplicateReservationException.class)
    public ResponseEntity<ErrorResponse> handleDuplicate(DuplicateReservationException e) {
        return ResponseEntity.status(HttpStatus.CONFLICT)
                .body(ErrorResponse.of(HttpStatus.CONFLICT, e.getMessage()));
    }

    /**
     * ⑤ 낙관적 락이 재시도 상한을 소진한 경우. <b>만석(409)과 일부러 구분한다</b> — 만석은
     * "자리가 없다"는 확정된 답이지만 이건 "경합에 밀려 확인하지 못했다"는 <b>서버 쪽 실패</b>다.
     * 클라이언트가 잠시 후 다시 시도하면 성공할 수 있으므로 503 이 맞다. Gatling 이 409 를 정상
     * 거절로 세기 때문에, 이 실패를 409 에 섞으면 낙관적 락 고유의 실패 모드가 리포트에서
     * 사라진다는 실측상의 이유도 있다.
     */
    @ExceptionHandler(RetryExhaustedException.class)
    public ResponseEntity<ErrorResponse> handleRetryExhausted(RetryExhaustedException e) {
        return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
                .body(ErrorResponse.of(HttpStatus.SERVICE_UNAVAILABLE, e.getMessage()));
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ErrorResponse> handleValidation(MethodArgumentNotValidException e) {
        String message = e.getBindingResult().getFieldErrors().stream()
                .findFirst()
                .map(fe -> fe.getField() + ": " + fe.getDefaultMessage())
                .orElse("잘못된 요청입니다.");
        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(ErrorResponse.of(HttpStatus.BAD_REQUEST, message));
    }
}
