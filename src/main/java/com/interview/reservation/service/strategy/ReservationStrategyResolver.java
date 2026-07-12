package com.interview.reservation.service.strategy;

import com.interview.reservation.exception.NotFoundException;
import java.util.List;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;
import org.springframework.stereotype.Component;

/**
 * 요청 경로의 {@code {strategy}} 키로 알맞은 {@link ReservationStrategy} 구현체를 찾아준다.
 * 스프링이 주입한 모든 전략 빈을 {@link ReservationStrategy#key()} 기준으로 색인한다 —
 * 새 방어 전략을 추가하면 빈 등록만으로 라우팅에 자동 편입된다.
 */
@Component
public class ReservationStrategyResolver {

    private final Map<String, ReservationStrategy> byKey;

    public ReservationStrategyResolver(List<ReservationStrategy> strategies) {
        this.byKey = strategies.stream()
                .collect(Collectors.toMap(ReservationStrategy::key, Function.identity()));
    }

    public ReservationStrategy resolve(String key) {
        ReservationStrategy strategy = byKey.get(key);
        if (strategy == null) {
            throw new NotFoundException(
                    "알 수 없는 예약 전략입니다: " + key + " (사용 가능: " + byKey.keySet() + ")");
        }
        return strategy;
    }
}
