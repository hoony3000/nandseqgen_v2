# PRD v2 — NAND Sequence Generator (nandsim_demo.py)

## 1. Overview
- 다양한 시나리오에서 NAND device를 평가하기 위한 다양한 operation sequence를 생성한다.
- NAND 내부 state에 따른 확률 기반 샘플링으로 sequence를 생성해 미세조정 가능한 시스템을 만든다.
- 생성된 sequence를 ATE(Automatic Test Environment)가 사용 가능한 파일 형태로 출력한다.

## 2. Terminology
- op_id: operation을 schedule 할 때 생기는 고유값. log tracing을 위해 사용
- op_name: operation 이름
- op_base: operation 종류
- payload: operation 수행에 필요한 parameters. operation마다 형태 상이
- source: operation 생성과정의 출처
- op_state_phase: 어떤 operation 동작 중 어떤 state의 timeline에서 operation이 schedule 됐는지의 정보 (가능/불가능 CMD에 영향)
- cell_type: TLC/SLC/A0SLC/ACSLC/AESLC/FWSLC
- lane: die-block을 하나의 변수로 표현한 값 (gantt 시각화용)

## 3. Required Outputs

### 3.1 Operation Sequence
- 목적: 생성된 sequence를 실제 device test 가능한 파일로 변환하기 위한 궁극적 결과물
- 파일형태: `operation_sequence_yymmdd_0000001.csv`
- 필수 필드: `seq,time,op_id,op_name,op_uid,payload`
- payload는 op_name에 따라 다르며, CFG[payload_by_op_base] 에 명시
- 예시:
```
seq,time,op_id,op_name,op_uid,payload
5,101.0,4,SIN_PROGRAM,"[{""die"":0,""pl"":0,""block"":0,""page"":0}]"
17,301.0,3,MUL_ERASE,"[{""die"":0,""pl"":0,""block"":4,""page"":0},{""die"":0,""pl"":1,""block"":1,""page"":0}]"
6,121.0,8,SR,"{""die"":0,""pl"":0,""block"":0,""page"":0}"
```

#### 3.1.1 CSV Payload JSON 인코딩 정책
- 목적: CSV 출력 내 `payload` 필드에 JSON을 결정적이고 상호운용 가능하게 포함하기 위한 규칙 정의
- 표준: CSV는 RFC 4180을 따르고, `payload`는 유효한 UTF-8 JSON 문자열이어야 함
- 인코딩 규칙
  - CSV 라이터는 필드 내부의 `"`를 `""`로 이스케이프함
  - JSON은 키와 문자열에 쌍따옴표만 사용하고 홑따옴표 금지
  - 엔드투엔드: JSON 문자열 → CSV 필드 값 → 리더가 CSV 파싱 → JSON 파싱
- 권장 필드: `payload`(오퍼레이션 파라미터용 JSON; 스키마는 `op_name`에 종속)
- Writer/Reader 가이드 (Python 기준)
  - 작성: `csv` 모듈 `quoting=csv.QUOTE_MINIMAL` + `json.dumps(obj, separators=(",", ":"))`
  - 읽기: `csv`로 문자열 필드 로드 후 `json.loads`로 파싱
  - 커스텀 이스케이프/수기 치환 금지, CSV/JSON 표준에 의존


### 3.2 Address Touch Count
- 목적: 다양한 address에 operation이 수행됐는지 확인
- 파일형태: `address_touch_count_yymmdd_0000001.csv`
- 필수 필드: `op_base,cell_type,die,block,page,count`
- 의미: address(die,block,page) 별 program/read 수행 횟수
- 시각화: heatmap (die,block은 묶어 가독성 향상)
  - x: lane, y: page

### 3.3 Operation Timeline
- 목적: operation 다양성 및 operation 간 충돌 확인
- 파일형태: `operation_timeline_yymmdd_0000001.csv`
- 필수 필드: `start,end,die,plane,block,page,op_name,op_base,source,op_uid,op_state`
- 범위: (die,block) 별 operation timeline
- 시각화: gantt 차트
  - x: time, y: lane, 점유: op_name

### 3.4 op_state Timeline
- 목적: op_state에서 허용되지 않는 operation 수행 여부 및 plane/die level 충돌 확인
- 파일형태: `op_state_timeline_yymmdd_0000001.csv`
- 필수 필드: `start,end,die,plane,op_state,lane,op_name,duration`
- 범위: (die,plane) 별 op_state_phase timeline
- 시각화: gantt 차트
  - x: time, y: plane, 점유: op_state

### 3.5 op_state x op_name x input_time Count
- 목적: 다양한 op_state에서 다양한 operation이 다양한 시점에서 실제로 수행되는지 확인 (실제 propose 시 참조한 op_state 사용)
- 파일형태: `op_state_name_input_time_count_yymmdd_0000001.csv`
- 필수 필드: `op_state,op_name,input_time,count`
- 의미: 어떤 op_state에서 operation이 어느 time에 schedule 됐는지
- input_time: 0~1 사이 소수 (op_state 전체 duration 대비 시점)
- 시각화: histogram
  - x: op_state-op_name-input_time, y: count

