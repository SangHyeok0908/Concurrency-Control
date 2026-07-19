package com.interview.reservation.service.strategy;

import com.interview.reservation.domain.Reservation;
import com.interview.reservation.exception.RetryExhaustedException;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.ConcurrencyFailureException;
import org.springframework.dao.OptimisticLockingFailureException;
import org.springframework.stereotype.Component;

/**
 * 2-2단계 ⑤: <b>낙관적 락</b>({@code @Version}) + <b>지수 백오프 재시도</b>로 오버부킹을 막는
 * 방어 전략. 스키마에 {@code version} 컬럼을 추가한다(V3).
 *
 * <p><b>④와 무엇이 다른가.</b> 비관적 락은 "혹시 충돌할지 모르니 <b>미리</b> 잠근다"이고,
 * 낙관적 락은 "충돌은 드물 테니 일단 진행하고 <b>커밋 때 확인</b>한다"이다. 그래서 ④는
 * 경쟁자가 <b>기다리고</b>, ⑤는 경쟁자가 <b>실패한 뒤 다시 한다</b>. 대기 비용을 재시도
 * 비용으로 바꾼 교환이며, 어느 쪽이 싼지는 <b>충돌 빈도</b>가 정한다 — 충돌이 드물면 ⑤가
 * 공짜에 가깝고, 잦으면 재시도가 폭주해 ④보다 비싸진다. ⑦의 두 경합 지점이 그 갈림을
 * 실측하는 자리다.
 *
 * <p><b>재시도가 왜 이 클래스에 있나.</b> 버전 충돌은 메서드 본문이 아니라 <b>커밋 시점</b>에
 * 터지므로, 재시도는 트랜잭션 경계 <b>바깥</b>이어야 한다. 그래서 이 클래스에는
 * {@code @Transactional} 이 없고, 트랜잭션은 별도 빈인 {@link OptimisticReservationAttempt} 가
 * 연다. 자세한 근거는 그쪽 javadoc 에 있다.
 *
 * <p><b>상한과 백오프는 장식이 아니다.</b> 재시도에 상한이 없으면 경합이 심할 때 요청이
 * 영원히 돌며 커넥션을 물고 있어 <b>부하가 스스로를 증폭</b>시킨다 — 이쪽은 원리에 근거한
 * 설계 판단이다(상한을 없애 보는 실험은 하지 않았다). 반면 <b>지터의 효과는 실측했다</b>:
 * 같은 조건에서 지터 없는 백오프는 요청의 24~75% 를 상한 안에 처리하지 못했다
 * (docs/STEP2-OPTIMISTIC-LOCK.md 6절, {@link BackoffPolicy}). 상한을 넘기면
 * {@link RetryExhaustedException} 으로 503 을 돌려준다 — 만석(409)과 <b>구분해서</b>.
 *
 * <p><b>이 전략이 다루지 않는 것.</b> 같은 지원자의 중복 <i>예약</i>은 여전히 ①(V2 UNIQUE)의
 * 몫이다. 낙관적 락은 슬롯 행의 데이터 레이스만 본다.
 */
@Component
public class OptimisticLockReservationStrategy implements ReservationStrategy {

    private final OptimisticReservationAttempt attempt;
    private final OptimisticRetryStats retryStats;

    /**
     * 한 요청이 쓸 수 있는 최대 시도 횟수. <b>상한 없는 재시도는 장애 증폭기</b>이므로 반드시
     * 유한해야 한다(브랜치 전략 ⑤의 "재시도 횟수 상한 필수").
     *
     * <p><b>기본값 5에 실측 근거는 없다 — 재시도 상한의 관례값이다.</b> 오히려 실측은 이 값의
     * 한계를 보여준다: 정원 100·경쟁 120에서 상한 5는 <b>120건 중 70건을 503으로 떨구고 자리
     * 절반을 빈 채로</b> 남겼고, 자리를 다 채우려면 상한을 20까지 올려야 했는데 그때는 평균
     * 응답이 574ms(TPS 60→40)였다.
     *
     * <p>즉 이 도메인에는 <b>고를 만한 좋은 값이 아예 없다</b>. 낮으면 가용성을, 높으면 응답
     * 시간을 내준다. 이 "정답 없는 튜닝"이 ⑤가 치르는 비용의 일부이며, ⑤를 채택하지 않는
     * 근거 중 하나다(docs/STEP2-OPTIMISTIC-LOCK.md 1절·5-2절).
     */
    private final int maxAttempts;

    /** 백오프 기준 간격(ms). 첫 재시도는 [0, base], 다음은 [0, 2*base] … 로 창이 넓어진다. */
    private final long backoffBaseMillis;

