# nandsim_demo.py

## 목적
- NAND device 를 다양한 시나리오에서 평가하기 위한 다양한 operation sequence 를 생성한다.
- sequence 의 생성은 NAND 내부 state 에 따라 선택 확률 값을 정의해 샘플링하는 방식을 사용하여 미세조정 가능한 시스템을 만든다.
- 생성된 sequence 의 아웃풋 형태를 디자인하여 다음 단계인 ATE(Automatic Test Environment) 가 사용가능한 형태로 만든다.

## 용어
- op_id: operation 을 schedule 할 때 생기는 고유값. log tracing 을 위해 사용
- op_name: operation 이름
- op_base: operation 종류
- payload: operation 을 수행하기 위해 필요한 parameters 로 operation 마다 형태가 다름
- source: operation 생성과정의 출처
- op_state_phase: 어떤 operation 동작 중 어떤 state 의 timeline 에서 operation 이 schedule 됐는지의 정보. 이 정보에 따라 가능/불가능 CMD 입력이 달라진다.
- cell_type: TLC/SLC/A0SLC/ACSLC/AESLC/FWSLC
- lane: gantt 차트를 그리기 위해 die/block 을 변수 하나의 형태로 표현한 값

## 필수 아웃풋

### operation sequence
- 목적: 생성된 sequence 를 실제 device test 가능한 file 의 형태로 바꾸기 위한 궁극적인 결과물
- 파일형태: csv
- 필수 field: seq,time,op_id,op_name,payload
- payload 는 op_name 에 따라 다르며, 이것을 사용자가 정의하고 관리할 수 있어야 한다.
- 예시
  - seq,time,op_id,op_name,op_uid,payload
  - 5,101.0,4,SIN_PROGRAM,"[{""die"":0,""pl"":0,""block"":0,""page"":0}]"
  - 17,301.0,3,MUL_ERASE,"[{""die"":0,""pl"":0,""block"":4,""page"":0},{""die"":0,""pl"":1,""block"":1,""page"":0}]"
  - 6,121.0,8,SR,"{""die"":0,""pl"":0,""block"":0,""page"":0}"

### address touch count
- 다양한 address 에 operation 이 수행됐는지 알아보기 위한 데이터
- 파일형태: csv
- 필수 field: op_base,cell_type,die/block,page,count
- address(die,block,page) 별 program, read 동작 수행 횟수
- 시각화: heatmap

### `operation timeline`
- operation 의 다양성과, operation 간의 충돌이 존재하는지 확인하기 위한 데이터
- 파일형태: csv
- 필수 field: start,end,die,plane,block,page,op_name,op_base,source,op_uid,op_state
- (die,block) 별 operation timeline
- 시각화: gantt 차트

### `op_state timeline`
- op_state 에서 허용되지 않는 operation 이 수행됐는지, plane/die level 에서 충돌은 없는지 확인하기 위한 데이터
- 파일형태: csv
- 필수 field: start,end,die,plane,op_state,lane,op_name,duration
- (die,plane) 별 op_state_phase timeline
- 시각화: gantt 차트

### `op_state x op_name x input_time count`
- 다양한 op_state 에서 다양한 operation 가 다양한 시점에서 실제로 수행되는지 확인하기 위한 데이터. 실제 operation 이 propose 될 때 참조한 op_state 를 사용
- 파일형태: csv
- 필수 field: op_state,op_name,input_time,count
- 어떤 op_state 에서 operation 이 어느 time 에서 스케쥴 됐는지 나타냄.
- input_time: 0~1 사이의 소수. op_state 의 전체 duration 에서 어느 시점에서 operation 이 schedule 됐는지 나타냄.
- 시각화: histogram

## `달성 지표`
- `op_state x op_name x input_time count` 의 고른 분포가 실질적인 지표 -> 정량적인 지표 필요
- sequence 평균 생성 속도 >= 2000개/초
- operation sequence 의 validation check 모두 통과

## 구성 요소 (draft)

### `CFG`
- `config.yaml` 파일을 읽어와서 만든다
- topology; device 기본 구조 parameter
- celltypes: 지원하는 celltype 종류
- op_bases: base operation 별 parameter
  - scope: operation 을 어느 level 로 예약할 지 정함
  - affect_state: operation 이 예약되면 예약 이후 시점의 state 가 바뀌어야 하는지 나타냄
  - sequence: operation 제안 시 sequence 의 형태로 제안할 경우 가능한 case 선언
    - probs: 각 case 별 확률
    - inherit: 후속 operation 의 상속 조건 명시
