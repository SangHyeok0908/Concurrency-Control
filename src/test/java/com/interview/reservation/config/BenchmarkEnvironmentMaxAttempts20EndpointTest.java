package com.interview.reservation.config;

import static org.awaitility.Awaitility.await;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.interview.reservation.support.AbstractIntegrationTest;
import com.zaxxer.hikari.HikariDataSource;
import java.time.Duration;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest(properties = "reservation.optimistic.max-attempts=20")
@AutoConfigureMockMvc
@ActiveProfiles(profiles = {"test", "benchmark"}, inheritProfiles = false)
class BenchmarkEnvironmentMaxAttempts20EndpointTest extends AbstractIntegrationTest {

    @Autowired MockMvc mockMvc;
    @Autowired HikariDataSource dataSource;

    @Test
    @DisplayName("benchmark 환경 엔드포인트는 재시도 상한 20 override를 공개한다")
    void exposesOverriddenOptimisticMaxAttempts() throws Exception {
        await().atMost(Duration.ofSeconds(10)).until(() ->
                dataSource.getHikariPoolMXBean() != null
                        && dataSource.getHikariPoolMXBean().getTotalConnections() == 100);

        mockMvc.perform(get("/api/metrics/benchmark-environment"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.optimisticMaxAttempts").value(20));
    }
}
