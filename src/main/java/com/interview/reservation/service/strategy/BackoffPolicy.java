package com.interview.reservation.service.strategy;

import java.util.concurrent.ThreadLocalRandom;

/**
 * 충돌한 시도가 <b>얼마나 물러섰다가</b> 다시 할지 정하는 방식. 세 가지를 나란히 두는 이유는
 * 이것 자체가 ⑤의 실험 축이기 때문이다 — 어느 쪽을 쓸지 원리로 고르지 않고 <b>재서</b> 골랐고,
 * 그 근거는 docs/STEP2-OPTIMISTIC-LOCK.md 6절에 있다.
 *
 * <p>교과서는 {@link #EXPONENTIAL_JITTER} 를 권한다. 지터가 없으면 같은 순간 충돌한 경쟁자들이
 * 같은 간격을 자고 <b>같은 순간 다시 깨어나</b> 재충돌한다는 것이다(thundering herd). 다만 이
 * 주장에는 검증할 구석이 있다 — {@code Thread.sleep} 은 OS 스케줄러 오차 때문에 애초에 정확히
 * 같은 시각에 깨어나지 않으므로, <b>그 자연 오차만으로 이미 충분할</b> 가능성이 있다. 그래서
 * 세 방식을 모두 구현해 같은 조건에서 쟀다.
 *
 * <p>측정에 쓰는 지표는 응답시간이 아니라 <b>충돌로 버려진 시도 수</b>다. 방식마다 자는 시간의
 * 기댓값이 다르므로(고정 10ms vs 지터 평균 5ms) 응답시간은 백오프 방식 자체에 오염된다.
 */
public enum BackoffPolicy {

    /** 매번 같은 간격. 경합이 심해져도 물러서는 폭이 그대로라 압력을 낮추지 못한다. */
    FIXED {
        @Override
        long sleepMillis(int attemptNo, long baseMillis, long maxMillis) {
            return Math.min(baseMillis, maxMillis);
        }
    },

    /** 실패할수록 2배씩 물러선다. 다만 <b>전원이 똑같은 시간을 잔다</b> — 대열을 유지한 후퇴다. */
    EXPONENTIAL {
        @Override
        long sleepMillis(int attemptNo, long baseMillis, long maxMillis) {
            return windowMillis(attemptNo, baseMillis, maxMillis);
        }
    },

    /**
     * 지수로 넓힌 창 <b>안에서 무작위로</b> 뽑는다(full jitter). 재시도가 시간축에 흩어지므로
     * 이론상 재충돌이 줄어든다. 이 프로젝트의 기본값.
     */
    EXPONENTIAL_JITTER {
        @Override
        long sleepMillis(int attemptNo, long baseMillis, long maxMillis) {
            long window = windowMillis(attemptNo, baseMillis, maxMillis);
            // 상한 포함이라 +1. 0ms(= 즉시 재시도)도 뽑힐 수 있다는 것이 full jitter 의 정의다.
            return ThreadLocalRandom.current().nextLong(window + 1);
        }
    };

    /**
     * {@code attemptNo} 번째 시도가 실패했을 때 실제로 잘 시간(ms).
     *
     * @param attemptNo  방금 실패한 시도의 번호(1부터)
     * @param baseMillis 기준 간격
     * @param maxMillis  아무리 커져도 넘지 않을 상한
     */
    abstract long sleepMillis(int attemptNo, long baseMillis, long maxMillis);

    /**
     * 지수적으로 넓어지는 대기 창. 실패가 거듭될수록 2배씩 키우되 {@code maxMillis} 에서 자른다.
     *
     * <p>{@link #EXPONENTIAL} 은 이 값을 <b>그대로</b> 자고, {@link #EXPONENTIAL_JITTER} 는 이
     * 값을 <b>상한 삼아</b> 그 안에서 무작위로 뽑는다. 같은 창을 두 방식이 다르게 쓰는 것이므로
     * 계산은 한 곳에 둔다.
     *
     * <p>기본값(base 10ms, max 200ms)에서: 10 → 20 → 40 → 80 → 160 → 200(고정).
     */
    static long windowMillis(int attemptNo, long baseMillis, long maxMillis) {
        long window = baseMillis;
        // 첫 시도(attemptNo=1)는 아직 두 배가 된 적이 없으므로 base 그대로다.
        for (int doubled = 1; doubled < attemptNo; doubled++) {
            window *= 2;
            if (window >= maxMillis) {
                // 상한에 닿았으면 더 곱할 이유가 없다(오버플로 방지도 겸한다).
                return maxMillis;
            }
        }
        return Math.min(window, maxMillis);
    }
}
