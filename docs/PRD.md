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
  - seq,time_op_id,op_name,op_uid,payload
  - 5,101.0,4,SIN_PROGRAM,"[{""die"":0,""pl"":0,""block"":0,""page"":0}]"
  - 17,301.0,3,MUL_ERASE,"[{""die"":0,""pl"":0,""block"":4,""page"":0},{""die"":0,""pl"":1,""block"":1,""page"":0}]"
  - 6,121.0,8,SR,"{""die"":0,""pl"":0,""block"":0,""page"":0}"

### address touch count
- 다양한 address 에 operation 이 수행됐는지 알아보기 위한 데이터
- 파일형태: csv
- 필수 field: op_base,cell_type,die/block,page,count
- address(die,block,page) 별 program, read 동작 수행 횟수
- 시각화: heatmap

### operation timeline
- operation 의 다양성과, operation 간의 충돌이 존재하는지 확인하기 위한 데이터
- 파일형태: csv
- 필수 field: start,end,die,plane,block,page,op_name,op_base,source,op_uid,op_state
- (die,block) 별 operation timeline
- 시각화: gantt 차트

### op_state timeline
- op_state 에서 허용되지 않는 operation 이 수행됐는지, plane/die level 에서 충돌은 없는지 확인하기 위한 데이터
- 파일형태: csv
- 필수 field: start,end,die,plane,op_state,lane,op_name,op_state,duration
- (die,plane) 별 op_state_phase timeline
- 시각화: gantt 차트

### op_state x op_name x input_time count
- 다양한 op_state 에서 다양한 operation 가 다양한 시점에서 실제로 수행되는지 확인하기 위한 데이터. 실제 operation 이 propose 될 때 참조한 op_state 를 사용
- 파일형태: csv
- 필수 field: op_state,op_name,input_time,count
- 어떤 op_state 에서 operation 이 어느 time 에서 스케쥴 됐는지 나타냄.
- input_time: 0~1 사이의 소수. op_state 의 전체 duration 에서 어느 시점에서 operation 이 schedule 됐는지 나타냄.
- 시각화: histogram

## 달성 지표
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
- max_planes: multi-plane 동작 시 시도 가능한 최대 plane 갯수
- seq_maxloop: sequence 생성 시 최대 길이
- generate_seq_rules: sequence 생성 시 규칙
- latencies: operation 별 state 의 latency 값
- op_names: operation 별 parameter
- phase_conditional: op_state 별 operation 샘플링 확률 정의 (autofill 필요)
- **중요**: runtime 으로 만들어야 하는 항목
  - op_specs: op_bases 의 format 을 상속하고 값은 op_names 를 참고하여 operation 별 parameter 를 생성
    - scope, affect_state, sequence, states:: op_base 에서 상속
    - states['state_name'][duration]: op_names['op_name']['duraions']['state_name'] 의 값으로 대체
  - groups_by_base: base 가 동일한 op_name 들을 list 로 만들고, `Proposer`, `Validator` 에서 사용
  - exclusions: `ResourceManager` 에서 exclusion_window 를 관리할 때 사용
    - op_name.state_name: exclusion_groups[op_bases['op_base']['state_name']['exclusion']] 값으로 채운다.

### `Proposer`
- op_state 에 따라 어떤 operation 을 어느 time 에 스케쥴링 제안을 할지 확률적으로 샘플링 한다.
- time 의 샘플링은 현재 시각 기준 특정 window 안에 plane/die wide 하게 schedule 이 비어 있는 곳을 찾아 선정한다.
- time 은 결정적, operation 은 확률적으로 샘플링.
- op_state_probs: op_state 에 따른 operation 의 확률값은 `CFG` -> phase_conditional key 값을 읽어와서 채운다.
- **중요**: operation sequence 제안
  - 단일한 operation 을 제안할 수도 있고, operation sequence 의 형태로 제안할 수도 있다: e.g) read->dout, cache_program/cache_read, oneshot_program_lsb->oneshot_program_csb->oneshot_program_execution_msb, set_feature->get_feature, etc.
  - sequence 의 형태로 제안하는 확률과 생성 규칙은 별도의 파일에 정의한다: `CFG` 의 op_base->sequence, generate_seq_rules
  - sequence 의 순서는 보장되어야 하며 순차적으로 스케쥴링 된다. 하지만 validity check 를 유발하지 않는 operation 은 sequence 중간에 스케쥴링 가능하다.
  - sequence 를 제안할 때는 우선 sequence 내 모든 operation 을 가상으로 추가했을 때 validity 를 만족하는지 `Validator` 를 통해서 확인하고 나서 제안한다.
  - sequence 를 구성하는 각 operation 은 schedule 에 우선 예약되어야 하며, 각 operation 마다 끝나는 시간을 예상해서 예약 시간을 정하고, event_hook 을 그 시점에 맞춰 생성할 수 있도록 `Scheduler` 에 정보를 전달한다.
- `ResourceManager` 에 요청하여 각종 state 값을 참조하여, 특정 operation 은 제외하고, 최종적으로 operation, target address(필요 시) 를 선정한다.
- 샘플링 순서: 현재 시각 기준 후보 time 선정 -> op_state_probs 참조 후 확률 값 0 인 후보 제외 -> `ResourceManager` 의 resource 값으로 operation 제외 -> operation 선정 -> erase/program/read 가 후보라면 target address 샘플링
- attributes
  - `CFG`
  - op_state_probs

### `ResourceManager`
- `NAND_BASICS_N_RULES.md` Resources & Rules 항목 참조
- 관리 대상: addr_state, addr_mode, IO_bus, exclusion_window, latches, logic_states, suspend_states, odt_state, write_protect_state, etc_states
- resource 현재 또는 미래 시점의 값의 상태를 관리한다.
- `AddressManager` 의 addr_state, addr_mode 값은 현재 시점을 참조할 때 사용하고, 미래 시점의 값은 별도로 관리한다.
- attribuets
  - `CFG`
  - addr_state, addr_mode, IO_bus, exclusion_window, latches, logic_states, suspend_states, odt_state, write_protect_state, etc_states

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
    - program_before_erase
    - read_before_program
    - programs_on_same_page
    - read_page_on_offset_guard
  - IO_bus_overlap
  - exclusion_window
  - latch_not_released
  - logic_state_overlap
  - operation_on_erase_suspended
  - operation_on_program_suspended
  - operation_on_nested_suspended
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

## 워크 플로우 (high-level)
- 초기화: `Proposer`, `ResourceManager`, `AddressManager`, `Validator`, `Scheduler` 인스턴스 생성
- runtime 정의 후 scheduler runtime 실행
  - `Scheduler` 에서 초기 event_hook 생성 후 heappush
  - while loop 으로 event_hook 을 heappop 하여 `Proposer` 를 호출하고, 새로운 operation 을 스케쥴한다

## Open questions
- **중요**:예약된 operation 과 runtime 으로 제안된 operation 의 우선 순위 조정 메커니즘은 어떻게 할 것인가? 단일 rail timeline 으로 runtime 으로 제안된 operation 이 예약된 operation 들의 validity 를 깨뜨리지 않으면 스케쥴 가능하다.
- state 별 oper

## 예상 risks
- research 후 작성






