package com.interview.reservation.concurrency;

import static org.assertj.core.api.Assertions.assertThat;

import com.interview.reservation.domain.ReservationStatus;
import com.interview.reservation.repository.ReservationRepository;
import com.interview.reservation.service.ApplicantService;
import com.interview.reservation.service.InterviewSlotService;
import com.interview.reservation.service.ReservationService;
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
 * 1단계 baseline 이 <b>실제로, 그러나 간헐적으로</b> 오버부킹한다는 것을 보이는 실험이다.
 *
 * <p>이 테스트는 인터리빙을 강제하지 않는다. 진짜 {@link ReservationService#reserve} 를
 * 그대로 부르고, 정원 1짜리 슬롯 하나에 서로 다른 지원자 여러 명이 동시에 달려들게 한 뒤,
 * 정원을 넘겨 확정된 예약이 생겼는지 <b>관찰만</b> 한다. 같은 시나리오를 여러 라운드 반복해
 * 몇 라운드에서 터지는지 집계한다.
 *
 * <p><b>왜 '무조건 터진다'고 단언하지 않는가.</b> 실제 MySQL 은 트랜잭션이 워낙 빨라
 * 경쟁이 저절로 직렬화되는 일이 잦다. 그래서 오버부킹은 실재하지만 매 실행 터지지는 않는다.
 * "방어가 없는데도 항상 터지지는 않는다" — 바로 이 간헐성이 baseline 의 진짜 위험 신호다.
 * 테스트로도 안 잡히고 운영에서 어느 순간 터진다. (origin story 의 "운이 좋아 안 터졌다")
 *
 * <p>따라서 단언은 <b>매 실행 신뢰할 수 있는 불변식</b>에만 건다: 확정 예약은 정원 미만으로
 * 떨어지지 않는다(최소 1건은 성공). 오버부킹 발생 여부·횟수는 콘솔 로그로 남겨 증거로 삼고,
 * 강제 재현이 필요한 결정적 검증은 2단계에서 방어를 붙인 뒤 최악-인터리빙 회귀 테스트로 한다.
 */
@SpringBootTest
@TestPropertySource(properties = {
        // 경쟁자 전원이 동시에 DB 에 닿을 수 있도록 커넥션 풀을 넉넉히 준다.
        "spring.datasource.hikari.maximum-pool-size=20",
        // 실측 로그가 SQL/바인딩 추적에 묻히지 않도록 이 테스트에서만 조용히 한다.
        "spring.jpa.properties.hibernate.show_sql=false",
        "logging.level.org.hibernate.orm.jdbc.bind=OFF"
})
class BaselineOverbookingProbeTest extends AbstractIntegrationTest {

    // 경쟁 강도를 정한다. 여기(정원 1에 15명 경쟁)가 "간헐" 구간이다 — 너무 세면(예: 100명)
    // 매번 터지고, 너무 약하면 경쟁이 저절로 직렬화돼 거의 안 터진다. 15명이면 대개는 겹쳐서
    // 터지지만 이따금 운 좋게 안 터지는, baseline 의 가장 위험한 얼굴이 드러난다.
    private static final int CAPACITY = 1;
    private static final int CONTENDERS = 15;
    private static final int ROUNDS = 10;

    @Autowired ApplicantService applicantService;
    @Autowired InterviewSlotService slotService;
    @Autowired ReservationService reservationService;
    @Autowired ReservationRepository reservationRepository;

    @Test
    @DisplayName("실제 reserve()에 동시 요청을 퍼부으면 baseline은 간헐적으로 정원을 초과한다")
    void realReserveOverbooksIntermittentlyUnderLoad() throws InterruptedException {
        // 지원자는 라운드 간 재사용한다. 매 라운드 슬롯이 새로 생기므로 (지원자, 슬롯) 쌍은
        // 늘 유일해, 여기서 보는 것은 중복 요청이 아니라 순수한 정원 경쟁이다.
        List<Long> applicantIds = new ArrayList<>();
        for (int i = 0; i < CONTENDERS; i++) {
            applicantIds.add(applicantService.register("지원자" + i, "applicant" + i + "@example.com").getId());
        }

        ExecutorService pool = Executors.newFixedThreadPool(CONTENDERS);
        List<String> report = new ArrayList<>();
        int overbookedRounds = 0;

        for (int round = 1; round <= ROUNDS; round++) {
            Long slotId = slotService.create(
                    LocalDateTime.now().plusDays(1),
                    LocalDateTime.now().plusDays(1).plusMinutes(30),
                    CAPACITY).getId();

            CountDownLatch ready = new CountDownLatch(CONTENDERS);
            CountDownLatch fire = new CountDownLatch(1);
            CountDownLatch done = new CountDownLatch(CONTENDERS);
            AtomicInteger success = new AtomicInteger();

            for (Long applicantId : applicantIds) {
                pool.submit(() -> {
                    ready.countDown();
                    try {
                        fire.await();
                        reservationService.reserve(applicantId, slotId);
                        success.incrementAndGet();
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
            boolean overbooked = confirmed > CAPACITY;
            if (overbooked) {
                overbookedRounds++;
            }
            report.add(String.format("  round %2d: confirmed=%d%s", round, confirmed, overbooked ? "   <-- 오버부킹" : ""));

            // 매 실행 신뢰할 수 있는 불변식만 단언한다.
            assertThat(finished).as("round %d 이 시간 내에 끝나야 한다", round).isTrue();
            assertThat(confirmed)
                    .as("round %d: 확정 예약은 정원(%d) 미만으로 떨어지지 않는다", round, CAPACITY)
                    .isGreaterThanOrEqualTo(CAPACITY);
            assertThat(success.get())
                    .as("round %d: 성공 카운트와 확정 행 수가 일치해야 한다", round)
                    .isEqualTo((int) confirmed);
        }
        pool.shutdownNow();

        System.out.println("=== baseline 실측: 강제 없이 실제 reserve() 동시 호출 ===");
        report.forEach(System.out::println);
        System.out.printf(
                "capacity=%d, contenders=%d → 오버부킹 %d/%d 라운드%n",
                CAPACITY, CONTENDERS, overbookedRounds, ROUNDS);
        System.out.println("이 수치는 실행마다 다르다. 방어가 없는데도 '항상' 터지진 않는다는 것 자체가 baseline 의 위험이다.");
    }
}
