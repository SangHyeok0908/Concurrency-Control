package com.interview.reservation.web;

import com.interview.reservation.service.strategy.OptimisticRetryStats;
import java.util.Map;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * ⑤ 낙관적 락의 <b>재시도 횟수 분포</b>를 꺼내 보는 창구.
 *
 * <p>Gatling 은 애플리케이션 바깥 프로세스라 리포트에 재시도 횟수가 남지 않는다 — 응답시간
 * 평균에 뭉개져 보일 뿐이다. 부하를 건 뒤 이 엔드포인트를 읽어야 "경합이 올라가면 재시도가
 * 몇 배로 뛰는가"를 수치로 남길 수 있다(브랜치 전략 ⑦이 요구하는 지표).
 *
 * <p>실험 도구이지 제품 API 가 아니다. 실제 서비스라면 이건 Micrometer 히스토그램으로 내보내
 * 모니터링에 붙일 자리다 — 여기서는 의존성을 늘리지 않으려고 최소 구현만 뒀다.
 */
@RestController
@RequestMapping("/api/metrics/optimistic-retries")
@RequiredArgsConstructor
public class OptimisticRetryMetricsController {

    private final OptimisticRetryStats retryStats;

    @GetMapping
    public Map<String, Object> snapshot() {
        return retryStats.snapshot();
    }

    /** 부하 실행 하나의 분포만 보려고 실행 직전에 비운다. */
    @DeleteMapping
    public ResponseEntity<Void> reset() {
        retryStats.reset();
        return ResponseEntity.noContent().build();
    }
}
