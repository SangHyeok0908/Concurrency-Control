package com.interview.reservation.concurrency;

import static org.assertj.core.api.Assertions.assertThat;

import com.interview.reservation.domain.ReservationStatus;
import com.interview.reservation.repository.InterviewSlotRepository;
import com.interview.reservation.repository.ReservationRepository;
import com.interview.reservation.service.ApplicantService;
import com.interview.reservation.service.InterviewSlotService;
import com.interview.reservation.service.strategy.BackoffPolicy;
import com.interview.reservation.service.strategy.OptimisticLockReservationStrategy;
import com.interview.reservation.service.strategy.OptimisticReservationAttempt;
import com.interview.reservation.service.strategy.OptimisticRetryStats;
import com.interview.reservation.support.AbstractIntegrationTest;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.TestPropertySource;

/**
 * ⑤의 백오프 방식을 <b>원리가 아니라 실측으로</b> 고르기 위한 실험. 고정 / 지수 /
 * 지수+지터 세 방식을 <b>같은 조건</b>에서 돌려 비용을 비교한다.
 *
 * <p><b>왜 재는가.</b> 교과서는 지터를 권한다 — 지터가 없으면 충돌한 경쟁자들이 같은 간격을
 * 자고 같은 순간 깨어나 재충돌한다는 것이다(thundering herd). 그런데 {@code Thread.sleep} 은
 * OS 스케줄러 오차 때문에 애초에 정확히 같은 시각에 깨어나지 않으므로, <b>그 자연 오차만으로
 * 이미 충분할</b> 가능성이 있다. 그렇다면 지터는 효과 없는 장식이다. 앉아서 정할 문제가
 * 아니라서 쟀다.
 *
 * <p><b>조건 설계에 함정이 있다.</b> 다른 ⑤ 테스트처럼 <b>정원 1</b>로 재면 세 방식이 반드시
 * 같게 나온다 — 첫 라운드에서 한 명이 이기는 순간 슬롯이 차버려, 나머지는 재시도할 때 버전
 * 충돌이 아니라 만석 거절을 받고 즉시 빠져나가기 때문이다. 백오프가 개입할 여지 자체가 없다.
 * 그래서 여기서는 <b>정원 = 경쟁자 = {@value #CAPACITY}</b> 로 둔다. 전원이 결국 성공하되
 * 그 과정에서 같은 행을 두고 계속 충돌하므로, "같은 일을 하는 데 시도를 몇 번 썼는가"가
 * 방식 간 <b>순수한 비용 차이</b>가 된다.
 *
 * <p><b>지표는 응답시간이 아니라 충돌 횟수다.</b> 방식마다 자는 시간의 기댓값이 다르므로
 * (고정 10ms vs 지터 평균 5ms) 응답시간은 백오프 방식 자체에 오염된다. 반면 "충돌로 버려진
 * 시도 수"는 재시도가 실제로 헛돌았는지를 직접 센다. 경과 시간은 참고로만 함께 출력한다.
 */
@SpringBootTest
@TestPropertySource(properties = {
        // 상한에 걸려 시도가 잘리면 세 방식이 "똑같이 20회에서 멈춘 것"으로 보여 비교가
        // 무의미해진다. 전원이 끝까지 성공하도록 넉넉히 준다(운영 기본값은 5).
        "reservation.optimistic.max-attempts=20",
        // 경쟁자 전원이 동시에 DB 에 닿아야 진짜로 경쟁한다. 풀이 좁으면 커넥션 대기가
        // 요청을 알아서 직렬화해 재현하려던 경합이 사라진다.
        "spring.datasource.hikari.maximum-pool-size=100",
})
class OptimisticBackoffPolicyBenchmarkTest extends AbstractIntegrationTest {

    /** 정원 = 경쟁자. 전원이 성공하되 그 과정에서 계속 충돌하는 구간이다. */
    private static final int CAPACITY = 100;
    private static final int CONTENDERS = 100;

    /** 편차가 커서 1회로는 판단할 수 없다. */
    private static final int MEASURED_ROUNDS = 5;

