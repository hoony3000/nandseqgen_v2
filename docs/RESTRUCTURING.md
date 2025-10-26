# RESTRUCTURING.md

## 문서 개요
이 문서는 NAND operation 시뮬레이터의 핵심 개념과 설계를 정의합니다. Operation의 속성, resource 관리, 확률적 스케줄링, validation 규칙을 다룹니다.

---

## 1. Operation 모델

### 1.1 Operation 속성 정의

각 operation은 다음 속성들로 정의됩니다:

```yaml
# Operation 속성 예시
base: PROGRAM_SLC              # Operation의 기본 카테고리
multi: false                   # Multi-plane operation 여부
scope: "DIE_WIDE"             # 리소스 점유 범위 (DIE_WIDE, PLANE, GLOBAL)
affect_state: false           # 이 operation이 state를 변경하는지 여부
instant_resv: true            # 즉시 예약 여부 (duration이 0인 경우)
celltype: FWSLC               # 대상 cell type

sequence:                     # 후속 operation 확률 및 상속 정의
  probs:
    - ONESHOT_PROGRAM_CSB.SEQ: 1.0
    - ONESHOT_PROGRAM_CSB_CACHE.SEQ: 0.0
  inherit:
    - ONESHOT_PROGRAM_CSB.SEQ: ['same_page']
    - ONESHOT_PROGRAM_CSB_CACHE.SEQ: ['same_page']

states:                       # 이 operation이 점유하는 state들
  - ISSUE:
      bus: true              # Bus 점유 여부
      duration: 0.4          # 지속 시간 (마이크로초)
  - CORE_BUSY:
      bus: false
      duration: 8.0

payload: []                   # 이 operation이 요구하는 payload 필드
```

**속성 설명:**
- **base**: Operation을 카테고리화하는 기본 타입 (PROGRAM_SLC, ERASE, READ 등)
- **multi**: `true`일 경우 여러 plane을 동시에 점유하는 multi-plane operation
- **scope**: 리소스 점유 범위
  - `PLANE`: 특정 plane만 점유
  - `DIE_WIDE`: Die 전체 점유 (모든 plane 차단)
  - `GLOBAL`: 전역적으로 점유
- **affect_state**: State machine에 영향을 주는지 여부
- **instant_resv**: Duration이 0이어서 즉시 완료되는 operation인지
- **celltype**: 대상 cell type (FWSLC, TLC 등)
- **sequence**: 이 operation 완료 후 자동으로 예약될 후속 operation 정의
- **states**: 이 operation이 점유할 state 리스트와 각 state의 duration

### 1.2 Operation 동작 특성

Operation은 다음과 같은 동작 특성을 가집니다:

#### Schedule (예약)
- NAND operation은 scheduler에 의해 예약되고 수행됩니다
- 예약 시점에 resource state를 검증하여 가능 여부를 판단합니다

#### Resource Occupation (리소스 점유)
- Operation마다 속성이 정해져 있고, 속성에 따라 NAND resources가 변경됩니다
- 리소스는 plane, die, global 레벨로 계층화되어 있습니다

#### Schema & Payload
- Operation 종류에 따라 schema가 정해지고, schema의 속성값은 런타임에 결정됩니다
- Operation 종류에 따라 NAND에 입력해야 하는 command, payload(address, data 등)가 정해집니다

#### Execution & Duration
- Scheduler가 operation을 수행하기 위한 execution time이 있습니다
- Execution이 끝나면 NAND resource가 예약되어 특정 시간동안 resource의 state가 변화합니다
- State는 operation당 여러 개가 될 수 있고 각 state별로 duration이 정해져 있습니다
- Duration은 사전에 정의되어 고정값을 지닙니다

#### Suspension & Resumption
- Suspend operation은 진행 중이던 다른 operation을 중단하게 만듭니다
- 예약된 resource의 state 변화가 중단됩니다
- 중단된 동안 다른 operation이 수행될 수 있습니다
- 이후 resume operation을 수행하면 중단됐던 operation의 리소스 스케줄이 재개됩니다

#### Reset
- Reset operation은 현재 진행중인 모든 operation의 수행을 완전히 중단시킵니다
- Resource state도 변하게 됩니다

#### Operation 간 Exclusivity
- 특정 operation이 수행되면, 그 동안 다른 특정 operation의 수행이 금지될 수 있습니다
- Operation의 수행이 완료되면 금지도 풀립니다

