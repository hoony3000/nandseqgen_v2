# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

NAND operation 시퀀스를 확률적으로 생성하는 시뮬레이터입니다. NAND 플래시 메모리의 state 별 operation 확률과 하드웨어 제약사항을 기반으로 runtime에 operation을 제안하고 예약합니다.

## 핵심 명령어

### 환경 설정
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 기본 실행
```bash
.venv/bin/python main.py --config config.yaml --out-dir out
```

### 테스트
```bash
# 전체 테스트
.venv/bin/python -m pytest

# 특정 테스트 파일
.venv/bin/python -m pytest tests/test_suspend_resume.py

# 특정 테스트 케이스 (키워드 매칭)
.venv/bin/python -m pytest tests/test_suspend_resume.py -k resume

# 상세 출력
.venv/bin/python -m pytest -v
```

### 주요 실행 옵션
```bash
# 시뮬레이션 시간 지정 (마이크로초 단위)
python main.py --run-until 100000 --num-runs 5

# Bootstrap 활성화
python main.py --bootstrap

# 다중 사이트 배치 실행
python main.py --site-count 10 --site-start 1
```

## 아키텍처

### 핵심 구성요소

1. **main.py** - 시뮬레이터 진입점
   - CLI 인자 처리 및 설정 로딩 (YAML)
   - 다중 run 지원 및 연속성 관리 (shared ResourceManager/AddressManager)
   - CSV 내보내기 (operation_sequence, operation_timeline, op_state_timeline 등)
   - Multi-site 배치 처리

2. **scheduler.py** - Operation 예약 및 스케줄링
   - `Scheduler.run()`: 시뮬레이션 메인 루프
   - `Scheduler.tick()`: 단일 스케줄링 사이클 (propose → reserve → commit/rollback)
   - Event queue 기반 시간 진행 (OP_START, OP_END, PHASE_HOOK, QUEUE_REFILL)
   - Backlog 관리 (SUSPEND 이후 operation 재예약)
   - Bootstrap 진행 추적

3. **proposer.py** - Operation 제안 로직
   - Phase-conditional 확률 기반 operation 샘플링
   - Address dependency 검증 (same_page, same_block 등)
   - Multi-plane operation 처리
   - State blocking 감지 및 diagnostics

4. **resourcemgr.py** - NAND 리소스 상태 관리
   - Plane/die/global 레벨 리소스 추적
   - State timeline (op_state) 관리
   - Suspend/Resume 상태 관리
   - Validation 규칙 (bus_exclusion, busy_exclusion, latch_exclusion 등)
   - Snapshot/restore 지원

5. **addrman.py** - 주소 공간 관리 (NumPy 기반)
   - Block/page 상태 추적 (erase, program 횟수)
   - Celltype 별 주소 샘플링 (SLC, TLC 등)
   - Badlist 지원 (불량 블록 제외)
   - EPR (Erase-Program-Read) 정책 검증

### Operation 동작 원리

- **Probablistic scheduling**: `phase_conditional` 설정에 따라 현재 state에서 가능한 operation을 확률적으로 선택
  - **중요**: 확률은 operation이 아닌 **resource state**에 바인딩됨

- **Resource occupation**: Operation마다 `scope` (DIE_WIDE/PLANE/등), `states` (ISSUE/CORE_BUSY/등), `duration` 정의

- **State transitions**: Operation 실행 시 nand resource의 state가 변경되며, 이는 다음 operation 선택 확률에 영향

- **Suspend/Resume**:
  - `PROGRAM_SUSPEND`/`ERASE_SUSPEND`: 진행 중인 operation 중단, backlog에 저장
  - `PROGRAM_RESUME`/`ERASE_RESUME`: Backlog에서 operation을 재예약하여 재개

- **Operation chaining**: 특정 operation은 완료 후 자동으로 후속 operation을 예약 (예: READ → DOUT)

- **Multi-plane operations**: `multi: true` operation은 여러 plane을 동시 점유

### 주요 데이터 흐름

```
config.yaml 로딩
    ↓
AddressManager 초기화 (badlist.csv 적용)
    ↓
ResourceManager 초기화 (topology, dies, planes)
    ↓
Scheduler.run() 시작
    ↓
반복: Scheduler.tick()
    - EventQueue에서 다음 이벤트 배치 처리
    - PHASE_HOOK → proposer.propose() 호출
    - ResourceManager로 feasibility 검증
    - 성공 시 commit, 실패 시 rollback
    - OP_START/OP_END 이벤트 발생
    - State timeline 업데이트
    ↓
CSV 내보내기 (operation_sequence, timeline 등)
```