    /** JIT 워밍업. 첫 방식만 느리게 나오는 것을 결과로 착각하지 않기 위해 버린다. */
    private static final int WARMUP_ROUNDS = 1;

    private static final int MAX_ATTEMPTS = 20;
    private static final long BASE_MILLIS = 10;
    private static final long MAX_MILLIS = 200;

    @Autowired ApplicantService applicantService;
    @Autowired InterviewSlotService slotService;
    @Autowired OptimisticReservationAttempt attempt;
    @Autowired OptimisticRetryStats retryStats;
    @Autowired OptimisticLockReservationStrategy defaultStrategy;
    @Autowired ReservationRepository reservationRepository;
    @Autowired InterviewSlotRepository slotRepository;

    @Test
    @DisplayName("고정·지수·지수+지터를 같은 조건에서 재고, 셋 다 정합성은 동일하게 지킨다")
    void comparesBackoffPolicies() throws InterruptedException {
        List<Long> applicantIds = new ArrayList<>();
        for (int i = 0; i < CONTENDERS; i++) {
            applicantIds.add(applicantService.register("백오프" + i, "backoff" + i + "@example.com").getId());
        }

        ExecutorService pool = Executors.newFixedThreadPool(CONTENDERS);
        Map<BackoffPolicy, Result> results = new LinkedHashMap<>();

        try {
            for (BackoffPolicy policy : BackoffPolicy.values()) {
                // 설정만 다른 전략을 직접 만든다. 생성자 주입이라 가능한 일이다 —
                // 필드 주입이었다면 프로퍼티를 바꿔 컨텍스트를 세 번 띄워야 했다.
                OptimisticLockReservationStrategy strategy = new OptimisticLockReservationStrategy(
                        attempt, retryStats, MAX_ATTEMPTS, BASE_MILLIS, MAX_MILLIS, policy);

                for (int i = 0; i < WARMUP_ROUNDS; i++) {
                    runRound(pool, strategy, applicantIds);
                }

                retryStats.reset();
                long startedAt = System.nanoTime();
                long confirmed = 0;
                for (int round = 0; round < MEASURED_ROUNDS; round++) {
                    confirmed += runRound(pool, strategy, applicantIds);
                }
                long elapsedMillis = (System.nanoTime() - startedAt) / 1_000_000;

                Map<String, Object> snapshot = retryStats.snapshot();
                results.put(policy, new Result(
                        confirmed,
                        ((Number) snapshot.get("versionConflicts")).longValue(),
                        ((Number) snapshot.get("deadlocks")).longValue(),
                        ((Number) snapshot.get("retryExhausted")).longValue(),
                        ((Number) snapshot.get("meanAttemptsPerSuccess")).doubleValue(),
                        elapsedMillis));
            }
        } finally {
            pool.shutdownNow();
        }

        print(results);

        // ── ① 정합성은 백오프 방식과 무관하다 ─────────────────────────────────
        // 백오프는 "언제 다시 할지"만 정할 뿐 "무엇이 커밋되는지"에는 관여하지 않는다.
        // 오버부킹 0은 라운드마다 runRound() 가 확인하고, 여기서는 데드락이 방식에 따라
        // 되살아나지 않는지를 본다(flush 순서 교정이 백오프와 독립인지).
        results.forEach((policy, result) -> assertThat(result.deadlocks())
                .as("%s: 데드락 0 — flush 순서 교정은 백오프 방식과 무관하게 유지된다", policy)
                .isZero());

        // ── ② 비용은 방식에 따라 크게 갈린다 ─────────────────────────────────
        // 이 실험의 결론이다. 세 방식은 정합성은 같지만 <b>같은 일을 끝내지도 못한다</b> —
        // 지터 없는 방식은 상한 안에 처리하지 못하고 요청을 통째로 떨어뜨린다.
        Result fixed = results.get(BackoffPolicy.FIXED);
        Result exponential = results.get(BackoffPolicy.EXPONENTIAL);
        Result jitter = results.get(BackoffPolicy.EXPONENTIAL_JITTER);

        assertThat(jitter.confirmed())
                .as("지터가 가장 많은 요청을 처리한다 — 고정 %d건, 지수 %d건",
                        fixed.confirmed(), exponential.confirmed())
                .isGreaterThan(fixed.confirmed())
                .isGreaterThanOrEqualTo(exponential.confirmed());
        assertThat(jitter.conflicts())
                .as("그러면서 헛돈 시도는 가장 적다 — 더 많이 처리하고도 덜 충돌한다")
                .isLessThan(fixed.conflicts());

        // 기본 전략이 주입됐다는 것은 곧 `backoff-policy: exponential-jitter` 라는 하이픈
        // 표기가 enum 상수로 변환됐다는 뜻이다(변환에 실패했다면 컨텍스트가 뜨지 않는다).
        assertThat(defaultStrategy).as("운영 기본 전략이 주입되어야 한다").isNotNull();
    }

