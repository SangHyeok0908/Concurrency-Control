package com.interview.reservation.service.strategy;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicLongArray;
import org.springframework.stereotype.Component;

/**
 * 낙관적 락이 요청 하나를 처리하는 데 <b>시도를 몇 번 썼는지</b>의 분포를 센다.
 *
 * <p><b>왜 이게 필요한가.</b> 다른 전략은 요청 1건 = 시도 1건이라 응답시간만 보면 비용이
 * 다 드러난다. 낙관적 락만 요청 1건이 시도 N건으로 부풀 수 있고, <b>그 N은 응답시간
 * 평균에 뭉개져 보이지 않는다.</b> 브랜치 전략 ⑦이 "낙관적 락은 재시도 횟수 분포도 함께
 * 남긴다"고 못박은 이유이며, 경합 수준에 따른 재시도 폭주를 보여주는 유일한 직접 지표다.
 *
 * <p>Gatling 부하는 애플리케이션 바깥에서 돌기 때문에 부하 후 이 분포를 꺼내 볼 창구가
 * 필요하다 — {@code GET /api/metrics/optimistic-retries} 가 그 창구다.
 *
 * <p>프로세스 메모리에만 쌓는 단순 카운터다. 이 저장소는 실험 1회 = 앱 1회 실행이라
 * 영속화할 이유가 없고, 정확한 원자성보다 <b>측정이 부하를 왜곡하지 않는 것</b>이 중요해
 * 락 없는 {@code AtomicLong} 만 쓴다.
 */
@Component
public class OptimisticRetryStats {

    /** 이 값을 넘는 시도 횟수는 마지막 칸에 함께 담는다(상한이 이보다 크게 설정될 경우 대비). */
    private static final int MAX_TRACKED_ATTEMPTS = 16;

    private final AtomicLongArray succeededByAttempts = new AtomicLongArray(MAX_TRACKED_ATTEMPTS + 1);
    private final AtomicLong exhausted = new AtomicLong();
    private final AtomicLong versionConflicts = new AtomicLong();
    private final AtomicLong deadlocks = new AtomicLong();

    /** 시도 {@code attempts} 번 만에 성공한 요청 1건을 기록한다. */
    void recordSuccess(int attempts) {
        succeededByAttempts.incrementAndGet(Math.min(attempts, MAX_TRACKED_ATTEMPTS));
    }

    /** 재시도 상한을 소진하고 실패한 요청 1건을 기록한다. */
    void recordExhausted() {
        exhausted.incrementAndGet();
    }

    /** 버전 충돌로 시도 1건이 버려졌음을 기록한다 — 낙관적 락이 <b>의도한</b> 실패 모드다. */
    void recordVersionConflict() {
        versionConflicts.incrementAndGet();
    }

    /**
     * 데드락으로 시도 1건이 버려졌음을 기록한다. <b>0이 아니면 조사 대상</b>이다 — 정상
     * 동작이 아니라 락 획득 순서가 어긋났다는 신호이기 때문이다
     * ({@code OptimisticReservationAttempt} 의 flush 주석 참고).
     */
    void recordDeadlock() {
        deadlocks.incrementAndGet();
    }

    /**
     * 시도 횟수 → 성공 건수 분포와 소진 건수를 함께 돌려준다.
     * 만석 거절은 세지 않는다 — 그건 재시도 없이 1회에 끝나는 정상 경로다.
     */
    public Map<String, Object> snapshot() {
        Map<String, Long> distribution = new LinkedHashMap<>();
        long totalSucceeded = 0;
        long totalAttempts = 0;
        for (int attempts = 1; attempts <= MAX_TRACKED_ATTEMPTS; attempts++) {
            long count = succeededByAttempts.get(attempts);
            if (count > 0) {
                distribution.put(String.valueOf(attempts), count);
                totalSucceeded += count;
                totalAttempts += (long) attempts * count;
            }
        }

        Map<String, Object> snapshot = new LinkedHashMap<>();
        snapshot.put("succeededByAttempts", distribution);
        snapshot.put("succeeded", totalSucceeded);
        snapshot.put("retryExhausted", exhausted.get());
        snapshot.put("versionConflicts", versionConflicts.get());
        // 0 이어야 정상이다. 0 이 아니면 락 획득 순서가 어긋났다는 뜻이다.
        snapshot.put("deadlocks", deadlocks.get());
        snapshot.put("meanAttemptsPerSuccess",
                totalSucceeded == 0 ? 0.0 : Math.round((double) totalAttempts / totalSucceeded * 100) / 100.0);
        return snapshot;
    }

    /** 실험 사이에 카운터를 비운다(부하 실행 하나의 분포만 보기 위해). */
    public void reset() {
        for (int i = 0; i <= MAX_TRACKED_ATTEMPTS; i++) {
            succeededByAttempts.set(i, 0);
        }
        exhausted.set(0);
        versionConflicts.set(0);
        deadlocks.set(0);
    }
}