- maxplanes: multi-plane 동작 시 시도 가능한 최대 plane 갯수
- seq_maxloop: sequence 생성 시 최대 길이
- generate_seq_rules: sequence 생성 시 규칙
- latencies: operation 별 state 의 latency 값
- op_names: operation 별 parameter
- phase_conditional: op_state 별 operation 샘플링 확률 정의 (autofill 필요)
- **중요**: runtime 으로 만들어야 하는 항목
  - op_specs: op_bases 의 format 을 상속하고 값은 op_names 를 참고하여 operation 별 parameter 를 생성
    - scope, affect_state, sequence, states:: op_base 에서 상속
    - states['state_name'][duration]: op_names['op_name']['durations']['state_name'] 의 값으로 대체
  - groups_by_base: base 가 동일한 op_name 들을 list 로 만들고, `Proposer`, `Validator` 에서 사용
  - phase_conditional
    1. op_specs 의 key 값인 op_name 과 states 를 결합하여 만든 모든 op_name.state 생성 후 이것을 phase_conditional 의 기본 key 값으로 사용.
    2. op_name.state 의 하위 key 값은 CFG[op_names]의 모든 op_name 을 기본으로 이 중에서 CFG[exclusion_groups][CFG[exclusions_by_state[op_name.state]]] 인 op_base 의 값으로 후보들을 제외함(groups_by_base 활용).
    3. 남은 후보들 중 특별히 가중치를 설정해야 할 operation 들은 `config.yaml` 파일에 phase_conditional_overrides 로 명시하여 override 한다(e.g RESET 등). 이 때 exclusion 으로 제외된 op_name 들은 override 에서도 제외시킨다.
    4. override 하지 않은 후보들의 확률은 random sample 으로 전체 확률값의 합이 1 로 만든다
    5. 이 방식으로 초기 확률값을 만들어 `op_state_probs.yaml` 파일에 저장하고, 추후 필요시 값들을 수동으로 미세조정한다.

### `Proposer`
- op_state 에 따라 어떤 operation 을 어느 time 에 스케쥴링 제안을 할지 확률적으로 샘플링 한다.
- time 의 샘플링은 현재 시각 기준 특정 window 안에 plane/die wide 하게 schedule 이 비어 있는 곳을 찾아 선정한다.
- time 은 결정적, operation 은 확률적으로 샘플링.
- op_state_probs: op_state 에 따른 operation 의 확률값은 CFG[phase_conditional] key 값을 읽어와서 채운다.
- **중요**: operation sequence 제안
  - 단일한 operation 을 제안할 수도 있고, operation sequence 의 형태로 제안할 수도 있다: e.g) read->dout, cache_program/cache_read, oneshot_program_lsb->oneshot_program_csb->oneshot_program_execution_msb, set_feature->get_feature, etc.
  - sequence 의 형태로 제안하는 확률과 생성 규칙은 별도의 파일에 정의한다: CFG[op_base][sequence], CFG[generate_seq_rules]
  - sequence 의 순서는 보장되어야 하며 순차적으로 스케쥴링 된다. 하지만 validity check 를 유발하지 않는 operation 은 sequence 중간에 스케쥴링 가능하다.
  - sequence 를 제안할 때는 우선 sequence 내 모든 operation 을 가상으로 추가했을 때 validity 를 만족하는지 `Validator` 를 통해서 확인하고 나서 제안한다.
  - sequence 를 구성하는 각 operation 은 schedule 에 우선 예약되어야 하며, 각 operation 마다 끝나는 시간을 예상해서 예약 시간을 정하고, event_hook 을 그 시점에 맞춰 생성할 수 있도록 `Scheduler` 에 정보를 전달한다.
  - sequence 중 일부가 스케쥴에서 누락되어서는 안되며, 무조건 실행되어야 한다.
