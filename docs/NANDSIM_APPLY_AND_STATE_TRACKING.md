# NANDSim 상태 적용(apply) · 추적(tracking) · 검증(validate) 설계 대안

작성자: Engineering
상태: 제안(토론용, 구현 가능)

## 0) 배경 · 문제 · 목표
- 배경: `addrman.py`는 샘플링(`random_*`)과 동시에 상태를 즉시 갱신(ERASE/PGM 적용)합니다. 시뮬레이터(`nandsim_demo.py`)는 스케줄링 시점에 “미래(future)”와 “커밋(committed)”을 분리해 다루어야 합니다. 또한 Validator가 READ/PGM/ERASE의 겹침을 정확히 판단하려면, 아직 커밋되지 않은 예약 상태를 포함한 일관된 상태 뷰가 필요합니다.
- 문제: 샘플링·적용을 한 곳에서 동시에 수행하면(현재 addrman) 계획 단계에서 롤백/재현이 어려워지고, Validator와의 연계가 복잡해집니다.
- 목표:
  - 샘플링, 검증, 스케줄링을 분리하고, 상태는 “커밋/미래”로 분리 관리
  - Validator가 사용할 일관된 상태 뷰 제공(effective state)
  - 시각화 필요 시에만 `addrman.py`에 선택적으로 반영

## 1) 설계 대안 비교(요약)
- 대안 A: ResourceManager 오버레이(권장)
  - 개요: `ResourceManager`가 커밋 상태(배열) + 미래 오버레이(예약)로 상태를 단일 소스로 관리. 샘플러는 이 상태 뷰를 기반으로 비파괴 샘플링. 커밋 시점에만 `addrman.py`에 반영(옵션).
  - 장점: 명확한 책임 분리, 롤백/리플레이 용이, Validator 연계 쉬움
  - 단점: 오버레이 병합 로직 필요, addrman와 이중화 가능성
  - 위험: 오버레이와 커밋의 동기화 버그 → 철저한 테스트로 완화
- 대안 B: AddressManager 단일 소스 + 적용/되돌리기(undo)
  - 개요: 샘플링 시 `random_*` 호출 후 즉시 `undo_last()`로 되돌려 비파괴 시뮬레이션. 예약 확정 시 다시 적용.
  - 장점: 구현 간단, 기존 코드 활용
  - 단점: RNG/상태 비결정성, 다중 예약 동시 처리 복잡, 성능 저하 가능
  - 위험: 되돌리기 누락/중첩 시 오염 → 회귀 위험 높음
- 대안 C: 이벤트 소싱(Event Sourcing)
  - 개요: 모든 연산을 시간순 이벤트로 저장, 쿼리 시 스냅샷+재생으로 상태 계산
  - 장점: 시간여행/감사 용이, 강력한 일관성 모델
  - 단점: 구현 난이도/비용 큼, 성능/메모리 비용 증가
  - 위험: 과도한 복잡화 → 범위 초과 우려

권장: 대안 A(ResourceManager 오버레이)

## 2) 권장안 상세(A) — 단일 소스 상태 + 오버레이
### 2.1 데이터 모델
- 커밋 상태(전역, die×block 단위)
  - `state_committed[global_block]: int`  # -3 BAD, -2 GOOD, -1 ERASE, 0..pagesize-1
  - `mode_committed[global_block]: mode`
- 미래 오버레이(예약)
  - `future_erase_by_block[(die,block)]: List[(t0,t1)]`
  - `future_pgm_by_block[(die,block)]: List[(t0,t1, pages:int)]`  # 연속 PGM 크기 포함
  - `future_read_by_block[(die,block)]: List[(t0,t1, pages:int, start_page:int)]`  # 선택사항
- 도우미
  - `blocks_per_die`, `num_dies`, `num_planes` (addrman과 동일 기준)
  - `effective_state_view(now?)` → 샘플링/검증용 계산 결과(배열 뷰 또는 on-demand 질의)

### 2.2 상태 뷰 계산(effective)
- 용도에 따라 2가지 접근을 병행 가능
  1) 필요 시 on-demand로 질의: 특정 (die,block)에 대해 미래 예약을 스캔해 읽기/쓰기 가능성 판정
  2) 빈번한 대량 샘플링을 위해 “가상 상태 배열” 구성:
     - `state_virtual = state_committed.copy()`
     - 각 블록의 미래 예약을 적용한 가상 진행도 계산: ERASE가 예약되어 있으면 읽기 후보에서 제외, PGM이 k만큼 예약되면 `min(state_committed + k, pagesize-1)`로 가정 등
     - 모드는 커밋 기준/예약 기준 일치 확인

### 2.3 샘플링(Sampler)
- `Sampler`는 `ResourceManager`로부터 “가상 상태 배열(view)”를 주입받아 비파괴 샘플링 구현
  - 구현 옵션 1) `addrman.py`의 랜덤 로직을 함수형으로 분리/복제해, 배열 인자를 받아 동등 로직을 수행(적용 없이)
  - 구현 옵션 2) `AddressManager`에 `pick_*`(apply 없음) API 추가
- 반환은 항상 `(die, block, page)`; `sel_plane`, `sel_die`, `size`, `sequential`, `mode`, `offset` 지원

