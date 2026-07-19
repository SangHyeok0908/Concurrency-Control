package com.interview.reservation.concurrency;

import static org.assertj.core.api.Assertions.assertThat;

import com.interview.reservation.domain.ReservationStatus;
import com.interview.reservation.exception.RetryExhaustedException;
import com.interview.reservation.exception.SlotFullException;
import com.interview.reservation.repository.InterviewSlotRepository;
import com.interview.reservation.repository.ReservationRepository;
import com.interview.reservation.service.ApplicantService;
import com.interview.reservation.service.InterviewSlotService;
import com.interview.reservation.service.strategy.OptimisticLockReservationStrategy;
import com.interview.reservation.support.AbstractIntegrationTest;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.TestPropertySource;

/**
 * ⑤ 낙관적 락의 <b>재시도 상한이 실제로 동작하는가</b>를 검증한다.
 *
 * <p><b>왜 별도 테스트인가.</b> 기본 설정(5회)에서는 재시도가 대부분 흡수돼 상한에 닿지
 * 않는다 — 상한이 있는지 없는지 구분되지 않는다는 뜻이다. 여기서는 상한을 <b>1로 낮춰</b>
 * (= 재시도 없음) 충돌이 곧바로 소진으로 이어지게 만들고, 그 상태에서도
 * <b>정합성이 깨지지 않는지</b>를 본다.
 *
 * <p>확인하려는 것은 두 가지다.
 * <ol>
 *   <li><b>상한에 이빨이 있다</b> — 상한을 넘긴 요청은 조용히 성공하거나 무한히 돌지 않고
 *       {@link RetryExhaustedException} 으로 <b>명시적으로</b> 실패한다.</li>
 *   <li><b>실패해도 데이터는 온전하다</b> — 소진된 시도의 트랜잭션은 통째로 롤백되므로
 *       자리를 먹어 치우지 않는다({@code 확정 + 남은 자리 == 정원}).</li>
 * </ol>
 *
 * <p>이 대비가 ⑤의 트레이드오프를 가장 선명하게 보여준다: 상한은 <b>정합성을 지키는 대신
 * 가용성을 깎는다.</b> 자리가 남아 있는데도 요청이 503 으로 떨어질 수 있고, 그 빈도는
 * 경합에 비례한다. 다른 전략에는 이 실패 모드 자체가 없다.
 */
@SpringBootTest
// 공통 설정(커넥션 풀·로그)은 test 프로파일에 있다. 여기 남긴 것은 이 테스트의 존재 이유인
// 상한 1 하나뿐이다 — 재시도 없음, 충돌하면 곧바로 소진.
@TestPropertySource(properties = "reservation.optimistic.max-attempts=1")
class OptimisticLockRetryLimitTest extends AbstractIntegrationTest {

    /** 자리를 넉넉히 둔다 — 거절이 '만석' 때문이 아니라 '충돌' 때문임을 분리하기 위해서다. */
    private static final int CAPACITY = 20;
    private static final int CONTENDERS = 20;

    @Autowired ApplicantService applicantService;
    @Autowired InterviewSlotService slotService;
    @Autowired OptimisticLockReservationStrategy strategy;
    @Autowired ReservationRepository reservationRepository;
    @Autowired InterviewSlotRepository slotRepository;

    @Test
    @DisplayName("재시도 상한을 1로 낮추면 충돌한 요청은 무한히 돌지 않고 명시적으로 실패한다")
    void exhaustsRetriesInsteadOfSpinningForever() throws InterruptedException {
        List<Long> applicantIds = new ArrayList<>();
        for (int i = 0; i < CONTENDERS; i++) {
            applicantIds.add(applicantService.register("상한테스트" + i, "limit" + i + "@example.com").getId());
        }
        Long slotId = slotService.create(
                LocalDateTime.now().plusDays(1),
                LocalDateTime.now().plusDays(1).plusMinutes(30),
                CAPACITY).getId();

        ExecutorService pool = Executors.newFixedThreadPool(CONTENDERS);
        CountDownLatch ready = new CountDownLatch(CONTENDERS);
        CountDownLatch fire = new CountDownLatch(1);
        CountDownLatch done = new CountDownLatch(CONTENDERS);
        AtomicInteger succeeded = new AtomicInteger();
        AtomicInteger exhausted = new AtomicInteger();
        AtomicInteger rejectedAsFull = new AtomicInteger();

        for (Long applicantId : applicantIds) {
            pool.submit(() -> {
                ready.countDown();
                try {
                    fire.await();
                    strategy.reserve(applicantId, slotId);
                    succeeded.incrementAndGet();
                } catch (RetryExhaustedException e) {
                    exhausted.incrementAndGet();
                } catch (SlotFullException e) {
                    rejectedAsFull.incrementAndGet();
                } catch (Exception ignored) {
                    // 그 밖의 예외는 아래 합계 단언에서 드러난다(개발 중 데드락을 여기서 잡아냈다).
                } finally {
                    done.countDown();
                }
            });
        }

        ready.await();
        fire.countDown();
        // 상한이 1이라 백오프도 없다. 무한 재시도였다면 이 대기가 터졌을 것이다.
        boolean finished = done.await(30, TimeUnit.SECONDS);
        pool.shutdownNow();

        long confirmed = reservationRepository.countBySlotIdAndStatus(slotId, ReservationStatus.CONFIRMED);
        int remaining = slotRepository.findById(slotId).orElseThrow().getRemaining();

        assertThat(finished)
                .as("상한이 있으므로 모든 요청이 유한 시간에 끝나야 한다 — 무한 재시도가 아니다")
                .isTrue();
        assertThat(succeeded.get() + exhausted.get() + rejectedAsFull.get())
                .as("모든 요청은 성공·소진·만석 중 하나로 결말이 난다 — 삼켜지는 요청이 없다")
                .isEqualTo(CONTENDERS);
        assertThat(exhausted.get())
                .as("자리가 %d개나 남아도는데도 충돌 때문에 실패한 요청이 있어야 한다 "
                        + "— 상한이 실제로 작동한다는 증거", CAPACITY)
                .isPositive();
        assertThat(confirmed)
                .as("확정 예약은 정원을 넘지 않는다 — 오버부킹 0")
                .isLessThanOrEqualTo(CAPACITY);
        assertThat(remaining)
                .as("소진된 시도는 통째로 롤백된다 — 확정 + 남은 자리 == 정원")
                .isEqualTo(CAPACITY - (int) confirmed);

        System.out.printf("[재시도 상한=1] 확정 %d · 소진 %d · 만석거절 %d (정원 %d, 경쟁 %d)%n",
                confirmed, exhausted.get(), rejectedAsFull.get(), CAPACITY, CONTENDERS);
    }
}
