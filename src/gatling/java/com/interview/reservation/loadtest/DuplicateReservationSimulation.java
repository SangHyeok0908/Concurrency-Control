package com.interview.reservation.loadtest;

import static io.gatling.javaapi.core.CoreDsl.StringBody;
import static io.gatling.javaapi.core.CoreDsl.atOnceUsers;
import static io.gatling.javaapi.core.CoreDsl.details;
import static io.gatling.javaapi.core.CoreDsl.exec;
import static io.gatling.javaapi.core.CoreDsl.jsonPath;
import static io.gatling.javaapi.core.CoreDsl.scenario;
import static io.gatling.javaapi.http.HttpDsl.http;
import static io.gatling.javaapi.http.HttpDsl.status;

import io.gatling.javaapi.core.ScenarioBuilder;
import io.gatling.javaapi.core.Session;
import io.gatling.javaapi.core.Simulation;
import io.gatling.javaapi.http.HttpProtocolBuilder;
import java.time.LocalDateTime;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 같은 지원자가 같은 슬롯을 동시에 재요청하는 HTTP 중복 검증 workload.
 *
 * <p>{@link BaselineReservationSimulation}은 서로 다른 지원자의 정원 경쟁을 재현하는 역사적
 * 측정 자산이다. 이 simulation은 그 구도를 바꾸지 않고, 별도 클래스에서 지원자 한 명과 슬롯
 * 하나만 시드한 뒤 모든 가상 사용자가 동일한 요청 body를 전송한다.
 *
 * <p>201과 409만 Gatling OK로 인정한다. 다른 상태와 응답 자체가 없는 요청은 숨기지 않고 종료
 * sentinel에 따로 집계한다. Gatling의 check 판정도 별도 sentinel로 세어, 실행 스크립트가 HTTP
 * 버킷·Gatling OK/KO·DB 불변식을 서로 대조한다.
 */
public class DuplicateReservationSimulation extends Simulation {

    private static final String BASE_URL = System.getProperty("baseUrl", "http://localhost:8080");
    private static final String STRATEGY = System.getProperty("strategy", "unique");
    private static final int CAPACITY = Integer.getInteger("capacity", 200);
    private static final int REQUESTS = Integer.getInteger("requests", 200);
    private static final String RESERVE_PATH = "/api/reservations/" + STRATEGY;
    private static final String RESERVE_LABEL = String.format(
            "duplicate reserve [%s cap=%d req=%d]", STRATEGY, CAPACITY, REQUESTS);
    private static final String RUN_ID = Long.toHexString(System.nanoTime());

    private static final AtomicLong SLOT_ID = new AtomicLong(-1);
    private static final AtomicLong APPLICANT_ID = new AtomicLong(-1);
    private static final AtomicInteger HTTP_201 = new AtomicInteger();
    private static final AtomicInteger HTTP_409 = new AtomicInteger();
    private static final AtomicInteger HTTP_500 = new AtomicInteger();
    private static final AtomicInteger HTTP_503 = new AtomicInteger();
    private static final AtomicInteger HTTP_OTHER = new AtomicInteger();
    private static final AtomicInteger NO_RESPONSE = new AtomicInteger();
    private static final AtomicInteger GATLING_OK = new AtomicInteger();
    private static final AtomicInteger GATLING_KO = new AtomicInteger();

    private static final String SLOT_BODY = String.format(
            "{\"startAt\":\"%s\",\"endAt\":\"%s\",\"capacity\":%d}",
            LocalDateTime.now().plusDays(1),
            LocalDateTime.now().plusDays(1).plusMinutes(30),
            CAPACITY);
    private static final String APPLICANT_BODY = String.format(
            "{\"name\":\"duplicate-loadtest\","
                    + "\"email\":\"duplicate-loadtest-%s@example.com\"}",
            RUN_ID);

    private final HttpProtocolBuilder httpProtocol = http
            .baseUrl(BASE_URL)
            .contentTypeHeader("application/json")
            .acceptHeader("application/json");

    private final ScenarioBuilder seed = scenario("duplicate seed")
            .exec(http("create duplicate slot")
                    .post("/api/slots")
                    .body(StringBody(SLOT_BODY))
                    .check(status().is(201))
                    .check(jsonPath("$.id").ofLong().saveAs("slotId")))
            .exec(session -> {
                SLOT_ID.set(session.getLong("slotId"));
                return session;
            })
            .exec(http("create duplicate applicant")
                    .post("/api/applicants")
                    .body(StringBody(APPLICANT_BODY))
                    .check(status().is(201))
                    .check(jsonPath("$.id").ofLong().saveAs("applicantId")))
            .exec(session -> {
                APPLICANT_ID.set(session.getLong("applicantId"));
                return session;
            });

    private final ScenarioBuilder duplicateRequests = scenario("duplicate reservation requests")
            .exec(http(RESERVE_LABEL)
                    .post(RESERVE_PATH)
                    .body(StringBody(session -> String.format(
                            "{\"applicantId\":%d,\"slotId\":%d}",
                            APPLICANT_ID.get(), SLOT_ID.get())))
                    // 저장 check를 먼저 둬 500/503도 실제 상태별로 센다. 두 번째 check만 OK/KO를
                    // 결정하므로 201·409는 OK, 그 밖의 응답은 KO다.
                    .check(status().saveAs("responseStatus"))
                    .check(status().in(201, 409)))
            .exec(DuplicateReservationSimulation::recordStatus);

    {
        setUp(seed.injectOpen(atOnceUsers(1))
                        .andThen(duplicateRequests.injectOpen(atOnceUsers(REQUESTS))))
                .protocols(httpProtocol)
                .assertions(details(RESERVE_LABEL).allRequests().count().is((long) REQUESTS));
    }

    @Override
    public void before() {
        SLOT_ID.set(-1);
        APPLICANT_ID.set(-1);
        HTTP_201.set(0);
        HTTP_409.set(0);
        HTTP_500.set(0);
        HTTP_503.set(0);
        HTTP_OTHER.set(0);
        NO_RESPONSE.set(0);
        GATLING_OK.set(0);
        GATLING_KO.set(0);
    }

    @Override
    public void after() {
        System.out.printf(
                "DUPLICATE_HTTP_STATUS_COUNTS="
                        + "{\"201\":%d,\"409\":%d,\"500\":%d,\"503\":%d,"
                        + "\"other\":%d,\"no_response\":%d}%n",
                HTTP_201.get(),
                HTTP_409.get(),
                HTTP_500.get(),
                HTTP_503.get(),
                HTTP_OTHER.get(),
                NO_RESPONSE.get());
        System.out.printf(
                "DUPLICATE_GATLING_COUNTS={\"ok\":%d,\"ko\":%d}%n",
                GATLING_OK.get(),
                GATLING_KO.get());
    }

    private static Session recordStatus(Session session) {
        if (session.isFailed()) {
            GATLING_KO.incrementAndGet();
        } else {
            GATLING_OK.incrementAndGet();
        }

        if (!session.contains("responseStatus")) {
            NO_RESPONSE.incrementAndGet();
            return session;
        }

        switch (session.getInt("responseStatus")) {
            case 201 -> HTTP_201.incrementAndGet();
            case 409 -> HTTP_409.incrementAndGet();
            case 500 -> HTTP_500.incrementAndGet();
            case 503 -> HTTP_503.incrementAndGet();
            default -> HTTP_OTHER.incrementAndGet();
        }
        return session.remove("responseStatus");
    }
}
