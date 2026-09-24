package com.interview.reservation.config;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.ConfigDataApplicationContextInitializer;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.core.env.Environment;

class BenchmarkProfileConfigurationTest {

    private final ApplicationContextRunner contextRunner = new ApplicationContextRunner()
            .withInitializer(new ConfigDataApplicationContextInitializer())
            .withPropertyValues("spring.profiles.active=benchmark");

    @Test
    @DisplayName("benchmark 프로필은 풀 크기를 고정하고 SQL 계측 로그를 끈다")
    void fixesConnectionPoolAndDisablesSqlInstrumentation() {
        contextRunner.run(context -> {
            Environment environment = context.getEnvironment();

            assertThat(environment.getProperty(
                    "spring.datasource.hikari.maximum-pool-size", Integer.class))
                    .isEqualTo(100);
            assertThat(environment.getProperty(
                    "spring.datasource.hikari.minimum-idle", Integer.class))
                    .isEqualTo(100);
            assertThat(environment.getProperty(
                    "spring.jpa.properties.hibernate.show_sql", Boolean.class))
                    .isFalse();
            assertThat(environment.getProperty(
                    "spring.jpa.properties.hibernate.format_sql", Boolean.class))
                    .isFalse();
            assertThat(environment.getProperty(
                    "spring.jpa.properties.hibernate.use_sql_comments", Boolean.class))
                    .isFalse();
            assertThat(environment.getProperty("logging.level.org.hibernate.SQL"))
                    .isEqualTo("OFF");
            assertThat(environment.getProperty("logging.level.org.hibernate.orm.jdbc.bind"))
                    .isEqualTo("OFF");
            assertThat(environment.getProperty("logging.level.root"))
                    .isEqualTo("OFF");
        });
    }
}
