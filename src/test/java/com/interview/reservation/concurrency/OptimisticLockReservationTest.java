package com.interview.reservation.concurrency;

import static org.assertj.core.api.Assertions.assertThat;

import com.interview.reservation.domain.ReservationStatus;
import com.interview.reservation.repository.InterviewSlotRepository;
import com.interview.reservation.repository.ReservationRepository;
import com.interview.reservation.service.ApplicantService;
import com.interview.reservation.service.InterviewSlotService;
import com.interview.reservation.service.strategy.OptimisticLockReservationStrategy;
import com.interview.reservation.service.strategy.OptimisticRetryStats;
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

/**
 * 2-2단계 ⑤: 낙관적 락({@code @Version}) + 지수 백오프 재시도가 <b>오버부킹을 0으로</b>
 * 만듦을 검증한다.
 *
 * <p>{@link ConditionalUpdateReservationTest}·{@link PessimisticLockReservationTest} 와
 * <b>의도적으로 같은 구도·같은 단언</b>이다(정원/경쟁자/라운드 수까지 동일). 실험 통제
 * 원칙(브랜치 전략 문서)상 락마다 유리한 시나리오를 만들지 않으므로, ②가 이미 충분했던 바로
 * 그 상황에서 ⑤를 잰다. 여기서 확인하는 것은 "낙관적 락도 오버부킹 0을 달성한다"이고,
 * "대신 무엇을 치렀는가"(재시도 횟수)는 {@link OptimisticLockRetryLimitTest} 와 ⑦이 다룬다.
 */
@SpringBootTest
class OptimisticLockReservationTest extends AbstractIntegrationTest {

    private static final int CAPACITY = 1;
    private static final int CONTENDERS = 20;
    private static final int ROUNDS = 10;

    @Autowired ApplicantService applicantService;
    @Autowired InterviewSlotService slotService;
    @Autowired OptimisticLockReservationStrategy strategy;
    @Autowired OptimisticRetryStats retryStats;
    @Autowired ReservationRepository reservationRepository;
    @Autowired InterviewSlotRepository slotRepository;

    @Test
    @DisplayName("정원 1짜리 슬롯에 동시 요청을 퍼부어도 낙관적 락은 매 라운드 정확히 1건만 확정한다")
    void concurrentContendersNeverOverbook() throws InterruptedException {
        // 지원자는 라운드 간 재사용한다. 매 라운드 새 슬롯이라 (지원자, 슬롯) 쌍은 늘 유일해,
        // 여기서 보는 것은 중복 예약이 아니라 순수한 정원 경쟁이다.
        List<Long> applicantIds = new ArrayList<>();
        for (int i = 0; i < CONTENDERS; i++) {
            applicantIds.add(applicantService.register("낙관경쟁자" + i, "opt" + i + "@example.com").getId());
        }

        ExecutorService pool = Executors.newFixedThreadPool(CONTENDERS);

        for (int round = 1; round <= ROUNDS; round++) {
            Long slotId = slotService.create(
                    LocalDateTime.now().plusDays(1),
                    LocalDateTime.now().plusDays(1).plusMinutes(30),
                    CAPACITY).getId();

            CountDownLatch ready = new CountDownLatch(CONTENDERS);
            CountDownLatch fire = new CountDownLatch(1);
            CountDownLatch done = new CountDownLatch(CONTENDERS);
            AtomicInteger succeeded = new AtomicInteger();

            for (Long applicantId : applicantIds) {
                pool.submit(() -> {
                    ready.countDown();
                    try {
                        fire.await();
                        strategy.reserve(applicantId, slotId);
                        succeeded.incrementAndGet();
                    } catch (Exception ignored) {
                        // SlotFullException(만석) 또는 RetryExhaustedException(재시도 소진).
                    } finally {
                        done.countDown();
                    }
                });
            }

            ready.await();
            fire.countDown(); // 전원 동시에 출발
            // 충돌한 경쟁자들이 백오프하며 다시 도는 시간이 필요하다. 넉넉히 준다.
            boolean finished = done.await(30, TimeUnit.SECONDS);

            long confirmed = reservationRepository.countBySlotIdAndStatus(slotId, ReservationStatus.CONFIRMED);
            int remaining = slotRepository.findById(slotId).orElseThrow().getRemaining();

            assertThat(finished).as("round %d 이 시간 내에 끝나야 한다", round).isTrue();
            assertThat(confirmed)
                    .as("round %d: 확정 예약은 정원(%d)과 정확히 같아야 한다 — 오버부킹 0", round, CAPACITY)
                    .isEqualTo(CAPACITY);
            assertThat(succeeded.get())
                    .as("round %d: 성공 카운트와 확정 행 수가 일치해야 한다", round)
                    .isEqualTo((int) confirmed);
            assertThat(remaining)
                    .as("round %d: 남은 자리는 정확히 0 — 음수(오버부킹)로 내려가지 않는다", round)
                    .isEqualTo(0);
        }
        pool.shutdownNow();

        // 이 브랜치의 고유 비용. 다른 전략에는 이 숫자 자체가 존재하지 않는다.
        System.out.println("[낙관적 락] 정원 1 경쟁 20 · " + ROUNDS + "라운드 재시도 분포: "
                + retryStats.snapshot());
    }