#### Operation 간 Dependency
- 특정 operation은 이전에 어떤 operation이 수행된 이후에만 수행될 수 있습니다
- 두 operation 간에는 operation의 address 속성이 상속되거나 특정한 제한을 가질 수 있습니다

#### Operation 간 Chaining
- 특정 operation이 수행되면 반드시 뒤따라 수행되어야 하는 operation이 하나 이상 예약될 수 있습니다
- 예: 어떤 operation이 예약되면 성공적으로 종료됐는지 확인하는 operation을 추가 예약
- 예: read 후 출력하는 operation이 예약됨

#### Multi-plane Operation
- 특정 동작은 plane resource를 여러 개 동시에 점유할 수 있습니다

#### Probablistic Scheduling
- Operation의 예약은 resource state에 따라 가능한 후보 operation 중 확률적으로 선택됩니다
- **중요!** 이 확률은 사전에 정의한 가중치에 따라 샘플되고, **operation에 bound되지 않고 resource state에 따라 샘플됩니다**

---

## 2. Address 모델

### 2.1 Address Scheme

Address는 다음 구조를 가집니다:

```python
class Address:
    die: int
    planes: List[int]        # Multi-plane operation의 경우 여러 개
    blocks: List[int]        # Plane 개수와 동일
    pages: List[int]         # Plane 개수와 동일
    celltypes: List[str]     # Plane 개수와 동일
```

### 2.2 Address 상속 규칙

특정 operation이 예약될 때 연속해서 예약되는 operation이 정의된 경우, 후속 operation은 address 및 순서에 대한 종속성이 생깁니다.

**상속 규칙 종류:**

| 규칙 | 설명 |
|------|------|
| `same_page` | 상속 대상 operation의 address를 그대로 상속 |
| `inc_page` | die, plane, block은 동일하게, page는 1 증가시킴 |
| `same_celltype` | 상속 대상 operation의 celltype을 동일하게 함 |
| `multi` | Multi-plane operation을 상속할 경우 동일하게 multi-plane operation으로, address 값도 동일하게 함 |
| `multi_dout` | Read operation을 상속할 경우 dout operation의 예약을 plane 개수만큼 함 |
| `prev_page` | 여러 operation이 순차적으로 상속되어 예약될 경우, 직전 operation의 address를 상속 |
| `pgm_same_page` | 여러 operation이 순차적으로 상속되어 예약될 경우, program operation의 address를 상속 |
| `same_page_from_program_suspend` | Program suspend operation에 의한 상속일 경우, suspended program operation의 address를 상속. Suspend operation 자체에는 address 속성이 없기 때문 |

---

## 3. Payload 모델

### 3.1 Payload Scheme

Operation base별로 필요한 payload 필드가 정의됩니다:

```yaml
payload_by_op_base:
  NOP: []
  ETC: [die]
  ERASE: [die, plane, block, page, celltype]
  DSL_VTH_CHECK: [die, plane, block, page, celltype]
  PROGRAM_SLC: [die, plane, block, page, celltype]
  ALLWL_PROGRAM: [die, plane, block, page, celltype]
  CACHE_PROGRAM_SLC: [die, plane, block, page, celltype]
  ONESHOT_PROGRAM_LSB: [die, plane, block, page, celltype]
  ONESHOT_PROGRAM_CSB: [die, plane, block, page, celltype]
  ONESHOT_PROGRAM_MSB: [die, plane, block, page, celltype]
  ONESHOT_PROGRAM_MSB_23H: [die, plane, block, page, celltype]
  ONESHOT_PROGRAM_EXEC_MSB: [die, plane, block, page, celltype]
  ONESHOT_CACHE_PROGRAM: [die, plane, block, page, celltype]
  TRANSFER_TO_CACHE_LSB: [die]
  TRANSFER_TO_CACHE_CSB: [die]
  TRANSFER_TO_CACHE_MSB: [die]
```

---

## 4. Resource 모델

### 4.1 리소스 구조

NAND resources는 **multi-level 구조**를 가집니다:
- **Plane level**: Plane별로 독립적인 state
- **Die level**: Die 전체에 영향을 주는 state
- **Global level**: 전역적인 state

각 resource별로 어느 level에서 변경되는지는 다릅니다.

### 4.2 State 종류