### 3.6 State Snapshot
- 목적: run 간 state를 안전·결정적으로 스냅샷/재개해 연속성 유지
- 디렉터리: `snapshots/`
- 파일명: `state_snapshot_YYYYMMDD_HHMMSS_<run_id>.json`
- 포맷: JSON. 대형 바이너리(RNG 상태 등)는 base64 인코딩. 대형 배열(addr_state/modes 등)은 옵션으로 사이드카 `.npy` 파일을 사용하고 JSON에서는 해당 파일명을 참조
- 원자성: `*.json.tmp` 등 임시 파일로 기록 후 fsync → 최종명으로 rename. 사이드카가 있다면 사이드카를 모두 작성·fsync 후 인덱스 JSON을 마지막에 쓴다.
- 스냅샷 대상
  - 전역: 스키마/PRD 버전, `config.yaml` 커밋 해시, RNG 상태(시드+생성기 바이트), 현재 시뮬레이션 시간 `t`
  - ResourceManager: (die,plane)별 `op_state_timeline` 꼬리(마지막 상태와 종료 시각), 다이별 `suspend_states`, `odt_state`, (die,plane)별 `cache_state`, 활성 `exclusion_windows`, `ongoing_ops`/`suspended_ops` 메타데이터(op_id, op_name, base, 타깃 주소, 잔여 duration)
  - AddressManager: `addr_state` 배열, `addr_mode_erase`, `addr_mode_pgm`
- 재개 절차
  1) JSON 로드 → 버전/스키마 검증 → 사이드카(있다면) 로드
  2) 토폴로지로 관리자 인스턴스 재구성 후 타임라인/상태 복원
  3) 진행 전 불변식 검증(타임라인-배제 윈도우 일관성 등) 후 실행 재개
- 프라이버시/보안: 스냅샷은 로컬 테스트 산출물로 취급하고 외부 유출 금지

## 4. Success Metrics (달성 지표)
- `op_state x op_name x input_time count`의 고른 분포 확보 → 정량 지표 필요
- sequence 평균 생성 속도 ≥ 2000개/초
- operation sequence의 validation check 모두 통과

## 5. Architecture & Components (draft)

### 5.1 Data classes

#### 5.1.0 Address class
- 대상: ERASE/PROGRAM/READ/DOUT/DATAIN/SR_ADD/RESET_LUN/SETFEATURE_LUN/GETFEATURE_LUN은 target address 필요
- 주소 체계: (die, planes, blocks, page)
  - multi-plane의 경우 planes, blocks는 `CFG[topology][planes]` 최대갯수까지 가질 수 있음
```python
@dataclass(frozen=True)
class Address:
    die:int;
    plane:int;
    block:int;
    page:Optional[int]=None
```

#### 5.1.1 State class
```python
@dataclass
class StateSeg:
    name:str
    dur_us: float
    bus: bool = False
```

#### 5.1.0 Operation class
```python
from dataclasses import dataclass
from typing import Protocol, Optional, List, Tuple, Any

@dataclass
class Operation:
    op_id: int
    op_name: str
    op_base: str
    payload: dict
    target: List[Address]  # (die,plane,block,page)
    states: List[StateSeg]
    source: str
    meta: Dict[str,Any] = field(default_factory=dict)
```


### 5.2 CFG
- 생성: `config.yaml` 파일을 읽어 구성
- topology: device 기본 구조 parameter
- celltypes: 지원 celltype 종류
- op_bases: base operation 별 parameter
  - scope: operation 예약 레벨
  - affect_state: 예약 이후 시점의 state 변화 여부
  - sequence: operation을 sequence 형태로 제안할 경우 가능한 case 선언
    - probs: 각 case 별 확률
    - inherit: 후속 operation의 상속 조건 명시
- payload_by_op_base: op_base 별 payload 명시.
- policies
  - admission_window
  - queue_refill_period_us: `Scheduler`의 QUEUE_REFILL hook 생성 주기
  - maxplanes: multi-plane 동작 시 시도 가능한 최대 plane 갯수
  - maxloop_seq: sequence 생성 시 최대 길이
  - maxtry_candidate