    /** 한 라운드: 새 슬롯 하나에 경쟁자 전원이 동시에 달려든다. 확정된 예약 수를 돌려준다. */
    private long runRound(ExecutorService pool,
                          OptimisticLockReservationStrategy strategy,
                          List<Long> applicantIds) throws InterruptedException {
        Long slotId = slotService.create(
                LocalDateTime.now().plusDays(1),
                LocalDateTime.now().plusDays(1).plusMinutes(30),
                CAPACITY).getId();

        CountDownLatch ready = new CountDownLatch(CONTENDERS);
        CountDownLatch fire = new CountDownLatch(1);
        CountDownLatch done = new CountDownLatch(CONTENDERS);

        for (Long applicantId : applicantIds) {
            pool.submit(() -> {
                ready.countDown();
                try {
                    fire.await();
                    strategy.reserve(applicantId, slotId);
                } catch (Exception ignored) {
                    // 실패는 아래 확정 건수 단언에서 드러난다.
                } finally {
                    done.countDown();
                }
            });
        }

        ready.await();
        fire.countDown(); // 전원 동시에 출발 — 최악 인터리빙을 강제한다
        boolean finished = done.await(60, TimeUnit.SECONDS);
        assertThat(finished).as("라운드가 시간 내에 끝나야 한다").isTrue();

        long confirmed = reservationRepository.countBySlotIdAndStatus(slotId, ReservationStatus.CONFIRMED);
        int remaining = slotRepository.findById(slotId).orElseThrow().getRemaining();
        assertThat(remaining)
                .as("확정 + 남은 자리 == 정원 — 카운터가 깨지지 않는다")
                .isEqualTo(CAPACITY - (int) confirmed);
        return confirmed;
    }

    private void print(Map<BackoffPolicy, Result> results) {
        System.out.printf("%n[백오프 방식 비교] 정원 %d · 경쟁 %d · %d라운드 (base %dms, max %dms, 상한 %d회)%n",
                CAPACITY, CONTENDERS, MEASURED_ROUNDS, BASE_MILLIS, MAX_MILLIS, MAX_ATTEMPTS);
        long requested = (long) CONTENDERS * MEASURED_ROUNDS;
        System.out.printf("%-20s %8s %8s %8s %10s %10s %10s%n",
                "방식", "성공", "소진", "성공률", "충돌", "충돌/성공", "경과(ms)");
        results.forEach((policy, r) -> System.out.printf("%-20s %8d %8d %7.1f%% %10d %10.1f %10d%n",
                policy, r.confirmed(), r.exhausted(),
                100.0 * r.confirmed() / requested,
                r.conflicts(),
                r.confirmed() == 0 ? 0.0 : (double) r.conflicts() / r.confirmed(),
                r.elapsedMillis()));
        System.out.printf("* 요청 %d건. 충돌 = 재시도로 버려진 시도 수. 경과 시간은 방식마다 자는 시간"
                + " 기댓값이 달라 참고용이다.%n", requested);
    }

    private record Result(long confirmed, long conflicts, long deadlocks,
                          long exhausted, double meanAttempts, long elapsedMillis) {
    }
}
