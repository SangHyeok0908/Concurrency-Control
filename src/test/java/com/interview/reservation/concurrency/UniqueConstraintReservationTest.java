package com.interview.reservation.concurrency;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.interview.reservation.domain.ReservationStatus;
import com.interview.reservation.exception.DuplicateReservationException;
import com.interview.reservation.repository.InterviewSlotRepository;
import com.interview.reservation.repository.ReservationRepository;
import com.interview.reservation.service.ApplicantService;
import com.interview.reservation.service.InterviewSlotService;
import com.interview.reservation.service.strategy.UniqueConstraintReservationStrategy;
import com.interview.reservation.support.AbstractIntegrationTest;
import java.time.LocalDateTime;
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
 * 2-1단계 ①: {@code UNIQUE(applicant_id, slot_id)}(V2)가 <b>중복 예약을 결정적으로</b>
 * 차단함을 검증한다.
 *
 * <p>baseline 오버부킹({@link BaselineOverbookingProbeTest})은 트랜잭션이 저절로 직렬화돼
 * "간헐적"으로만 터졌다 — 그래서 프로브는 관찰만 하고 단언하지 않았다. 반면 <b>중복 예약은
 * DB 제약이 매번 막으므로</b> 여기서는 "정확히 1건"을 강하게 단언할 수 있다. 이 결정성 자체가
 * 애플리케이션 레벨 방어와 DB 최후 방어선의 차이다.
 */
@SpringBootTest
@TestPropertySource(properties = {
        "spring.datasource.hikari.maximum-pool-size=20",
        "spring.jpa.properties.hibernate.show_sql=false",
        "logging.level.org.hibernate.orm.jdbc.bind=OFF"
})
class UniqueConstraintReservationTest extends AbstractIntegrationTest {

    @Autowired ApplicantService applicantService;
    @Autowired InterviewSlotService slotService;
    @Autowired ReservationRepository reservationRepository;
    @Autowired InterviewSlotRepository slotRepository;
    @Autowired UniqueConstraintReservationStrategy strategy;

    @Test
    @DisplayName("같은 (지원자, 슬롯) 재요청은 DuplicateReservationException 으로 거절되고 예약은 1건뿐이다")
    void sequentialDuplicateIsRejected() {
        Long applicantId = applicantService.register("중복지원자", "dup@example.com").getId();
        Long slotId = newSlot(10);

        strategy.reserve(applicantId, slotId); // 첫 요청: 성공

        assertThatThrownBy(() -> strategy.reserve(applicantId, slotId))
                .isInstanceOf(DuplicateReservationException.class);

        assertThat(reservationRepository.countByApplicantIdAndSlotId(applicantId, slotId))
                .as("중복 재요청 뒤에도 예약 행은 1건이어야 한다")
                .isEqualTo(1);
    }

    @Test
    @DisplayName("같은 (지원자, 슬롯)에 동시 요청을 퍼부어도 DB 가 매번 막아 예약은 정확히 1건이다")
    void concurrentDuplicatesCollapseToOne() throws InterruptedException {
        int contenders = 20;
        int capacity = 10; // 정원이 아니라 UNIQUE 제약이 한계임을 분명히 하려고 넉넉히 잡는다.

        Long applicantId = applicantService.register("동시중복지원자", "dupc@example.com").getId();
        Long slotId = newSlot(capacity);

        ExecutorService pool = Executors.newFixedThreadPool(contenders);
        CountDownLatch ready = new CountDownLatch(contenders);
        CountDownLatch fire = new CountDownLatch(1);
        CountDownLatch done = new CountDownLatch(contenders);
        AtomicInteger succeeded = new AtomicInteger();
        AtomicInteger rejected = new AtomicInteger();

        for (int i = 0; i < contenders; i++) {
            pool.submit(() -> {
                ready.countDown();
                try {
                    fire.await();
                    strategy.reserve(applicantId, slotId);
                    succeeded.incrementAndGet();
                } catch (DuplicateReservationException e) {
                    rejected.incrementAndGet();
                } catch (Exception ignored) {
                    // 그 외(락 경합 등)는 이 테스트의 관심사가 아니다.
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
        assertThat(reservationRepository.countByApplicantIdAndSlotId(applicantId, slotId))
                .as("동시 중복 요청 %d건 중 DB 에 남는 예약은 정확히 1건", contenders)
                .isEqualTo(1);
        assertThat(succeeded.get())
                .as("성공은 1건, 나머지는 DuplicateReservationException 으로 거절")
                .isEqualTo(1);
        assertThat(slotRepository.findById(slotId).orElseThrow().getRemaining())
                .as("거절된 요청의 좌석 감소는 롤백돼야 한다(정확히 1자리만 소모)")
                .isEqualTo(capacity - 1);
    }

    private Long newSlot(int capacity) {
        return slotService.create(
                LocalDateTime.now().plusDays(1),
                LocalDateTime.now().plusDays(1).plusMinutes(30),
                capacity).getId();
    }
}
