package com.interview.reservation.web;

import com.zaxxer.hikari.HikariDataSource;
import java.util.Arrays;
import java.util.List;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Profile;
import org.springframework.core.env.Environment;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * HTTP 벤치마크가 <b>통제된 애플리케이션 설정</b> 위에서 실행되는지 확인하는 창구.
 *
 * <p>{@code benchmark} 프로필에서만 빈이 등록된다. 측정 스크립트는 부하를 넣기 전에 이 응답을
 * 읽어 Hikari 풀·SQL 로깅·낙관적 락 상한이 기대값과 일치하는지 검증한다. 따라서 기본 프로필로
 * 잘못 띄운 앱이나 Phase B 상한을 적용하지 않은 앱의 결과가 원시 CSV에 섞이지 않는다.
 */
@RestController
@Profile("benchmark")
@RequestMapping("/api/metrics/benchmark-environment")
public class BenchmarkEnvironmentController {

    private final HikariDataSource dataSource;
    private final Environment environment;
    private final int optimisticMaxAttempts;
    private final long backoffBaseMillis;
    private final long backoffMaxMillis;
    private final String backoffPolicy;

    public BenchmarkEnvironmentController(
            HikariDataSource dataSource,
            Environment environment,
            @Value("${reservation.optimistic.max-attempts:5}") int optimisticMaxAttempts,
            @Value("${reservation.optimistic.backoff-base-millis:10}") long backoffBaseMillis,
            @Value("${reservation.optimistic.backoff-max-millis:200}") long backoffMaxMillis,
            @Value("${reservation.optimistic.backoff-policy:exponential-jitter}") String backoffPolicy) {
        this.dataSource = dataSource;
        this.environment = environment;
        this.optimisticMaxAttempts = optimisticMaxAttempts;
        this.backoffBaseMillis = backoffBaseMillis;
        this.backoffMaxMillis = backoffMaxMillis;
        this.backoffPolicy = backoffPolicy;
    }

    @GetMapping
    public BenchmarkEnvironmentResponse snapshot() {
        int totalConnections = dataSource.getHikariPoolMXBean() == null
                ? 0
                : dataSource.getHikariPoolMXBean().getTotalConnections();

        return new BenchmarkEnvironmentResponse(
                Arrays.asList(environment.getActiveProfiles()),
                dataSource.getMaximumPoolSize(),
                dataSource.getMinimumIdle(),
                totalConnections,
                property("spring.jpa.properties.hibernate.show_sql", Boolean.class, false),
                property("spring.jpa.properties.hibernate.format_sql", Boolean.class, false),
                property("spring.jpa.properties.hibernate.use_sql_comments", Boolean.class, false),
                environment.getProperty("logging.level.org.hibernate.SQL", "UNSET"),
                environment.getProperty("logging.level.org.hibernate.orm.jdbc.bind", "UNSET"),
                environment.getProperty("logging.level.root", "UNSET"),
                optimisticMaxAttempts,
                backoffBaseMillis,
                backoffMaxMillis,
                backoffPolicy);
    }

    private <T> T property(String key, Class<T> type, T defaultValue) {
        return environment.getProperty(key, type, defaultValue);
    }

    public record BenchmarkEnvironmentResponse(
            List<String> activeProfiles,
            int maximumPoolSize,
            int minimumIdle,
            int totalConnections,
            boolean showSql,
            boolean formatSql,
            boolean useSqlComments,
            String sqlLogLevel,
            String bindLogLevel,
            String rootLogLevel,
            int optimisticMaxAttempts,
            long backoffBaseMillis,
            long backoffMaxMillis,
            String backoffPolicy) {
    }
}
