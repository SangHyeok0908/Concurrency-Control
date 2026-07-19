package com.interview.reservation.concurrency;

import static org.assertj.core.api.Assertions.assertThat;

import com.interview.reservation.domain.ReservationStatus;
import com.interview.reservation.repository.InterviewSlotRepository;
import com.interview.reservation.repository.ReservationRepository;
import com.interview.reservation.service.ApplicantService;
import com.interview.reservation.service.InterviewSlotService;
import com.interview.reservation.service.strategy.ConditionalUpdateReservationStrategy;
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
 * 2-1단계 ②: 원자적 조건부 UPDATE 가 <b>오버부킹을 결정적으로</b> 없앰을 검증한다.
 *
 * <p>이 테스트는 {@link BaselineOverbookingProbeTest} 와 의도적으로 대칭이다. 같은 구도(정원
 * 1짜리 슬롯에 서로 다른 지원자 여럿이 동시에 달려듦)를 쓰되, baseline 은 정원 초과를
 * "간헐적으로 관찰만" 했던 반면 여기서는 <b>매 라운드 확정 예약이 정확히 정원과 같다</b>고
 * 강하게 단언한다. 조건부 UPDATE 의 {@code WHERE remaining > 0} 이 감소와 검사를 원자적으로
 * 묶어, 락/데드락 없이도 마지막 자리를 정확히 한 요청만 가져가기 때문이다.
 */
@SpringBootTest
class ConditionalUpdateReservationTest extends AbstractIntegrationTest {

    private static final int CAPACITY = 1;
    private static final int CONTENDERS = 20;
    private static final int ROUNDS = 10;

    @Autowired ApplicantService applicantService;
    @Autowired InterviewSlotService slotService;
    @Autowired ConditionalUpdateReservationStrategy strategy;
    @Autowired ReservationRepository reservationRepository;
    @Autowired InterviewSlotRepository slotRepository;

    @Test
    @DisplayName("정원 1짜리 슬롯에 동시 요청을 퍼부어도 조건부 UPDATE 는 매 라운드 정확히 1건만 확정한다")
    void concurrentContendersNeverOverbook() throws InterruptedException {
        // 지원자는 라운드 간 재사용한다. 매 라운드 새 슬롯이라 (지원자, 슬롯) 쌍은 늘 유일해,
        // 여기서 보는 것은 중복 예약이 아니라 순수한 정원 경쟁이다.
        List<Long> applicantIds = new ArrayList<>();
        for (int i = 0; i < CONTENDERS; i++) {
            applicantIds.add(applicantService.register("경쟁자" + i, "contender" + i + "@example.com").getId());
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
                        // SlotFullException 등 정상적으로 거절된 요청.
                    } finally {
                        done.countDown();
                    }
                });
            }

            ready.await();
            fire.countDown(); // 전원 동시에 출발
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
    }

    @Test
    @DisplayName("정원만큼만 확정되고 나머지는 만석으로 거절된다(정원 3, 경쟁 20)")
    void confirmsExactlyCapacityAndRejectsTheRest() throws InterruptedException {
        int capacity = 3;
        int contenders = 20;

        List<Long> applicantIds = new ArrayList<>();
        for (int i = 0; i < contenders; i++) {
            applicantIds.add(applicantService.register("지원자C" + i, "capc" + i + "@example.com").getId());
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
                    // 만석으로 거절.
                } finally {
                    done.countDown();
                }
            });
        }

        ready.await();
        fire.countDown();
        boolean finished = done.await(30, TimeUnit.SECONDS);
        pool.shutdownNow();

        assertThat(finished).as("모든 요청이 시간 내에 끝나야 한다").isTrue();
        assertThat(succeeded.get())
                .as("성공은 정확히 정원(%d)만큼", capacity)
                .isEqualTo(capacity);
        assertThat(reservationRepository.countBySlotIdAndStatus(slotId, ReservationStatus.CONFIRMED))
                .as("확정 예약 == 정원 — 오버부킹 0")
                .isEqualTo(capacity);
        assertThat(slotRepository.findById(slotId).orElseThrow().getRemaining())
                .as("남은 자리는 정확히 0")
                .isEqualTo(0);
    }
}