#### bus_state
- **정의**: Bus 점유 상태
- **제약**: Operation의 execution time이 동시에 겹칠 수 없습니다
- **레벨**: Global

#### block_pgm_state
- **정의**: Block/page별 erase/program 상태
- **구성**: 여러 개의 die, block, page로 구성
- **변화**:
  - Erase operation 수행 후 값이 변함
  - Program operation 수행 후 값이 변함
- **제약**: 이 값에 따라 이후 수행될 erase/program/read operation의 address 속성에 제한이 생김
- **레벨**: Plane

#### block_celltype_state
- **정의**: Block별 celltype 기록
- **변화**: Block별로 어떤 celltype으로 erase/program operation이 수행됐는지 기록
- **제약**: Celltype에 따라 해당 block에 수행될 수 있는 erase/program/read operation의 celltype이 제한됨
- **레벨**: Plane

#### op_state
- **정의**: Operation 수행 후 내부 상태
- **변화**: Operation이 수행되면 NAND 내부적으로 op_state가 변경
- **제약**: 특정 operation이 금지됨
- **복구**: op_state의 duration이 끝나면 ready 상태가 됨
- **레벨**: Plane 또는 Die (operation의 scope에 따라)

#### cache_state
- **정의**: Cache 점유 상태
- **변화**: 특정 operation 수행 후 cache_state가 설정됨
- **제약**: Cache_state를 종료하는 operation이 수행되지 않으면 상태가 지속되어 특정 동작이 금지됨
- **레벨**: Die

#### suspend_state
- **정의**: Suspend/Resume 상태
- **변화**:
  - Suspend operation이 수행되면 대상 operation이 중단되고 suspend_state가 변경됨
  - 대상 operation이 수행되고 있지 않은 상황이면 suspend_state는 변경되지 않음
- **제약**: Suspend 상태일 때 특정 operation은 금지됨
- **복구**: Resume operation이 수행되기 전까지 suspended operation은 재개되지 않음
- **레벨**: Die

#### odt_state
- **정의**: ODT (On-Die Termination) 활성화 상태
- **변화**: ODT enable operation이 수행되면 설정됨
- **제약**: 특정 operation이 금지됨
- **복구**: ODT disable operation이 수행되면 금지가 해제됨
- **레벨**: Die

#### latch_state
- **정의**: 내부 latch data 점유 상태
- **변화**: 특정 operation 수행 시 NAND 내부 latch에 data가 load됨
- **제약**: Latch data를 사용하기 전까지 다른 data가 latch에 load되지 않도록 latch load 관련 특정 operation이 금지됨
- **복구**: Latch data가 특정 operation에 의해 사용되면 latch_state가 해제됨
- **레벨**: Die

---

## 5. 확률 모델 (Phase Conditional)

### 5.1 op_state 기반 확률 가중치 생성 방법

Phase conditional dictionary는 다음 절차로 생성됩니다:

#### Step 1: op_state 정의
- op_state는 `op_base.state` 형식입니다
- 예: `PROGRAM_SLC.ISSUE`, `PROGRAM_SLC.CORE_BUSY`, `ERASE.CORE_BUSY`

#### Step 2: Operation 카테고리화
- 각 operation은 카테고리화되어 `op_base` 속성을 지닙니다
- 각 operation은 state list를 속성값으로 가지며, state마다 duration 값이 정의됩니다

#### Step 3: op_state 리스트 생성
- 모든 operation을 순회하여 op_state(op_base.state) list를 만듭니다
- 순회할 때 특정 op_base를 제외하고 대부분은 `op_base.END` state를 op_state list에 추가합니다
  - **목적**: Operation 간 dependency를 모델링하기 위해 operation이 끝난 직후에 특정 operation을 금지하거나 빈도수를 높이기 위한 모델링

#### Step 4: DEFAULT state 추가
- Fallback을 위해 `DEFAULT` op_state를 추가합니다

#### Step 5: Exclusion 정의
- 미리 `exclusion_by_op_state`를 정의해 op_state별 금지시킬 operation을 정의합니다
- op_state list에서 각 op_state key 값별 금지 operation을 제외합니다