- `ResourceManager` 에 요청하여 각종 state 값을 참조하여, 특정 operation 은 제외하고, 최종적으로 operation, target address(필요 시) 를 선정한다.
- 샘플링 단계:
  1. 현재 시각 기준 후보 time 선정
  2. op_state_probs 참조 후 확률 값 0 인 후보 제외
  3. 제안하는 time 시점에서의 op_state_timeline 값을 확인 후 CFG[exclusion_groups][CFG[exclusions_by_state][op_state]] 를 참조하여 operation 종류 제외
  4. cache_state 에 대한 고려: `ResourceManager` 의 cache_state 를 참조해 celltype 으로 operation 제외: cache operation 중일 경우 cache end 전까지 target plane(for cache_read), die(for cache_program) 에 대해 동일한 celltype 의 후속 cache_read/cache_program 이 동작되어야 한다.
  5. operation 선정
  6. erase/program/read 가 후보라면 `ResourceManager` 에 요청하여 target address 샘플링.
- attributes
  - `CFG`
  - op_state_probs

### `ResourceManager`
- `NAND_BASICS_N_RULES.md` Resources & Rules 항목 참조
- 관리 대상: addr_state, addr_mode, IO_bus, exclusion_windows, latches, op_state_timeline, suspend_states, odt_state, cache_state, etc_states
- resource 현재 또는 미래 시점의 값의 상태를 관리한다.
- `AddressManager` 의 addr_state, addr_mode 값은 현재 시점을 참조할 때 사용하고, 미래 시점의 값은 별도로 관리한다.
- state_timeline 관리 방법
  - operation 이 스케쥴 될 때, operation 의 모든 logic_state 를 op_state_timeline 에 등록하고, 추가로 op_name.END state 를 마지막에 추가한다. end_time 은 'inf' 로 등록한다. 이렇게 하는 목적은 `달성 지표` 중 op_state x op_name x input_time 의 다양성을 확보하기 위해서이다
  - SUSPEND->RESUME
    - ERASE_SUSPEND/PROGRAM_SUSPEND/ERASE_RESUME/PROGRAM_SUSPEND operation 은 END state 를 추가하지 않는다
    - resume 시에 이전에 중단됐던 operation 의 state 중 마저 진행하지 못했던 state 중 ISSUE 를 제외한, CORE_BUSY 와 END state 를 다시 추가한다.
  - RESET/RESET_LUN 동작이 스케쥴되면, RESET 의 경우 모든 die 에 대한 동작 및 state 가 초기화 된다.
- attributes
  - `CFG`
  - addr_state: (die,block) 마다 현재 data 기록 상태 저장. e.g) -2:BAD, -1: ERASE, 0~pagesize-1: last_pgmed_page_address
  - addr_mode: (die,block) 마다 erase 할 떄 celltype 등록
  - IO_bus: ISSUE timeline 등록.
  - exclusion_windows: CFG[op_specs][op_name][multi] 참조하여 die level 에서 single x multi, multi x multi 가 overlap 될 수 없게 금지 구간 등록 관리
  - latches
    - read: (die, plane) target, program: die-wide target
    - read/oneshot_program_lsb/oneshot_program_csb/oneshot_program_msb 진행 직후 특정 latch lock 및 금지되는 exclusion_group 존재
      - read/cache_read 완료 후 cache_latch lock: CFG[exclusion_groups][after_read]
      - oneshot_program_lsb 완료 후 lsb_latch lock: CFG[exclusion_groups][after_program_lsb] 로 operation 금지
      - oneshot_program_csb 완료 후 csb_latch lock: CFG[exclusion_groups][after_program_csb] 로 operation 금지
      - oneshot_program_msb 완료 후 msb_latch lock: CFG[exclusion_groups][after_program_msb] 로 operation 금지 
  - op_state_timeline: (die,plane) target. logic_state timeline 등록. state 에 따른 exclusions_by_op_state 에 list
  - suspend_states: (die) target. suspend_states on/off 등록. suspend_states 에 따른 exclusions_by_suspend_state 에 list
  - odt_state: odt_state on/off 등록. odt_state 에 따른 exclusions_by_odt_state 에 list
  - cache_state: (die,plane) target. cache_read, cache_program 이 진행 중인지 관리.
  - etc_states: 현재 미사용. 추후 관리
  - suspended_ops: erase_suspend/program_suspend/nested_suspend 시에 중단됐던 operation 의 정보를 저장. 이후 resume 시에 재개 시 필요한 resource 들에 등록하여 복구한다. 예를 들어, logic_state timeline 의 경우 suspend 시 그 시점 이후의 operation 의 state 정보는 저장 후 삭제하고, resume 시 정보를 복구해서 등록한다

