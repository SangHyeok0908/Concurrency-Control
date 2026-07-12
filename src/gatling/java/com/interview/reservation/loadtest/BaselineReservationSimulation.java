package com.interview.reservation.loadtest;

import static io.gatling.javaapi.core.CoreDsl.StringBody;
import static io.gatling.javaapi.core.CoreDsl.atOnceUsers;
import static io.gatling.javaapi.core.CoreDsl.exec;
import static io.gatling.javaapi.core.CoreDsl.jsonPath;
import static io.gatling.javaapi.core.CoreDsl.scenario;
import static io.gatling.javaapi.http.HttpDsl.http;
import static io.gatling.javaapi.http.HttpDsl.status;

import io.gatling.javaapi.core.ScenarioBuilder;
import io.gatling.javaapi.core.Simulation;
import io.gatling.javaapi.http.HttpProtocolBuilder;
import java.time.LocalDateTime;

/**
 * 1단계 baseline HTTP 부하 테스트.
 *
 * <p><b>목적.</b> 락 없는 {@code POST /api/reservations}가 실제 HTTP 부하에서 ① 정원을 넘겨
 * 예약을 확정한다(오버부킹)는 증거를 만들고 ② TPS·응답시간·실패율의 baseline 수치를 남긴다.
 * 서비스-계층 프로브({@code BaselineOverbookingProbeTest})가 못 하는 HTTP 계층 측정을 보강한다 —
 * 네트워크·직렬화 지연이 경쟁 창을 넓혀 오버부킹이 더 잘 드러난다.
 *
 * <p><b>구도.</b> 정원 {@code capacity}짜리 슬롯 하나에 서로 다른 지원자 {@code contenders}명이
 * {@code atOnceUsers}로 동시에 예약을 시도한다. 시드(슬롯 1개 + 지원자 N명 생성)를 먼저 돌리고,
 * {@code andThen}으로 그 뒤에 경쟁 버스트를 발사한다.
 *
 * <p><b>제약(CLAUDE.md).</b> 이 시뮬레이션은 baseline을 측정만 한다. {@code ReservationService}는
 * 의도적으로 락이 없으며 여기서 고치지 않는다. 오버부킹은 리포트 통과/실패가 아니라 DB 확정 행 수로
 * 증명하므로 assertion을 걸지 않는다(프로브 테스트와 같은 철학).
 *
 * <p>파라미터는 시스템 프로퍼티로 오버라이드한다. 예:
 * {@code ./gradlew gatlingRun -Dcapacity=1 -Dcontenders=200 -DbaseUrl=http://localhost:8080}
 */
public class BaselineReservationSimulation extends Simulation {

    private static final String BASE_URL = System.getProperty("baseUrl", "http://localhost:8080");
    private static final int CAPACITY = Integer.getInteger("capacity", 100);
    private static final int CONTENDERS = Integer.getInteger("contenders", 500);

    /** 반복 실행 시 지원자 이메일 UNIQUE 제약 충돌을 피하려고 런마다 고유한 프리픽스를 쓴다. */
    private static final String RUN_ID = Long.toHexString(System.nanoTime());

    /** 슬롯 시각은 {@code @Future}여야 하므로 현재로부터 미래로 잡는다. */
    private static final String SLOT_BODY = String.format(
            "{\"startAt\":\"%s\",\"endAt\":\"%s\",\"capacity\":%d}",
            LocalDateTime.now().plusDays(1),
            LocalDateTime.now().plusDays(1).plusMinutes(30),
            CAPACITY);

    private final HttpProtocolBuilder httpProtocol = http
            .baseUrl(BASE_URL)
            .contentTypeHeader("application/json")
            .acceptHeader("application/json");

    /** 슬롯 1개 + 지원자 CONTENDERS명을 만들고, id를 공유 홀더에 적재한다. */
    private final ScenarioBuilder seed = scenario("seed")
            .exec(http("create slot")
                    .post("/api/slots")
                    .body(StringBody(SLOT_BODY))
                    .check(status().is(201))
                    .check(jsonPath("$.id").ofLong().saveAs("slotId")))
            .exec(session -> {
                SeedState.SLOT_ID.set(session.getLong("slotId"));
                return session;
            })
            .repeat(CONTENDERS, "i").on(
                    exec(http("create applicant")
                            .post("/api/applicants")
                            .body(StringBody(session -> String.format(
                                    "{\"name\":\"loadtest-%d\",\"email\":\"loadtest-%s-%d@example.com\"}",
                                    session.getInt("i"), RUN_ID, session.getInt("i"))))
                            .check(status().is(201))
                            .check(jsonPath("$.id").ofLong().saveAs("applicantId")))
                            .exec(session -> {
                                SeedState.APPLICANT_IDS.add(session.getLong("applicantId"));
                                return session;
                            }));

    /** 서로 다른 지원자가 같은 슬롯 하나를 두고 동시에 예약을 시도한다. */
    private final ScenarioBuilder contention = scenario("reservation contention")
            .feed(SeedState.applicantFeeder())
            .exec(http("reserve")
                    .post("/api/reservations")
                    .body(StringBody(session -> String.format(
                            "{\"applicantId\":%d,\"slotId\":%d}",
                            session.getLong("applicantId"), SeedState.SLOT_ID.get())))
                    // 409(정원 마감)는 baseline의 '정상 거절'이라 실패로 세지 않는다. 그래야
                    // KO 카운트가 진짜 실패(500 데드락, 타임아웃)만 남아 실패율이 의미를 갖는다.
                    .check(status().in(201, 409)));

    {
        setUp(
                seed.injectOpen(atOnceUsers(1))
                        .andThen(contention.injectOpen(atOnceUsers(CONTENDERS))))
                .protocols(httpProtocol);
    }
}
