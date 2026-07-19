package com.interview.reservation.support;

import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.ActiveProfiles;
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
 *
 * <p><b>{@code test} 프로파일을 여기서 켠다.</b> 동시성 테스트들이 공유하는 설정(커넥션 풀
 * 20, SQL 로그 끄기)은 클래스마다 {@code @TestPropertySource} 로 반복하던 것을
 * {@code application.yml} 의 {@code test} 프로파일로 옮겼다. 하위 클래스는 이 프로파일을
 * 상속받으므로 아무것도 적지 않아도 된다.
 *
 * <p>덤으로 <b>스프링 컨텍스트가 재사용된다.</b> 테스트 컨텍스트 캐시의 키에는
 * {@code @TestPropertySource} 의 프로퍼티가 들어가므로, 클래스마다 같은 내용을 따로 선언하면
 * 내용이 같아도 선언 위치가 달라 캐시 적중에 불리했다. 공통 설정을 프로파일 하나로 모으면
 * 그 축이 사라져 클래스들이 같은 컨텍스트를 공유한다 — 개별 설정이 <i>진짜로</i> 필요한
 * 클래스만 자기 컨텍스트를 갖는다(예: 재시도 상한을 바꾸는 {@code OptimisticLockRetryLimitTest}).
 */
@ActiveProfiles("test")
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
