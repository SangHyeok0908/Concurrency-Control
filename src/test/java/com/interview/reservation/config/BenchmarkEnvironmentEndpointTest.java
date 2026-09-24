package com.interview.reservation.config;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;
import static org.awaitility.Awaitility.await;

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

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles(profiles = {"test", "benchmark"}, inheritProfiles = false)
class BenchmarkEnvironmentEndpointTest extends AbstractIntegrationTest {

    @Autowired MockMvc mockMvc;
    @Autowired HikariDataSource dataSource;

    @Test
    @DisplayName("benchmark 프로필은 실제 측정 설정을 검증할 환경 엔드포인트를 제공한다")
    void exposesEffectiveBenchmarkEnvironment() throws Exception {
        // minimumIdle 채움은 housekeeper가 비동기로 수행한다. 스크립트도 같은 이유로 준비될 때까지
        // 폴링하므로, 테스트 역시 시작 직후의 1개 연결을 최종 상태로 오인하지 않는다.
        await().atMost(Duration.ofSeconds(10)).until(() ->
                dataSource.getHikariPoolMXBean() != null
                        && dataSource.getHikariPoolMXBean().getTotalConnections() == 100);

        mockMvc.perform(get("/api/metrics/benchmark-environment"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.activeProfiles[0]").value("test"))
                .andExpect(jsonPath("$.activeProfiles[1]").value("benchmark"))
                .andExpect(jsonPath("$.maximumPoolSize").value(100))
                .andExpect(jsonPath("$.minimumIdle").value(100))
                .andExpect(jsonPath("$.totalConnections").value(100))
                .andExpect(jsonPath("$.showSql").value(false))
                .andExpect(jsonPath("$.formatSql").value(false))
                .andExpect(jsonPath("$.useSqlComments").value(false))
                .andExpect(jsonPath("$.sqlLogLevel").value("OFF"))
                .andExpect(jsonPath("$.bindLogLevel").value("OFF"))
                .andExpect(jsonPath("$.rootLogLevel").value("OFF"))
                .andExpect(jsonPath("$.optimisticMaxAttempts").value(5))
                .andExpect(jsonPath("$.backoffBaseMillis").value(10))
                .andExpect(jsonPath("$.backoffMaxMillis").value(200))
                .andExpect(jsonPath("$.backoffPolicy").value("exponential-jitter"));
    }
}