### `AddressManager`
- `addrman.py` 에 이미 구현됨
- `NAND_BASICS_N_RULES.md` addr_state, addr_mode: erase/program/read address dependencies 항목 참조
- op_base,cell_mode 를 인자로 받아서, erase/program/read address dependencies 제약을 모두 만족하는 address 후보를 제안함
- attributes
  - `CFG`
  - num_dies, num_planes, num_blocks, pagesize: topology paremeters
  - addr_state: (die,block) 별 BAD/ERASE/last_pgmed_page 를 나타낸다.
  - addr_mode: (die,block) 별 celltype
  - badlist: (die,block) 중 badblock 으로 mark 해서 사용되지 말아야할 주소 list
  - offset: read 가능한 page 를 샘플링 할 때 last_pgmed_page 값에서 아래로 얼마만큼 read 를 금지할 것인지 나타냄

### `Validator`
- `Proposer` 가 제안한 operation, target address, time 을 schedule 해도 문제 없는지 rule 을 체크한다.
- `NAND_BASICS_N_RULES.md` 의 내용을 기본으로 rule 을 조건식을 만들어 미리 저장해 두고 사용한다.
- validation 항목
  - epr_dependencies
    - reserved_at_past_addr_state: operation 예약 시 예약된 addr_state 변화 값이 아닌 과거 값을 기반으로 예약
    - program_before_erase
    - read_before_program
    - programs_on_same_page
    - read_page_on_offset_guard
    - different_celltypes_on_same_block
  - IO_bus_overlap
  - exclusion_window_violation
  - erase_program_on_latch_lock
  - logic_state_overlap
  - erase_on_erase_suspended: 
  - operation_on_odt_disable
  - erase_program_on_write_protect
- attributes
  - `CFG`
  - `ResourceManager`

### `Scheduler`
- event_hook 기반으로 현재 시각을 변경하고, `Proposer` 를 호출하여, `Validator` 의 통과가 된 operation 은 timeline 에 schedule 한다. 이 때, `ResourceManager` 를 통해 state 값을 update 한다.
- **중요**: operation 을 예약하면서 동시에 phase_hook 을 생성하여 heappush 하고 다음 턴에 heappop 으로 참조하여 동작 중인 operation 의 op_state, input_time 에 다양한 operation 이 스케쥴될 수 있게 drive 한다.
- 이전 erase/program/read 에 사용된 address 를 저장해두고, operation sequence 생성시에 이전 address 값을 참고한다.
- 시뮬레이션 시간을 정할 수 있으며, 시뮬레이션이 끝나면 `필수 아웃풋`, `달성 지표` 를 출력한다.
- 여러번 반복해서 돌릴 수 있어야 한다. 한 턴이 끝날때 마다 `ResourceManager` 의 state_timeline, bus 등의 snapshot 을 파일로 저장하고, 다음 턴에서 이어 받아 재개할 수 있도록 한다.
- attributes
  - `ResourceManager`
  - `Proposer`

## 워크 플로우 (draft)
- 초기화: `Proposer`, `ResourceManager`, `AddressManager`, `Validator`, `Scheduler` 인스턴스 생성
- runtime 정의 후 scheduler runtime 실행
  - `Scheduler` 에서 초기 event_hook 생성 후 heappush
  - while loop 으로 event_hook 을 heappop 하여 `Proposer` 를 호출하고, 새로운 operation 을 스케쥴한다

## 필수 Unit Test (draft)
- suspend->resume test: suspend->resume 후에 중단됐던 erase/program 이 다시 스케쥴되고, state_timeline 이 의도대로 변경되는지 검증
- operation exclusion 의 모든 단계가 제대로 동작하는지 검증
  - exclusions_by_op_state: (die,plane)
  - exclusions_by_latch_state: (die,plane)
  - exclusions_by_suspend_state: (die)
  - exclusions_by_odt_state: global
  - exclusions_by_cache_state: (die,plane)
- 그 외

## Open questions (draft)
- **중요**:예약된 operation 과 runtime 으로 제안된 operation 의 우선 순위 조정 메커니즘은 어떻게 할 것인가?
  - 대안1: 단일 rail timeline 으로 runtime 으로 제안된 operation 이 예약된 operation 들의 validity 를 깨뜨리지 않으면 스케쥴 가능하다.

## 예상 risks (draft)
- operation sequence 제안이 계속 거절될 수 있다.