### 2.4 검증(Validator)
- 입력: OpSpec + 샘플링 결과(addrs)
- 룰 예시(미래 오버레이와 교차 확인)
  - ERASE: 해당 (die,block)에 READ/PGM 예약과 시간 충돌이 없어야 함
  - PGM: pagesize-1 초과 금지, 모드 일치, 같은 시점의 READ/ERASE와 충돌 금지
  - READ: `state_virtual >= offset` 만족, 같은 시점 또는 앞선 시점의 ERASE 예약과 충돌 금지, PGM 예약과 정책적 충돌 시 제외
- 결과: (ok: bool, filtered_addrs: ndarray, reasons: list[str])

### 2.5 스케줄링(Scheduler) · 적용(Apply)
- 계획(plan): `Sampler` → `Validator` → `ResourceManager.can_reserve` → `reserve`
- 적용 시점(commit/execute):
  - `ResourceManager`의 미래 오버레이에 예약을 기록(시간 구간)
  - 시각화가 필요할 때만 `addrman.py`에 반영:
    - 옵션 a) 커밋 시점에만 반영(권장) — `addrman.set_adds_*` 또는 내부 직접 갱신
    - 옵션 b) 예약 시 반영하되, `addrman`은 “가상”으로만 사용(권장하지 않음)

## 3) 대안 B 상세 — addrman 적용/되돌리기
- 샘플링 시 `random_*` → 즉시 `undo_last()`로 되돌림
- 다중 예약/연속 PGM 등 복합 시나리오에서 되돌리기 스택 관리 필요
- RNG/상태 전개가 계획 단계마다 달라질 수 있어 재현성 저하
- Validator가 미래 예약을 인지하려면 별도 구조가 또 필요 → 이점 희박

## 4) 대안 C 상세 — 이벤트 소싱
- 모든 연산을 `(op, addrs, time_window, mode, pages)` 이벤트로 기록
- 쿼리 시 스냅샷+재생으로 상태·충돌 판정
- 강력하지만 과도하게 무거움(현 범위 초과)

## 5) Validator 연계 구체화
### 5.1 공통 타입
- `Address = (die:int, block:int, page:int)`
- `OpSpec = { name, mode, size, sequential, sel_plane, sel_die, offset, time_window }`

### 5.2 의사코드
- PGM 예시
```python
def validate_pgm(addrs, op: OpSpec, rm: ResourceManager):
    ok = [] ; reasons = []
    for row in addrs:  # (#, k, 3) 멀티‑플레인 가능
        dies = {a[0] for a in row}; blocks = [a[1] for a in row]
        if len(dies) != 1:  # 그룹은 단일 다이 제약
            reasons.append("cross-die group"); continue
        die = next(iter(dies))
        # 1) 미래 충돌 검사(버스/가용/ERASE/READ 예약과 충돌 금지)
        if not rm.can_reserve(op, row, op.time_window):
            reasons.append("resource conflict"); continue
        # 2) 페이지 상한 검사 (가상 상태 기준)
        if not rm.pages_available_for_pgm(die, blocks, op.size):
            reasons.append("pagesize overflow"); continue
        # 3) 모드 일치 검사
        if not rm.modes_match(die, blocks, op.mode):
            reasons.append("mode mismatch"); continue
        ok.append(row)
    return (len(ok) > 0, np.array(ok), reasons)
```
- READ 예시
```python
def validate_read(addrs, op: OpSpec, rm: ResourceManager):
    ok = [] ; reasons = []
    for row in addrs:
        die = row[0][0]
        if not rm.can_reserve(op, row, op.time_window):
            reasons.append("resource conflict"); continue
        if not rm.readable_under_offset(die, row, op.offset):
            reasons.append("offset too high"); continue
        ok.append(row)
    return (len(ok) > 0, np.array(ok), reasons)
```

## 6) 구현 체크리스트
- ResourceManager
  - [ ] 커밋 배열(state/mode) + 미래 오버레이 구조체 설계
  - [ ] effective 상태 뷰 계산(배열/온디맨드) 제공
  - [ ] can_reserve / reserve / commit API
  - [ ] (선택) addrman 반영 함수(커밋 시)
- Sampler
  - [ ] 비파괴 `sample_*` 구현(함수형 로직 또는 `pick_*` API 추가)
  - [ ] sel_plane/die, sequential, size, mode, offset 지원
- Validator
  - [ ] 충돌/모드/경계/오프셋 규칙 구현
  - [ ] 사유 코드(reason) 표준화
- 스케줄러
  - [ ] plan/tick 경로에서 Sampler→Validator→ResourceManager 연결
  - [ ] 정책 엔진과 결합(우선순위/가중치)
- 테스트
  - [ ] 2 dies × 2 planes × 소블록 구성 E2E(성공/실패 경로)
  - [ ] 유닛: Sampler/Validator/ResourceManager 각자 결정적 검증

## 7) 권고안 요약
- 상태의 “사실(커밋)”과 “계획(미래)”을 분리하고, `ResourceManager`를 단일 진실 소스로 둡니다.
- 샘플링은 비파괴로 수행하고, Validator는 오버레이를 포함한 일관된 상태 뷰로 판단합니다.
- 시각화는 커밋 시점에만 `addrman.py`에 반영(선택), 또는 별도 뷰로 렌더링합니다.
- 이는 유지보수성과 재현성을 높이고, 스케줄링/검증의 정확도를 개선합니다.