- generate_seq_rules: sequence 생성 규칙
- latencies: operation 별 state latency 값
- op_names: operation 별 parameter
- phase_conditional: op_state 별 operation 샘플링 확률 정의 (autofill 필요)
- 중요: runtime으로 만들어야 하는 key들
  - op_specs: op_bases의 format 상속, 값은 op_names를 참고하여 operation 별 parameter 생성
    - scope, affect_state, sequence, states: op_base에서 상속
    - states[state_name][duration]은 `op_names[op_name][durations][state_name]` 값으로 대체
  - groups_by_base: base가 동일한 op_name들을 list로 구성. `Proposer`, `Validator`에서 사용
  
  - phase_conditional 자동 채움 정책
    - 원칙
      - 결정적 기반 + 확률적 선택: 시드 가능한 RNG로 샘플링하며 동일 시드에서 재현 가능하도록 한다.
      - 보수적 기본값: 미정 조합은 0으로 두고, 처음에는 명확히 안전한 조합만 허용한다.
      - 상속: base 레벨의 사전분포는 명시적 오버라이드가 없는 한 파생 `op_name`에 적용한다.
    - 초기화 가이드
      1) `op_state_probs.yaml` 파일이 있으면 로드하여 사용한다. 없으면 아래 단계로 생성한다.
      2) `op_specs`의 key(op_name)와 각 `states`를 결합해 모든 `op_name.state`를 만들고, 이를 phase_conditional의 기본 key로 사용한다.
        - .ISSUE state 는 제외하고, .END state 는 모든 op_name 에 대해서 추가한다.
        - 단 예외적으로 DEFAULT op_base.state 형태가 아닌 DEFAULT 그 자체로 key 에 추가한다.
      3) 각 `op_name.state`의 후보는 기본적으로 `CFG[op_names]`의 모든 `op_name`이며, `CFG[exclusion_groups][CFG[exclusions_by_op_state[op_name.state]]]`에 속한 base에 해당하는 후보는 제외한다(`groups_by_base` 활용).
      4) 남은 후보 중 특별 가중치가 필요한 항목은 `config.yaml`의 `phase_conditional_overrides`로 명시적으로 override한다(예: RESET 등). 이때 3)에서 제외된 후보는 override에서도 제외한다.
        - override 규칙
          - CFG[phase_conditional_overrides] 의 key 값을 순회하여 key 값에 따라 override 항목 정한다
            - global: 모든 op_state 에 적용한다.
            - 특정 op_state: 특정 op_state 에만 적용한다. e.g) ERASE.END, READ.CORE_BUSY
            - overrides 순서는 global->특정 opstate 순
      5) override하지 않은 후보들의 확률은 랜덤 샘플로 채운 뒤 양수 항목의 합이 1이 되도록 정규화한다.
      6) 이렇게 생성한 초기 확률을 `op_state_probs.yaml`로 저장하고, 필요 시 사용자가 값을 수동으로 미세 조정한다.
    - 템플릿(YAML 예시)
      ```yaml
      phase_conditional:
        READ.CORE_BUSY:
          DOUT: 0.9
        READ.DATA_OUT:
          SR: 0.01
        ERASE.CORE_BUSY:
          ERASE_SUSPEND: 0.01
      ```
    - 자동 채움 규칙
      - op_bases와의 일관성 유지: 파생 `op_name`은 해당 base의 제약을 따른다.
      - 래치/배제 정책 준수: `exclusions_by_*`로 금지된 `op_name`의 확률은 0으로 한다.
      - 상태별 정규화: 양수 항목이 존재하면 합이 1이 되도록 정규화한다.
    - 런타임 적응(선택)
      - 승인율/큐 기아를 추적해 작은 보정(±epsilon)을 적용하되, 사전 정의한 min/max 범위 내에서 제한한다.
      - 학습된 보정은 기본 설정과 분리해 저장한다.

  - 런타임 정규화 규칙
    - Topology 키 매핑: `dies→num_dies`, `planes→num_planes`, `blocks_per_die→num_blocks`, `pages_per_block→pagesize`로 런타임 키를 생성한다. 각 값은 0보다 큰 정수로 검증한다.
    - Policies: `maxplanes` 표기를 단일화해 사용하고, 정수 범위 검증을 수행한다.
    - `op_bases.states` 정규화: YAML이 단일 키 맵 리스트(`[{ISSUE:{...}}, {CORE_BUSY:{...}}]`)인 점을 고려하여, 런타임에서는 `{state_name: state_spec}` 딕셔너리로 변환한다. 변환 시
      1) 각 원소가 정확히 하나의 키만 갖는지 확인,
      2) 순서를 보존하며 삽입,
      3) `duration`(0 이상 실수), `bus`(bool) 필수 키 검증을 수행한다.
    - `generate_seq_rules` 정규화: `sequences`가 단일 키 맵 리스트 형태인 경우, 런타임에서 `{op_base: [inherit_rules...]}` 딕셔너리로 변환해 접근성을 높인다. 이후 본 문서의 예시처럼 `CFG[generate_seq_rules][key][op_base]` 형태로 조회 가능하다.
  - 파생 런타임 키: `op_specs[op_name]`는 `op_bases[base]`를 상속하되, `op_names[*]`의 `durations/multi/celltype`로 오버라이드한다. 이때 base에 정의된 상태의 duration 값은 무시하고, `op_names[op_name].durations[state]`를 단일 진실로 사용한다.
  - `op_specs[op_name].instant_resv`: bool, 기본 false. true이면 admission window 상한에 관계없이 현재 훅 시각 `t` 이후의 earliest feasible 시각에 예약을 시도한다. 동일 틱 원자성(전부/없음)과 모든 검증/배제 규칙은 그대로 적용된다.
  - `groups_by_base`: 동일 base에서 파생된 `op_name` 리스트를 구성한다.
  - `exclusion_groups` / `exclusions_by_*`: config.yaml의 명칭을 단일 진실로 사용한다. 본 문서 전반의 표기를 `exclusions_by_op_state` 등 config와 동일하게 맞춘다.
  - 검증 불변식: 모든 `op_specs[op_name].durations`는 base가 정의한 모든 상태 키를 포함해야 하며, 음수 duration은 금지되고 `bus`는 불리언이어야 한다.

