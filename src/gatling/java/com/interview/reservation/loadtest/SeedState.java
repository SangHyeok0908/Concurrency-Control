package com.interview.reservation.loadtest;

import java.util.Collections;
import java.util.Iterator;
import java.util.Map;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 시드 시나리오가 만든 식별자를 부하 시나리오로 넘기는, 프로세스 내 얇은 홀더.
 *
 * <p>Gatling은 단일 JVM에서 돌고 부하 단계는 {@code andThen}으로 시드 뒤에 실행되므로,
 * 부하 시나리오가 이 값들을 읽는 시점엔 시드가 이미 채워 둔 상태다. 그래서 Gatling
 * 세션(시나리오 격리) 밖에서 슬롯 id 하나와 지원자 id 큐를 공유해도 안전하다.
 */
final class SeedState {

    /** 시드가 만든 슬롯 id. 모든 부하 요청이 이 한 슬롯을 두고 경쟁한다. */
    static final AtomicLong SLOT_ID = new AtomicLong(-1);

    /** 시드가 만든 지원자 id들. 부하 피더가 요청마다 하나씩 poll 한다. */
    static final ConcurrentLinkedQueue<Long> APPLICANT_IDS = new ConcurrentLinkedQueue<>();

    private SeedState() {
    }

    /**
     * 큐에서 지원자 id를 lazy하게 꺼내는 Gatling 피더. 부하 사용자 수 == 시드된 지원자 수라
     * 서로 다른 지원자가 정확히 하나씩 배정된다 — 여기서 보는 것은 중복 요청(멱등성)이 아니라
     * 순수한 정원 경쟁이다(서비스-계층 프로브와 같은 구도).
     */
    static Iterator<Map<String, Object>> applicantFeeder() {
        return new Iterator<>() {
            @Override
            public boolean hasNext() {
                return !APPLICANT_IDS.isEmpty();
            }

            @Override
            public Map<String, Object> next() {
                return Collections.singletonMap("applicantId", APPLICANT_IDS.poll());
            }
        };
    }
}