#### Step 6: 확률 가중치 Override
- 미리 `phase_conditional_overrides`를 정의해 level(operation, op_base, global)별로 확률 가중치를 override합니다
- **Override 합이 1.0 미만인 경우**: 남은 후보에게 랜덤 분배하여 모든 확률의 합이 1.0이 되도록 조정
- **Override 합이 1.0 이상인 경우**: 전체 확률값을 조정해 확률 합이 1.0이 되도록 조정

#### Step 7: Runtime 확률 조정
- Operation 확률 가중치는 현재의 다른 resource state(latch_state, suspend_state 등)에 따라 runtime으로 변할 수 있습니다

---

## 6. Validation 규칙

Scheduler는 operation 예약 시 다음 validation 규칙들을 검증합니다:

| 규칙 | 설명 |
|------|------|
| `bus_exclusion` | Bus를 점유하는 state(ISSUE, DATA_IN, DATA_OUT)가 동시에 발생하지 않도록 검증 |
| `busy_exclusion` | 동일 plane/die에서 CORE_BUSY state가 중복되지 않도록 검증 |
| `multi_exclusion` | Multi-plane operation이 die를 독점적으로 사용하는지 검증 |
| `latch_exclusion` | Latch를 점유하는 operation이 충돌하지 않도록 검증 |
| `suspend_exclusion` | Suspend 상태에서 금지된 operation이 수행되지 않도록 검증 |
| `odt_exclusion` | ODT 활성화 상태에서 금지된 operation이 수행되지 않도록 검증 |
| `cache_exclusion` | Cache 상태 충돌 검증 |
| `addr_dependency` | Operation 간 주소 상속 제약 검증 (same_page, same_block, sequential 등) |

---

## 7. Scheduler

### 7.1 역할

- **Operation 예약**: Resource state를 참고하여 예약 가능한 operation list에 대한 확률 가중치를 기반으로 하나를 선택하여 예약
- **Resource 관리**: 예약, 종료 시 발생하는 resource들의 변화를 관리

### 7.2 예약 프로세스

1. **확률 기반 선택**: 현재 resource state에서 예약 가능한 operation list를 확률 가중치 기반으로 샘플링
2. **Runtime 검증**: 확률에 의해 선택됐어도 `block_pgm_state`, `block_celltype_state`의 상태에 따라 예약 불가능할 수 있음
3. **재시도**: 실패할 경우 다른 operation이 다시 샘플링될 수 있음
4. **Runtime 필터링**: `block_pgm_state`, `block_celltype_state`를 참고하여 특정 operation을 runtime으로 제외시킬 수 있음

### 7.3 예약 방식

- **실시간 예약**: 현재 시점에 즉시 operation 예약
- **사전 예약**: 미리 특정 시점에 operation이 예약될 수 있게 함
- **충돌 해결**: 실시간 예약 시 미리 예약된 operation이 있다면 충돌 해결 필요

### 7.4 예약 실패 처리

- 예약이 실패했을 때 그 사유를 수집해서 내보냅니다

---

## 8. Runner

### 8.1 역할

#### 확률적 생성을 위한 Random Seed 주입
- 독립적인 seed를 갖는 여러 개의 run을 동시에(또는 순차적으로) 실행 가능

#### Simulation 실행 관리
- **Run의 중단과 재개**: Resource state 모두를 저장하고, 추후 복원하여 simulation 재개 가능
- **Bootstrap**: Simulation의 원활한 전개를 위해 초기 operation sequence를 주입할 수 있음

#### 데이터 수집 및 분석
- Operation sequence, timeline, state 변화 등을 수집

#### 결과 내보내기
- CSV 형식으로 결과 export

#### Scheduler 호출
- Event 기반으로 scheduler를 호출

### 8.2 Event 종류

| Event | 설명 |
|-------|------|
| `OP.START` | Operation의 수행 시작 |
| `OP.END` | Operation의 종료 |
| `REFILL` | 특정 시간 주기를 가지고 operation이 예약될 수 있게 하는 hook |

---

## 참고사항

이 문서는 시뮬레이터의 **설계 명세**입니다. 실제 구현 세부사항은 다음 파일들을 참조하세요:
- [config.yaml](../config.yaml): Operation 정의 및 확률 설정
- [scheduler.py](../scheduler.py): Scheduler 구현
- [resourcemgr.py](../resourcemgr.py): Resource 관리 구현
- [proposer.py](../proposer.py): Operation 제안 로직 구현
- [addrman.py](../addrman.py): Address 관리 구현