### 5.3 Scheduler
- 역할: event_hook으로 현재 시각을 진전시키고 `Proposer` 호출. `Validator` 통과한 operation을 timeline에 schedule하고 `ResourceManager`를 통해 state update
- 중요: operation 예약 시 PHASE_HOOK 등록. 각 operation의 state마다 다양한 timing에 operation이 제안되도록 drive
- event_hook
  - 데이터: time, hook_name, payload
  - 결정적 시간 샘플링(윈도잉)
    - 전역 시뮬레이션은 이산 이벤트 훅으로만 시간 전진. 동일 시각(time)의 훅 집합을 하나의 결정적 틱으로 간주한다.
    - 훅 실행 시 `[t, t+admission_window)` 구간을 제안 윈도우로 사용해 슬롯을 탐색한다. 기본값은 `CFG[policies][admission_window_us]`로 정의하며, 0이면 비활성(윈도잉 미사용), 0보다 크면 활성화한다.
    - 무충돌 슬롯 탐색: `ResourceManager`의 배제 윈도우/IO_bus 점유/래치 상태를 질의하여 윈도우 내 가장 이른 feasible time을 선택한다. feasible time이 없으면 해당 훅에서는 스킵(no-op)한다.
    - 예외(즉시예약): `CFG[op_specs][op_name][instant_resv]=true`인 operation은 admission window 상한을 무시한다. Proposer가 now 이후 earliest feasible 시각을 제안하면 Scheduler는 이를 수락할 수 있다(동일 틱 원자성 준수, 검증 통과 조건 하).
    - 동일 틱 내 부분 스케줄 금지: 하나의 훅 처리 중 일부만 성공/일부 실패 상태로 분할 예약하지 않는다. 시퀀스 제안은 전부 수락되거나 전부 거절된다.
  - RNG 분기(재현성)
    - 실행 시작 시 전역 시드 고정. 각 훅은 `(global_seed, hook_counter)` 기반 스트림으로 분기하여 독립적이고 결정적인 난수 시퀀스를 사용한다. 시스템 시간은 사용하지 않는다.
  - QUEUE_REFILL
    - 목적: bootstrap 및 propose 실패 대비. `CFG[policies][queue_refill_period_us]` 주기로 생성
    - 데이터: `time, 'QUEUE_REFILL', None`
  - PHASE_HOOK
    - 목적: 스케쥴된 operation이 timeline에 등록될 때 해당 operation의 모든 state에서 다른 operation input 유도
    - 데이터: time, 'PHASE_HOOK', payload
    - payload: time, hook.die, hook.plane, 기타 operation 정보
    - 생성 시점: state duration 끝나기 전/후 각각 생성. 각 time은 random
    - 주의: ISSUE state는 bus 제약으로 모든 operation이 거절되므로 PHASE_HOOK 생성하지 않음
  - OP_START
    - 목적: operation 시작 시점에 hook 생성, console에 시작 로그 출력
    - 데이터: time, 'OP_START', operation
  - OP_END
    - 목적: operation 종료 시점에 hook 생성, `ResourceManager` 값 commit 및 종료 로그 출력
    - 데이터: time, 'OP_END', operation
- 종료 조건 및 종료 루틴
  - run 종료 시점은 `run_until`로 결정. 해당 시간이 지나면 종료 루틴 실행
  - `run_until` 이후에도 종료되지 않은 operation은 지속시키되, 그 시간 동안 `Proposer`는 propose 금지(`on_termination_routine` flag enable)
  - 모든 operation 종료 후 `Required Outputs`, `Success Metrics` 출력
  - `ResourceManager` 상태 값 snapshot을 `state_snapshot_yymmdd_0000001.csv`와 유사한 형태로 저장. 목적: `run_until`을 일정 크기로 유지, 이전 run 상태 load로 연속성 확보. run index 증가시키며 snapshot 저장
- bootsrap
  - 초기 program/read 기아 및 특정 block 에 program/read 가 몰리는 현상을 방지하기 위해 erase/program/read 를 최우선순위로 미리 예약되게끔 하는 기능
  - 조건
    - erase/program/read/dout 는 모두 multi-plane 동작으로, multi-plane 동작 초기 기아 방지. dout 은 read 된 target 에 대한 모든 plane_set 에 대해 진행
    - erase 를 전체 die 의 모든 block 중 몇 퍼센트의 비율로 erase 해둘 것인지, 어떤 celltype 으로 할 것인지 설정
    - program 을 erase 된 block 중 모든 page 에서 몇 퍼센트의 비율로 program 해둘 것인지, 어떤 celltype 으로 할 것인지 설정
    - read 를 program 된 page 중 몇 퍼센트의 비율로 read 해둘 것인지, 어떤 celltype 으로 할 것인지 설정
  - bootstrap 은 num_runs 이 2 보다 큰 상황에서 첫 번째 run 에서만 적용한다
  - bootstrap 이 시작되면 runtime 으로 propose 불허하고, bootstrap 으로 예약된 operation 이 모두 종료되면 bootstrap 을 disable 시켜 runtime propose 를 허용한다.

