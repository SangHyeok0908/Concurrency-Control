package com.interview.reservation.service.strategy;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.HashSet;
import java.util.Set;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

/**
 * 세 백오프 방식이 <b>실제로 서로 다르게 동작하는지</b>를 못 박는다.
 *
 * <p><b>왜 이 테스트가 벤치마크보다 먼저인가.</b>
 * {@code OptimisticBackoffPolicyBenchmarkTest} 는 세 방식의 비용을 비교해 하나를 고르는 실험인데,
 * 만약 세 방식이 사실은 같은 값을 내놓고 있었다면 그 비교는 <b>아무것도 측정하지 않은 것</b>이
 * 된다("차이 없음"이라는 결론이 방식 때문인지 버그 때문인지 구분할 수 없다). DB 도 스레드도
 * 필요 없는 순수 계산이므로 여기서 값으로 직접 확인한다.
 */
class BackoffPolicyTest {

    private static final long BASE = 10;
    private static final long MAX = 200;

    @Nested
    @DisplayName("FIXED — 몇 번째 실패든 같은 간격")
    class Fixed {

        @Test
        void alwaysReturnsBase() {
            for (int attemptNo = 1; attemptNo <= 8; attemptNo++) {
                assertThat(BackoffPolicy.FIXED.sleepMillis(attemptNo, BASE, MAX))
                        .as("%d번째 실패", attemptNo)
                        .isEqualTo(BASE);
            }
        }
    }

    @Nested
    @DisplayName("EXPONENTIAL — 2배씩 넓어지고 상한에서 멈춘다")
    class Exponential {

        @Test
        void doublesUntilCapped() {
            assertThat(BackoffPolicy.EXPONENTIAL.sleepMillis(1, BASE, MAX)).isEqualTo(10);
            assertThat(BackoffPolicy.EXPONENTIAL.sleepMillis(2, BASE, MAX)).isEqualTo(20);
            assertThat(BackoffPolicy.EXPONENTIAL.sleepMillis(3, BASE, MAX)).isEqualTo(40);
            assertThat(BackoffPolicy.EXPONENTIAL.sleepMillis(4, BASE, MAX)).isEqualTo(80);
            assertThat(BackoffPolicy.EXPONENTIAL.sleepMillis(5, BASE, MAX)).isEqualTo(160);
        }

        @Test
        @DisplayName("상한을 넘기려 해도 상한에서 잘린다 — 지수 증가가 응답시간을 지배하지 못한다")
        void neverExceedsMax() {
            // 시도 횟수를 크게 잡아도 값이 폭주하지 않는지 본다(오버플로 방지 경로).
            for (int attemptNo = 6; attemptNo <= 64; attemptNo++) {
                assertThat(BackoffPolicy.EXPONENTIAL.sleepMillis(attemptNo, BASE, MAX))
                        .as("%d번째 실패", attemptNo)
                        .isEqualTo(MAX);
            }
        }

        @Test
        @DisplayName("전원이 똑같은 값을 받는다 — 이것이 thundering herd 가설의 전제다")
        void isDeterministic() {
            Set<Long> observed = new HashSet<>();
            for (int i = 0; i < 100; i++) {
                observed.add(BackoffPolicy.EXPONENTIAL.sleepMillis(3, BASE, MAX));
            }
            assertThat(observed).containsExactly(40L);
        }
    }

    @Nested
    @DisplayName("EXPONENTIAL_JITTER — 같은 창 안에서 매번 다른 값")
    class ExponentialJitter {

        @Test
        @DisplayName("창을 넘지 않는다 — 0도 창 자체도 뽑힐 수 있다(full jitter)")
        void staysWithinWindow() {
            for (int attemptNo = 1; attemptNo <= 8; attemptNo++) {
                long window = BackoffPolicy.EXPONENTIAL.sleepMillis(attemptNo, BASE, MAX);
                for (int i = 0; i < 200; i++) {
                    assertThat(BackoffPolicy.EXPONENTIAL_JITTER.sleepMillis(attemptNo, BASE, MAX))
                            .as("%d번째 실패의 창은 [0, %d]", attemptNo, window)
                            .isBetween(0L, window);
                }
            }
        }

        @Test
        @DisplayName("값이 흩어진다 — EXPONENTIAL 과 달리 한 점에 모이지 않는다")
        void spreadsAcrossTheWindow() {
            Set<Long> observed = new HashSet<>();
            for (int i = 0; i < 200; i++) {
                observed.add(BackoffPolicy.EXPONENTIAL_JITTER.sleepMillis(3, BASE, MAX));
            }
            // 창이 [0, 40] 이므로 200번 뽑으면 서로 다른 값이 넉넉히 나온다. 이 단언이 깨지면
            // 지터가 사실상 고정값으로 퇴화한 것이고, 벤치마크의 비교 대상이 사라진다.
            assertThat(observed)
                    .as("지터가 실제로 무작위여야 벤치마크가 의미를 갖는다")
                    .hasSizeGreaterThan(10);
        }
    }

    @Test
    @DisplayName("base 가 상한보다 커도 상한을 넘지 않는다 — 설정 실수에 대한 방어")
    void respectsMaxEvenWhenBaseIsLarger() {
        assertThat(BackoffPolicy.FIXED.sleepMillis(1, 500, MAX)).isEqualTo(MAX);
        assertThat(BackoffPolicy.EXPONENTIAL.sleepMillis(1, 500, MAX)).isEqualTo(MAX);
        assertThat(BackoffPolicy.EXPONENTIAL_JITTER.sleepMillis(1, 500, MAX)).isBetween(0L, MAX);
    }
}
