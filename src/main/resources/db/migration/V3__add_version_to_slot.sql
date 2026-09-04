-- 2-2단계 ⑤: 낙관적 락(@Version)이 쓸 버전 컬럼.
--
-- 낙관적 락은 행을 잠그지 않는다. 대신 "내가 읽은 뒤로 이 행이 바뀌었는가"를 UPDATE 시점에
-- 검사한다 — Hibernate 가 UPDATE ... SET remaining=?, version=? WHERE id=? AND version=?
-- 를 날리고, 갱신 행이 0이면 그 사이 누군가 먼저 커밋했다는 뜻이므로 예외를 던진다.
-- 이 컬럼이 그 "읽은 시점" 표식이다.
--
-- DEFAULT 0: 기존 행에도 값이 필요하고, INSERT 경로(InterviewSlotService)는 버전을 모르는
-- 매핑(InterviewSlot)을 쓰므로 DB 가 초기값을 채워줘야 한다.
--
-- ⚠️ 이 컬럼을 추가해도 baseline·unique·conditional·pessimistic 경로는 그대로다.
--    스키마에 컬럼이 생기는 것과 엔티티가 그걸 @Version 으로 해석하는 것은 별개이기 때문이다.
--    @Version 은 전략이 아니라 *엔티티*에 걸리는 방어라, 기존 InterviewSlot 에 달면
--    dirty check UPDATE 를 쓰는 baseline·unique 의 오버부킹이 사라져 1단계 재현 자산이
--    파괴된다(설계 원칙 2 위반). 그래서 @Version 은 같은 테이블을 가리키는 별도 엔티티
--    VersionedInterviewSlot 에만 달고, ⑤ 전략만 그 매핑을 쓴다.
--    근거: docs/STEP2-OPTIMISTIC-LOCK.md 2절.

ALTER TABLE interview_slot
    ADD COLUMN version BIGINT NOT NULL DEFAULT 0;