    /** 백오프 상한(ms). 지수적으로 늘어난 대기가 응답시간을 지배하지 않도록 자른다. */
    private final long backoffMaxMillis;

    /**
     * 물러서는 방식. 기본값 {@code exponential-jitter} 는 <b>실측으로 고른 것</b>이며 근거는
     * docs/STEP2-OPTIMISTIC-LOCK.md 6절에 있다({@link BackoffPolicy} 참고).
     */
    private final BackoffPolicy backoffPolicy;

    public OptimisticLockReservationStrategy(
            OptimisticReservationAttempt attempt,
            OptimisticRetryStats retryStats,
            @Value("${reservation.optimistic.max-attempts:5}") int maxAttempts,
            @Value("${reservation.optimistic.backoff-base-millis:10}") long backoffBaseMillis,
            @Value("${reservation.optimistic.backoff-max-millis:200}") long backoffMaxMillis,
            @Value("${reservation.optimistic.backoff-policy:exponential-jitter}") BackoffPolicy backoffPolicy) {
        this.attempt = attempt;
        this.retryStats = retryStats;
        this.maxAttempts = maxAttempts;
        this.backoffBaseMillis = backoffBaseMillis;
        this.backoffMaxMillis = backoffMaxMillis;
        this.backoffPolicy = backoffPolicy;
    }

    @Override
    public String key() {
        return "optimistic";
    }

    /**
     * 버전 충돌이 나는 동안 재시도한다. 만석({@code SlotFullException})·없는 슬롯
     * ({@code NotFoundException})은 <b>재시도하지 않는다</b> — 다시 해도 결과가 같은,
     * 정상적으로 결정된 거절이기 때문이다. 재시도는 오직 <b>"내가 늦었다"</b>는 신호인
     * 버전 충돌에만 쓴다.
     */
    @Override
    public Reservation reserve(Long applicantId, Long slotId) {
        for (int attemptNo = 1; attemptNo <= maxAttempts; attemptNo++) {
            try {
                Reservation reservation = attempt.attempt(applicantId, slotId);
                retryStats.recordSuccess(attemptNo);
                return reservation;
            } catch (ConcurrencyFailureException conflict) {
                // 내가 읽은 뒤 다른 트랜잭션이 먼저 커밋했거나(버전 충돌), DB 가 나를 데드락
                // 희생자로 골랐다. 어느 쪽이든 이 시도는 통째로 롤백됐으므로 남은 부작용이
                // 없다 — 잠시 물러섰다가 최신 상태로 처음부터 다시 한다.
                //
                // 데드락(CannotAcquireLockException)까지 재시도 대상에 넣은 것은 안전망이다.
                // 원래 이 경로는 FK 락 승격 때문에 데드락이 쏟아졌고, 지금은 flush 순서를
                // 바로잡아 0건이다(OptimisticReservationAttempt 참고). 다시 나타난다면 조용히
                // 재시도로 덮이지 않고 아래 통계에 데드락으로 잡히도록 따로 센다.
                if (conflict instanceof OptimisticLockingFailureException) {
                    retryStats.recordVersionConflict();
                } else {
                    retryStats.recordDeadlock();
                }
                if (attemptNo == maxAttempts) {
                    break;
                }
                backOff(attemptNo, slotId);
            }
        }

        retryStats.recordExhausted();
        throw new RetryExhaustedException(
                "예약 경합이 심해 " + maxAttempts + "회 시도 안에 처리하지 못했습니다: slotId=" + slotId);
    }

    /**
     * 다음 시도 전까지 물러선다. 얼마나 물러설지는 {@link BackoffPolicy} 가 정한다 — 세 방식을
     * 같은 조건에서 재보고 고른 축이라, 계산식을 여기 박아 두지 않고 갈아 끼울 수 있게 뺐다.
     */
    private void backOff(int attemptNo, Long slotId) {
        long sleep = backoffPolicy.sleepMillis(attemptNo, backoffBaseMillis, backoffMaxMillis);
        try {
            Thread.sleep(sleep);
        } catch (InterruptedException e) {
            // 인터럽트를 삼키면 상위(스레드 풀 종료 등)가 멈출 수 없다. 플래그를 복원하고
            // 재시도를 포기한다 — 대기를 요구받은 상황에서 계속 도는 것이 더 나쁘다.
            Thread.currentThread().interrupt();
            throw new RetryExhaustedException("재시도 대기 중 인터럽트되었습니다: slotId=" + slotId);
        }
    }
}