- attributes
  - `ResourceManager`
  - `Proposer`
  - `run_until`: run 당 시뮬레이션 시간
  - `num_runs`: 실행 횟수 (대략 10만 번까지 생성 예정)
  - enable_bootstrap: bootstrap 적용할지 여부
  

### 5.4 Proposer
- 역할: op_state에 따라 어떤 operation을 어느 time에 schedule 제안할지 확률적으로 샘플링 (random seed 고정으로 재현성)
- op_state_probs: 초기화 시 `CFG[phase_conditional]`을 읽어 채움 (global fixed seed)
- 중요: operation sequence 제안
  - 단일 operation 또는 sequence 형태로 제안 가능
    - 예: read→dout, cache_program/cache_read, oneshot_program_lsb→oneshot_program_csb→oneshot_program_execution_msb, set_feature→get_feature, etc.
  - sequence 확률과 생성 규칙: `CFG[op_bases][op_base][sequence]`, `CFG[generate_seq_rules]`
  - 순서 보장 및 순차 스케쥴. validity에 영향 없는 operation은 sequence 중간 스케쥴 가능
  - 제안 전, sequence 전체를 가상 추가해 `Validator`로 validity 확인
  - sequence 내 각 operation은 우선 예약. 종료 예상 시간 기반 예약 시간 산정, 해당 시점의 event_hook을 생성하도록 `Scheduler`에 전달
  - sequence 일부 누락 없이 무조건 실행되어야 함
  - 즉시예약(instant_resv): `op_specs[op_name].instant_resv=true`인 경우, Proposer는 admission window 상한을 적용하지 않고 now 이후 earliest feasible 시각으로 예약을 제안한다.
- `ResourceManager`로부터 state 참조해 특정 operation 제외 처리 후 최종 operation 및 필요 시 target address 선정
- Workflow (샘플링 단계)
  1) `Scheduler`의 event_hook이 `Proposer.propose`를 호출하고 payload 전달
  2) 현재 시각 기준 `ResourceManager.op_state_timeline`으로 op_state 확인
  3) `phase_conditional[op_state]`로 (operation, prob) 후보 list 생성하고, `exclusions_by_*`(op_state/latch/suspend/odt/cache) 기반 금지 operation을 제거한다.
  4) cache_state 고려: `ResourceManager.cache_state` 참조로 celltype 기반 제외. cache 진행 중에는 cache end 전까지 target plane(for cache_read)/die(for cache_program)에 대해 동일 celltype의 후속 cache_read/cache_program만 허용
  5) 남은 (operation, prob) 확률 정규화 후 후보 샘플링. 모든 난수는 훅별 RNG 스트림으로 생성한다. 후보가 비면 해당 훅은 no-op으로 종료한다.
  6) erase/program/read가 후보라면 `AddressManager.from_topology(topology)`로 초기화된 `AddressManager`를 통해 target address 샘플링. `CFG[op_specs][op_name][multi]=true`면 `CFG[policies][maxplanes]`로 plane_set 조합 생성 후 address 샘플링. 실패 시 plane_set 최소 크기 2까지 축소 시도. 그래도 없으면 남은 후보 중 `CFG[policies][maxtry_candidate]` 횟수만큼 비복원 샘플링
  7) 샘플링된 op_name에 `CFG[op_specs][op_name][sequence]`가 존재하면 `sequence[probs]`로 샘플링하여 후속 operation 생성을 위한 "sequence 생성 루틴" 실행
     - sequence 생성 루틴
       1. 선택된 `sequence[probs]`의 key를 '.'로 split 후 두 번째 원소가 'SEQ'가 아니면, `CFG[groups_by_base][key]` 후보를 uniform 확률로 샘플링하여 후속 operation 선택. `CFG[op_specs][op_name][sequence][inherit][key]` 규칙을 반영해 최종 operation sequence 생성. 규칙은 "후속 operation inherit 생성 규칙"에 따름
          - 'inc_page': 직전 operation의 page address에서 +1 (`AddressManager` 샘플링 시 `sequential=true`)
          - 'same_page': 직전 operation의 page address와 동일
          - 'pgm_same_page': 직전 program operation의 page address와 동일
          - 'same_celltype': 직전 operation의 celltype 동일
          - 'multi': 직전 operation이 multi-plane read였다면 해당 plane_set 모두에 DOUT 추가
          - 'same_page_from_program_suspend': RECOVERY_READ 시에 직전에 입력됐던 ResourceManager.suspended_ops 의 target address 를 그대로 상속
          - sequence 내 operation 간 time 간격: `CFG[policies][sequence_gap]`
       2. 선택된 `sequence[probs]`의 key를 '.'로 split 후 두 번째 원소가 'SEQ'라면, `CFG[generate_seq_rules][key][sequences]` 원소들을 모두 합쳐 operation sequence 생성. 각 operation 별 생성 규칙은 `CFG[generate_seq_rules][key][op_base]`의 "inherit 생성 규칙" 사용
  8) 사전 검증과 재시도: 동일 슬롯(윈도우) 내 가안 배치를 구성해 `Validator` 체크(epr_dependencies, IO_bus_overlap, exclusion_window_violation, 래치 락, ODT/피처 상태)를 수행한다. 실패 시 `CFG[policies][maxtry_candidate]` 한도 내에서 대안 op_name/주소 조합을 재시도한다. 동일 틱 내 부분 스케줄은 금지한다.
  9)  검증 통과 시 operation/time을 `Scheduler`에 반환하여 예약
