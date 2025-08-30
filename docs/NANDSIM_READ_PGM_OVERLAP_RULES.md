# READ · PGM 겹침(overlap) 불변 조건 설계

작성자: Engineering
상태: 제안(권장 규칙 + 선택 규칙 포함)

## 1) 용어/가정
- 커밋(Committed) 상태: `(die, block)`의 마지막 커밋된 프로그램 페이지 인덱스 `state ∈ {-3(BAD), -2(GOOD), -1(ERASE), 0..pagesize-1}` 및 `mode`.
- 예약(Reserved, 미래/Future) 상태: 스케줄러가 특정 시간 구간에 대해 예약한 ERASE/PGM/READ 작업(아직 커밋되지 않음).
- READ 대상 페이지: 오프셋 `offset≥0`을 고려한 `0..(state-offset)` 범위.
- PGM 예약 길이: `size`(연속 페이지), 시작 페이지는 예약 시점의 가상 상태에서 `state+1`.
- 멀티‑플레인 PGM: 동일 다이 내 선택된 모든 플레인 블록에서 상태가 동일하고 같은 길이로 전진.

## 2) 커밋(Committed) 기준 규칙
- READ 허용:
  - `state ≥ offset`인 블록에서 페이지 `p ∈ [0, state-offset]`만 읽기 허용.
- PGM 허용:
  - `state < pagesize-1`인 블록에서만 프로그램 가능.
- 멀티‑플레인 PGM 전제:
  - 선택 플레인의 모든 블록이 동일한 `state`와 `mode`를 가져야 함.
- ERASE 커밋:
  - `state := ERASE(-1)`, 이후 첫 PGM은 0 페이지부터.

## 3) 예약(Reserved) 기준 규칙 — 권장(Strict Default)
- 공통 시간 규칙:
  - 동일 `(die, block)`에 대해 시간 구간이 겹치는 READ/PGM/ERASE 예약은 금지.
- READ vs PGM(동일 블록):
  - 어떤 PGM 예약이든 해당 시간 구간에 READ 예약을 금지(블록 전체 금지).  
    이유: 프로그램 중/대기 중인 블록을 안전하게 보호(간단·보수적).
- READ vs ERASE(동일 블록):
  - ERASE 예약 시간 구간과 READ 예약은 상호 금지.
- PGM vs ERASE(동일 블록):
  - 시간 구간이 겹치면 금지.
- 멀티‑플레인 그룹:
  - 그룹에 속한 블록 중 하나에라도 위 금지 사유가 발생하면 그룹 전체 예약을 금지.

## 4) 예약(Reserved) 기준 규칙 — 선택(Relaxed, Optional)
- READ vs PGM(동일 블록) 완화 규칙(옵션):
  - 시간 구간이 겹치지 않고, READ 대상 페이지가 `p < pgm_start_page`를 만족하면 허용.  
    즉, READ 종료 시간이 PGM 시작 시간보다 앞서고, 읽는 페이지가 예약 PGM 범위 미만이면 허용.  
  - 정책 토글 예: `policy.read_under_pending_pgm = true/false` (기본 false=엄격 금지)

## 5) 커밋/예약 혼합 시나리오(불변 조건)
- READ 먼저 예약 → PGM 나중 예약(동일 블록):
  - PGM 시작 시간이 READ 종료 시간 이후여야 함.
  - PGM 시작 페이지는 예약 시점 가상 상태 기준 `state_virtual+1` 이상.
- PGM 먼저 예약 → READ 나중 예약(동일 블록):
  - 권장(Strict): 동일 시간 구간의 READ 금지(블록 전체).  
  - 선택(Relaxed): READ가 PGM 시작 이전에 끝나고, `p < pgm_start_page`이면 허용.
- ERASE 예약이 존재하는 블록:
  - 해당 시간 구간의 READ/PGM 예약 모두 금지.

## 6) 멀티‑플레인 + 다이 의미론
- 그룹은 단일 다이 내에서만 구성(다이 간 교차 금지).
- 그룹 내 모든 블록은 동일한 시간 구간 제약을 공유(한 블록의 충돌은 그룹 전체 충돌).
- READ 멀티‑플레인 예약은 각 블록의 READ 범위/오프셋을 모두 만족해야 함(최소 읽기 가능 페이지 수 기준).

## 7) Validator 체크리스트(구현 관점)
- 입력: `op(READ|PGM), addrs(#,k,3), time_window, mode, size, sequential, offset`
- 공통:
  - (die,block)별 겹치는 예약 존재 여부 검사(READ/PGM/ERASE)
  - 모드 일치 여부 확인(특히 PGM)
- READ:
  - 각 (die,block)에 대해 `state_virtual ≥ offset` 확인
  - Strict: 해당 시간 구간에 PGM/ERASE 예약 존재 시 블록 전체 제외
  - Relaxed: (옵션) `end(READ) ≤ start(PGM)` 이고 `page < pgm_start_page`인 페이지만 허용
- PGM:
  - `state_virtual + size ≤ pagesize-1` 확인
  - 멀티‑플레인: 모든 블록의 `state_virtual` 동일성 확인

## 8) 예시 시나리오
- 예시 1(Strict, 금지):  
  - `(die0, blk10)`에 `t∈[100,200]` PGM 예약, READ `t∈[150,170]` 요청 → 시간 구간 겹침 → READ 금지
- 예시 2(Relaxed, 허용):  
  - 같은 블록에서 PGM `t∈[200,300]`, READ `t∈[120,150]`, READ 페이지 < `pgm_start_page`, 그리고 `150 ≤ 200` → READ 허용(옵션)
- 예시 3(멀티‑플레인):  
  - 그룹의 한 블록에 READ/PGM 충돌 발생 시 그룹 전체 예약 거부

## 9) 권고안 요약
- 기본은 Strict: 동일 블록에 대해 시간 겹침 동안 READ/PGM 상호 금지(단순·안전).  
- 필요 시만 Relaxed를 켜고, 시간/페이지 범위를 엄격히 검증.
- Validator는 커밋과 예약을 아우르는 가상 상태 뷰(`state_virtual`)를 기준으로 판정.

