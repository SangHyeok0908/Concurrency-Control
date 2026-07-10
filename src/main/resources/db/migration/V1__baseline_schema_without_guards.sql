-- 1단계: 방어 수단이 없는 기준(baseline) 스키마.
--
-- 이 마이그레이션에는 동시성 방어 장치가 의도적으로 빠져 있다. 정원 초과와 중복
-- 예약을 부하 테스트로 재현하는 것이 1단계의 산출물이기 때문이다.
--
--   * reservation 에 UNIQUE(applicant_id, slot_id) 가 없다  -> 2-1단계(V2)에서 추가
--   * interview_slot 에 version 컬럼이 없다                 -> 2-2단계에서 추가
--   * idempotency_key 테이블이 없다                          -> 2-1단계에서 추가
--
-- 설계 근거와 단계별 스키마 변화는 docs/ERD.md 참고.
--
-- FK 는 남겨둔다. 참조 무결성 제약이지 동시성 방어 수단이 아니며, 없으면 실험
-- 데이터가 오염된다(존재하지 않는 슬롯을 가리키는 예약 등).

CREATE TABLE applicant (
    id         BIGINT       NOT NULL AUTO_INCREMENT,
    name       VARCHAR(50)  NOT NULL,
    email      VARCHAR(255) NOT NULL,
    created_at DATETIME(6)  NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uk_applicant_email UNIQUE (email)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

-- remaining 이 이 실험의 진앙지다. 1단계 구현은 이 값을 읽고, 애플리케이션
-- 메모리에서 검사하고, 다시 쓴다(check-then-act). 정원 초과는 여기서 발생한다.
--
-- capacity 를 따로 두는 이유: 부하 테스트 후 capacity 와 실제 reservation 행 수를
-- 비교해야 정원이 몇 명 초과됐는지 정량적으로 증명할 수 있다. remaining 만으로는
-- 음수인지 여부밖에 알 수 없다.
CREATE TABLE interview_slot (
    id         BIGINT      NOT NULL AUTO_INCREMENT,
    start_at   DATETIME(6) NOT NULL,
    end_at     DATETIME(6) NOT NULL,
    capacity   INT         NOT NULL,
    remaining  INT         NOT NULL,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

-- remaining 을 UNSIGNED 로 선언하지 않는다. 음수가 되는 것을 DB가 막아버리면
-- 오버부킹이 예외로 바뀌어, 1단계가 재현하려는 "조용히 깨진 데이터"를 관찰할 수 없다.

CREATE TABLE reservation (
    id           BIGINT      NOT NULL AUTO_INCREMENT,
    applicant_id BIGINT      NOT NULL,
    slot_id      BIGINT      NOT NULL,
    status       VARCHAR(20) NOT NULL,
    created_at   DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    KEY idx_reservation_slot (slot_id),
    CONSTRAINT fk_reservation_applicant FOREIGN KEY (applicant_id) REFERENCES applicant (id),
    CONSTRAINT fk_reservation_slot FOREIGN KEY (slot_id) REFERENCES interview_slot (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;