- attributes
  - `CFG`
  - `op_state_probs`
  - 결정성: 동일 `config.yaml`, 전역 시드, 동일 초기 스냅샷이면 결과는 동일해야 한다. 모든 난수는 시드된 RNG를 통해서만 발생하며 시스템 시간은 사용하지 않는다.

### 5.5 ResourceManager
- 참조: `NAND_BASICS_N_RULES.md`의 Resources & Rules
- 관리 대상: `addr_state`, `addr_mode_erase`, `addr_mode_pgm`, `IO_bus`, `exclusion_windows`, `latches`, `op_state_timeline`, `suspend_states`, `odt_state`, `cache_state`, `etc_states`, `ongoing_ops`, `suspended_ops`
- 역할: resource의 현재/미래 시점 상태 관리
- 주의: `AddressManager`의 `addr_state`, `addr_mode_erase`, `addr_mode_pgm`는 현재 시점 참조에 사용. 미래 시점 값은 별도 관리
- state_timeline 관리 방법
  - operation 스케쥴 시 operation의 모든 `logic_state`를 `op_state_timeline`에 등록하고, 추가로 `op_name.END` state를 마지막에 end_time='inf'로 추가
  - 목적: "달성 지표" 중 `op_state x op_name x input_time` 다양성 확보
  - 예외 operation
    - SUSPEND→RESUME
      - workflow (ERASE_SUSPEND 예시, PROGRAM_SUSPEND도 동일 적용)
        1) ERASE 동작이 `Scheduler`에 의해 예약됨. 이때 `ongoing_ops` 배열에 해당 operation을 복사하여 추가
        2) `ERASE.CORE_BUSY` 중 SUSPEND 동작이 `time_suspend` 시각에 등록됨
        3) `ResourceManager`가 `op_state_timeline`의 `ERASE.CORE_BUSY` 상태를 `time_suspend` 이후부터 제거하고, `ERASE_SUSPEND` 스케쥴을 등록. `suspended_ops`에 기존 ERASE를 추가하고 `ongoing_ops`에서 제거. `suspend_states`를 'erase_suspended'로 변경
           - 규칙: ERASE_SUSPEND/PROGRAM_SUSPEND/ERASE_RESUME/PROGRAM_SUSPEND는 END state를 별도로 추가하지 않음 → 예외 루틴 처리
        4) 이후 ERASE_RESUME 예약 시, `Scheduler`는 RESUME 동작으로 인지하여 별도 루틴으로 처리: `ERASE_RESUME.CORE_BUSY`를 timeline에 예약하고, `suspended_ops`에 있던 operation을 `ERASE_RESUME` 종료 직후에 추가
    - RESET/RESET_LUN 스케쥴 시: RESET의 경우 모든 die의 동작 및 state 초기화