    @Test
    @DisplayName("정원만큼만 확정되고 나머지는 거절된다(정원 3, 경쟁 20)")
    void confirmsExactlyCapacityAndRejectsTheRest() throws InterruptedException {
        int capacity = 3;
        int contenders = 20;

        List<Long> applicantIds = new ArrayList<>();
        for (int i = 0; i < contenders; i++) {
            applicantIds.add(applicantService.register("지원자O" + i, "capo" + i + "@example.com").getId());
        }
        Long slotId = slotService.create(
                LocalDateTime.now().plusDays(1),
                LocalDateTime.now().plusDays(1).plusMinutes(30),
                capacity).getId();

        ExecutorService pool = Executors.newFixedThreadPool(contenders);
        CountDownLatch ready = new CountDownLatch(contenders);
        CountDownLatch fire = new CountDownLatch(1);
        CountDownLatch done = new CountDownLatch(contenders);
        AtomicInteger succeeded = new AtomicInteger();

        for (Long applicantId : applicantIds) {
            pool.submit(() -> {
                ready.countDown();
                try {
                    fire.await();
                    strategy.reserve(applicantId, slotId);
                    succeeded.incrementAndGet();
                } catch (Exception ignored) {
                    // 만석 거절 또는 재시도 소진.
                } finally {
                    done.countDown();
                }
            });
        }

        ready.await();
        fire.countDown();
        boolean finished = done.await(30, TimeUnit.SECONDS);
        pool.shutdownNow();

        long confirmed = reservationRepository.countBySlotIdAndStatus(slotId, ReservationStatus.CONFIRMED);
        int remaining = slotRepository.findById(slotId).orElseThrow().getRemaining();

        assertThat(finished).as("모든 요청이 시간 내에 끝나야 한다").isTrue();
        // ④와 같은 단언(isEqualTo)을 쓴다. 재시도 상한(5회) 탓에 자리를 다 못 채울 가능성을
        // 의심해 5회 반복 실측했으나 5/5 모두 정확히 정원을 채웠다 — 20명이 3자리를 두고
        // 다투는 정도의 경합은 5회 안에 충분히 흡수된다. 상한이 실제로 자리를 비우는 지점은
        // OptimisticLockRetryLimitTest 가 상한을 1로 낮춰 따로 보여준다.
        assertThat(confirmed)
                .as("확정 예약 == 정원(%d) — 오버부킹 0", capacity)
                .isEqualTo(capacity);
        assertThat(succeeded.get())
                .as("성공 카운트와 확정 행 수가 일치해야 한다")
                .isEqualTo((int) confirmed);
        assertThat(remaining)
                .as("남은 자리와 확정 수의 합은 언제나 정원 — 카운터가 깨지지 않는다")
                .isEqualTo(capacity - (int) confirmed);
        assertThat(remaining)
                .as("남은 자리는 음수가 되지 않는다")
                .isGreaterThanOrEqualTo(0);
    }
}