### 설정 파일 구조

- **config.yaml**: 메인 설정
  - `topology`: dies, planes, blocks_per_die, pages_per_block
  - `policies`: admission_window, queue_refill_period_us, topN 등
  - `op_bases`: Operation 별 states, duration, scope 정의
  - `op_names`: 구체적인 operation 정의 (celltype, base 상속)
  - `phase_conditional`: State 별 operation 확률 분포 (핵심!)
  - `features`: 기능 플래그 (suspend_resume_chain_enabled 등)

- **op_state_probs.yaml**: Auto-generated phase_conditional 확률 테이블
  - `--autofill-pc` 또는 `--refresh-op-state-probs` 옵션으로 재생성

- **badlist.csv**: 불량 블록 리스트 (die, block 필드 필수)

### 핵심 검증 규칙

ResourceManager가 다음 규칙들을 검증합니다:

- **bus_exclusion**: ISSUE/DATA_IN/DATA_OUT 시간대 중복 방지
- **busy_exclusion**: 동일 plane에서 CORE_BUSY 중복 방지
- **multi_exclusion**: Multi-plane operation 시 die-wide 독점 검증
- **latch_exclusion**: Latch 점유 충돌 방지
- **suspend_exclusion**: Suspend 상태에서 특정 operation 금지
- **odt_exclusion**: ODT 활성화 시 operation 제한
- **cache_exclusion**: Cache 상태 충돌 방지
- **addr_dependency**: Operation 간 주소 상속 제약 (same_page, same_block, sequential 등)

## 중요 개념 및 주의사항

### Backlog 메커니즘

SUSPEND operation 수행 시:
1. 진행 중인 operation들이 truncate되고 `suspended_ops`로 이동
2. 새로운 operation 제안은 `_backlog` 큐에 저장 (axis별, die별)
3. RESUME operation 수행 시:
   - `suspended_ops`에서 operation 복구 및 재예약
   - Backlog 큐의 operation들을 순차적으로 flush

### Phase Key 시스템

- **phase_key**: Proposer가 사용한 실제 state key (propose-time)
- **phase_key_used**: Reserved-time에 정규화된 key (instant operation용)
- **phase_key_virtual**: 분석용 가상 key (phase_hook context 기반)

이 세 가지 key는 CSV 내보내기에서 추적되며 확률 분포 분석에 활용됩니다.

### Event Queue 우선순위

동일 시각 이벤트 처리 순서:
1. OP_END
2. PHASE_HOOK
3. BACKLOG_REFILL
4. BACKLOG_RETRY
5. QUEUE_REFILL
6. OP_START

### AddressManager 동기화

ERASE/PROGRAM operation의 OP_END 시점에 `apply_erase()` / `apply_pgm()` 호출하여 주소 상태를 업데이트합니다. 이는 `program_base_whitelist`에 정의된 base만 해당됩니다.

## 코드 스타일

- Black 포맷터 사용 (line length 88)
- Type hints 적극 활용
- Dataclass로 구조화 데이터 표현
- 모듈 함수: `snake_case`, 클래스: `PascalCase`

## 테스트 작성 가이드

- `pytest` 기반
- 파일명: `test_<feature>.py`
- 명시적 assertion 선호 (스냅샷 파일보다)
- Fixture로 재사용 가능한 설정 제공

## 출력 파일

`out/` 디렉토리에 다음 CSV들이 생성됩니다:
- `operation_sequence_*.csv`: 시퀀스, 시간, opcode, payload (JSON)
- `operation_timeline_*.csv`: Operation별 시작/종료 시각, phase_key
- `op_state_timeline_*.csv`: State 변화 timeline
- `address_touch_count_*.csv`: Block/page별 READ/PROGRAM 횟수
- `op_state_name_input_time_count_*.csv`: State별 operation 분포
- `phase_proposal_counts_*.csv`: Phase key별 제안 통계
- `snapshots/state_snapshot_*.json`: ResourceManager 상태 스냅샷

## 문서 참조

- [AGENTS.md](AGENTS.md): AI 에이전트용 상세 가이드라인
- [INPUT.md](INPUT.md): 개발 메모 및 작업 히스토리
- [docs/RESTRUCTURING.md](docs/RESTRUCTURING.md): Operation 속성 및 리소스 설계