- attributes
  - `CFG`
  - `addr_state`: (die,block) 별 현재 data 기록 상태. 예) -2: BAD, -1: ERASE, 0~pagesize-1: last_pgmed_page_address
  - `addr_mode_erase`: (die,block) 별 erase 시 celltype
  - `addr_mode_pgm`: (die,block) 별 program 시 celltype
  - `IO_bus`: ISSUE timeline 등록
  - `exclusion_windows`: `CFG[op_specs][op_name][multi]`를 참조.
    -  die level에서 single×multi, multi×multi overlap 금지 구간 관리.
    -  singlexsingle 이 허용되는 op_base 는 PLANE_READ/PLANE_READ4K/PLANE_CACHE_READ 이다.
  - `latches`
    - 집행 원칙: `ResourceManager`는 활성 래치를 `CFG[exclusions_by_latch_state]` → `CFG[exclusion_groups]`로 해석해 금지 op를 판단한다. 하드코딩 예외(DOUT/SR 허용 등)는 사용하지 않으며, 허용/금지는 전적으로 config 그룹 정의에 따른다.
    - 스코프: 래치는 plane 단위로 관리한다(READ/PROGRAM 모두 plane-target 단위). 프로그램 계열 래치는 die-wide 대상이더라도 동일 die의 모든 plane에 plane 단위로 등록하여 집행한다.
    - 생성 트리거
      - READ 계열: READ/READ4K/PLANE_READ/PLANE_READ4K/CACHE_READ/PLANE_CACHE_READ/COPYBACK_READ 완료 시 대상 plane에 `LATCH_ON_READ` 설정 → `exclusion_groups.after_read` 집행
      - PROGRAM 계열(ONESHOT): LSB/CSB/MSB 완료 시 해당 die의 모든 plane에 각각 `LATCH_ON_LSB`/`LATCH_ON_CSB`/`LATCH_ON_MSB` 설정
    - 해제 조건(API)
      - READ 계열: DOUT 종료 시 대상 plane 해제 → `ResourceManager.release_on_dout_end(targets, now)`
      - PROGRAM 계열: ONESHOT_PROGRAM_MSB_23h 또는 ONESHOT_PROGRAM_EXEC_MSB 종료 시 해당 die 전체 해제 → `ResourceManager.release_on_exec_msb_end(die, now)`
  - `op_state_timeline`: (die,plane) target. logic_state timeline 등록. state에 따른 `exclusions_by_op_state` list
  - `suspend_states`: (die) target. suspend_states on/off 등록. state에 따른 `exclusions_by_suspend_state` list
  - `odt_state`: on/off 등록. state에 따른 `exclusions_by_odt_state` list
  - `cache_state`: (die,plane) target. cache_read/cache_program 진행 중인지 관리
  - `etc_states`: 현재 미사용 (추후 관리)
  - `ongoing_ops`: (die) target. erase/program 시작 시 보관
  - `suspended_ops`: (die) target. erase_suspend/program_suspend/nested_suspend에 의해 중단된 operation 정보 저장. resume 시 필요한 resource 등록으로 복구 (예: logic_state timeline은 suspend 시점 이후 state 정보 저장 후 삭제, resume 시 복구 등록)

  - 스냅샷/재개 연동
    - 스냅샷 시: `op_state_timeline`의 각 (die,plane) 꼬리 상태와 종료 시각, `suspend_states`, `odt_state`, `cache_state`, 활성 `exclusion_windows`, `ongoing_ops`/`suspended_ops` 메타데이터를 저장한다.
    - 재개 시: 위 상태를 로드하여 타임라인과 윈도우·락들의 일관성을 검증한 뒤, `ongoing_ops`/`suspended_ops`를 기준으로 후속 스케줄 복구를 준비한다.

### 5.6 AddressManager
- 구현: `addrman.py`
- 참조: `NAND_BASICS_N_RULES.md`의 addr_state, addr_mode_erase/addr_mode_pgm: erase/program/read address dependencies
- 역할: op_base, cell_mode를 입력받아 제약을 만족하는 address 후보 제안
- attributes
  - `CFG`
  - `num_dies, num_planes, num_blocks, pagesize`: topology parameters
  - `addr_state`: (die,block) 별 BAD/ERASE/last_pgmed_page
  - `addr_mode_erase`, `addr_mode_pgm`: (die,block) 별 erase/program 시 celltype
  - `badlist`: (die,block) 중 badblock으로 mark된 주소 list (사용 금지)
  - `offset`: read 가능한 page 샘플링 시 last_pgmed_page 기준 아래로 금지할 범위
 - 초기화 유틸리티
   - `AddressManager.from_topology(topology)`: `config.yaml`의 topology로부터 파생된 표준 생성자. `pagesize`/plane 수 일관성을 보장한다.

 - 스냅샷/재개 연동
   - 스냅샷 시: `addr_state`, `addr_mode_erase`, `addr_mode_pgm`을 저장(대형일 경우 `.npy` 사이드카 사용 가능)
   - 재개 시: 저장된 상태를 로드하여 동일한 토폴로지에서 동등하게 복구한다.

### 5.7 Validator
- 역할: `Proposer`가 제안한 operation/target address/time을 schedule해도 문제 없는지 rule 체크
- 기반: `NAND_BASICS_N_RULES.md` 내용을 바탕으로 조건식 rule을 사전 구축해 사용
- validation 항목
  - epr_dependencies
    - reserved_at_past_addr_state: 예약된 addr_state 변화 값이 아닌 과거 값 기반 예약 금지
    - program_before_erase: addr_state(die,block)=-1 이 아닌 block 에 program 금지
    - read_before_program_with_offset_guard: 동일 (die,block) 내 (addr_state-offset_guard) 보다 같거나 작은 page 에만 read 할 수 있다.
    - programs_on_same_page: 동일한 page 에 두 번 이상 program 금지
    - different_celltypes_on_same_block
      - 동일 (die,block) 내에는 동일한 celltype 으로 program/read 해야한다
      - 동일 (die,block) 내에 program 할 때는 TLC, AESLC, FWSLC erase 된 곳에는 동일하게, SLC erase 된 곳은 SLC, A0SLC, ACSLC 로만 program 해야한다.
  - IO_bus_overlap: (AddressManager 에 기능 구현)
  - exclusion_window_violation (AddressManager 에 기능 구현)
  - forbidden_operations_on_latch_lock: latch lock 의 종류에 따라 CFG[exclusions_by_latch_state[latch_state]] 에 명시된 op_base 금지
  - logic_state_overlap: (AddressManger 에 기능 구현)
  - forbidden_operations_on_suspend_state: suspend_state 의 종류에 따라 CFG[exclusions_by_suspend_state[suspend_state]] 에 명시된 op_base 금지
  - operation_on_odt_disable: odt_state 에 따라 CFG[exclusions_by_odt_state][odt_state] 에 명시된 op_base 금지
  - forbidden_operations_on_cache_state: cache_state 의 종류에 따라 CFG[exclusions_by_cache_state[cache_state]] 에 명시된 op_base 금지
