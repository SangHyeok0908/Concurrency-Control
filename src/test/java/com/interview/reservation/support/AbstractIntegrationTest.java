package com.interview.reservation.support;

import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.testcontainers.containers.MySQLContainer;

/**
 * 통합 테스트가 공유하는 MySQL 컨테이너. 싱글턴 패턴이다 — static 필드에서 한 번만
 * start() 하고, 테스트 클래스마다 다시 띄우지 않는다(정리는 Testcontainers 의 Ryuk 가 한다).
 *
 * <p>H2 가 아니라 실제 MySQL(InnoDB) 을 쓰는 이유: 이 프로젝트가 관찰하려는 락/스냅숏
 * 동작은 InnoDB 고유의 것이라 인메모리 DB 로는 재현되지 않는다. MySQL 8 의 기본 격리
 * 수준은 REPEATABLE READ 로, 운영에서 쓸 docker-compose 설정과 동일하다.
 *
 * <p><b>연결 배선은 {@link ServiceConnection} 에 맡긴다.</b> 컨테이너를 스프링 빈으로
 * 등록하면, 프레임워크가 {@code MySQLContainer} 라는 타입만 보고 JDBC URL/user/password 를
 * DataSource 에 연결한다. {@code spring.datasource.*} 프로퍼티 키를 손으로 적을 필요가
 * 없어 오타 위험이 사라지고, Redis 컨테이너를 추가할 때도 어노테이션 한 줄로 끝난다.
 */
@Import(AbstractIntegrationTest.ContainersConfig.class)
public abstract class AbstractIntegrationTest {

    @TestConfiguration(proxyBeanMethods = false)
    static class ContainersConfig {

        static final MySQLContainer<?> MYSQL = new MySQLContainer<>("mysql:8.0");

        static {
            MYSQL.start();
        }

        @Bean
        @ServiceConnection
        MySQLContainer<?> mysqlContainer() {
            return MYSQL;
        }
    }
}