- attributes
  - `CFG`
  - `ResourceManager`

## 6. Workflow (draft)
- 초기화: `Proposer`, `ResourceManager`, `AddressManager`, `Validator`, `Scheduler` 인스턴스 생성
- runtime 정의 후 scheduler runtime 실행
  - `Scheduler`가 초기 `event_hook` 생성 후 heappush
  - while loop으로 event_hook을 heappop하여 `Proposer` 호출, 새로운 operation 스케쥴
 - 스냅샷/재개: 정책에 따라 주기적 또는 체크포인트 시점에 스냅샷을 생성한다. 재개 시에는 3.6의 절차에 따라 불변식 검증 후 동일 RNG 상태에서 실행을 이어간다.

## 7. Unit Tests

- 목표: 스케줄러/프로포저/리소스 매니저/배제 규칙/주소 제약을 포괄하는 결정적·격리된 테스트를 제공한다.

### 7.1 시드와 결정성
- 전역 RNG 시드를 테스트별로 고정한다. 시스템 시간은 사용하지 않고 시뮬레이션 시간만 사용한다. 동일 `config.yaml`/전역 시드/동일 초기 스냅샷이면 동일 결과가 생성되어야 한다(5.4, 3.6 참조).

### 7.2 주소 규칙 검증
- program_before_erase: 지워지지 않은 블록에 program 시 검증 실패.
- read_before_program: 오프셋 이하 또는 미프로그램 페이지 읽기 시 실패.
- programs_on_same_page: 동일 블록 동일 페이지 중복 program 탐지.
- different_celltypes_on_same_block: 블록 시퀀스 내 모드 일관성 강제.

### 7.3 버스/타임라인 중첩
- IO_bus_overlap: 동일 다이에서 ISSUE 버스를 쓰는 두 오퍼레이션 동시 예약 시 거절됨을 확인.
- logic_state_overlap: 동일 (die, plane)에서 `op_state_timeline`의 중첩이 없음을 보장.

### 7.4 배제 윈도우 및 래치 전이
- exclusion_windows: single×multi, multi×multi 상호 배제를 스펙대로 검증(5.5, 5.7 참조).
- 래치 전이: READ 이후 래치 락 동안 금지 오퍼레이션이 거절됨을 확인(`latches`와 `exclusions_by_*`).

### 7.5 서스펜드/리줌
- ERASE_SUSPEND/ERASE_RESUME: ERASE 중단 후 재개 시 타임라인 복원이 정확한지 검증.
- PROGRAM_SUSPEND/PROGRAM_RESUME: 중단 시 제거됐던 꼬리 state가 재개 시 복구되는지 확인.
- suspend→resume 후 중단된 erase/program이 재스케쥴되고 `op_state_timeline`이 의도대로 갱신되는지 확인.

### 7.6 스냅샷 일관성
- 스냅샷 저장 → 재시작 → 동일 RNG 상태에서 동일한 후속 시퀀스를 생성하는지 확인(3.6, 6 참조).
- 사이드카(`.npy`) 사용 시 `addr_state`/`addr_mode_*` 등 대형 배열 라운드 트립 무결성 검증.

### 7.7 출력 검증
- operation_sequence CSV 스키마가 3.1 정의와 일치하는지 검증.
- 3.1.1의 CSV Payload JSON 인코딩 정책에 따라 JSON → CSV 기록 → CSV 읽기 → `json.loads` 라운드 트립이 원본과 동일함을 검증(골든 샘플 포함).

### 7.8 배제 규칙 소스별 검증
- exclusions_by_op_state: (die,plane)
- exclusions_by_latch_state: (die,plane)
- exclusions_by_suspend_state: (die)
- exclusions_by_odt_state: global
- exclusions_by_cache_state: (die,plane)

## 8. Open Questions (draft)
- 중요: 예약된 operation과 runtime으로 제안된 operation의 우선 순위 조정 메커니즘?
  - 대안1: 단일 rail timeline에서 runtime 제안 operation이 예약된 operation들의 validity를 깨뜨리지 않으면 스케쥴 가능

## 9. Risks (draft)
- operation sequence 제안이 계속 거절될 수 있음
