# operation attributes example
base: PROGRAM_SLC
multi: false
scope: "DIE_WIDE"
affect_state: false
instant_resv: true
celltype: FWSLC
sequence:
  probs:
    - ONESHOT_PROGRAM_CSB.SEQ: 1.0
    - ONESHOT_PROGRAM_CSB_CACHE.SEQ: 0.0
  inherit:
    - ONESHOT_PROGRAM_CSB.SEQ: ['same_page']
    - ONESHOT_PROGRAM_CSB_CACHE.SEQ: ['same_page']
states:
  - ISSUE:
    bus: true
    duration: 0.4
  - CORE_BUSY:
    bus: false
    duration: 8.0
payload:

# state attributes
block_pgm_state
block_celltype_state
bus_state
busy_state
cache_state
suspend_state
odt_state
latch_state

# validation
bus_exclusion
busy_exclusion
multi_exclusion
latch_exclusion
suspend_exclusion
odt_exclusion
cache_exclusion
addr_dependency

# operation 동작 특성
- schedule : nand operation 은 scheduler 에 의해서 예약되고 수행된다.
- resource occupation : operation 마다 속성이 정해져 있고, 속성에 따라 nand resources 가 변경됨.
- schema : operation 종류에 따라 schema 가 정해지고, schema 의 속성값은 그 때 그 때 달라진다.
- payload : operation 종류에 따라 nand 에 입력해야 하는 command, payload(address, data 등) 가 정해지고, payload 의 속성값은 그 때 그 때 달라진다.
- execution & duration : scheduler 가 operation 을 수행하기 위한 execution time 이 있고, execution 이 끝나면 nand resource 가 예약이 되어 특정 시간동안 resource 의 state 가 변화한다. state 는 operation 당 여러개가 될 수 있고 각 state 별로 진행 시간이 정해져 있다.
- suspension & resumption : suspend operation 은 진행 중이던 또 다른 operation 을 중단하게 만들며, 이 때 예약된 resource 의 state 의 변화는 중단된다. 이 동안에는 중단됐던 operation 이외의 다른 operation 이 수행될 수 있다. 이 후 resume operation 을 수행하면, 중단됐던 operation 의 resource 의 스케쥴이 재개된다.
- reset : reset operation 은 현재 진행중인 모든 operation 의 수행을 완전히 중단시킨다. 이 때 resource state 도 변하게 된다.
- operation 간 exclusivity : 특정 operation 이 수행되면, 그 동안 또다른 특정 operation 의 수행이 금지될 수 있으며, operation 의 수행이 안료되면 금지도 풀린다.
- operation 간 dependency : 특정 operation 은 이전에 어떤 operation 이 수행된 이후에만 수행될 수 있다. 이 때, 두 operation 간에는 operation 의 address 속성이 상속되거나 특정한 제한을 가할 수도 있다.
- operation 간 chaining : 특정 operation 이 수행되면 반드시 뒤따라 수행되어야 하는 operation 이 하나이상 예약될 수 있다.
- multi-plane operation : 특정 동작은 plane resource 를 여러 개 동시에 점유할 수 있다.
- probablistic scheduling : operation 의 예약은 resource state 에 따라 가능한 후보 operation 중 확률적으로 선택되어진다. **중요! 이 확률은 사전에 정의한 가중치에 따라 샘플되고, operation 에 bound 되지 않고, resource state 에 따라 샘플된다.**

# resource
- multi-level 구조 : nand resources 는 plane, die level, global 별로 state 가 할당되어 있으며, resource 별로 어느 level 에서 변경되는지는 다르다.
- bus_state : operation 의 execution time 이 동시에 겹칠 수 없다.
- block_pgm_state : nand 는 여러 개의 die, block, page 으로 구성되어 있고, block/page 별로 erase operation 이 수행된 후, program operation 이 수행된 후의 값이 변하게 된다. 이 값에 따라 이후에 수행될 erase/program/read operation 의 address 속성에 제한이 생긴다.
- block_celltype_state : block 별로 어떤 celltype 으로 erase/program operation 이 수행됐는지 기록이 된다. celltype 에 따라 해당 block 에 수행될 수 있는 erase/program/read operation 의 celltype 이 제한된다.
- busy_state : operation 이 수행되면 nand 내부적으로 busy_state 가 변경이 되고, 특정 operation 이 금지된다. 이후 busy_state 의 duration 이 끝나면, ready 상태가 된다.
- cache_state : 특정 operation 은 수행 후 cache_state 가 설정이 되고, cache_state 를 종료하는 operation 이 수행되지 않으면 그 상태가 지속되어, 특정 동작이 금지된다.
- suspend_state : suspend operation 이 수행되면, 그 대상이 되는 operation 이 수행중이면 중단되고 suspend_state 가 변경이 된다. 대상 operation 이 수행되고 있지 않은 상황이었다면, suspend_state 는 변경되지 않는다. suspend 된 operation 은 resume operation 이 수행되기 전까지 재개되지 않는다. suspend 상태일 떄 특정 operation 은 금지된다.
- odt_state : odt enable operation 이 수행되면, 특정 operation 은 금지된다. 이 후 odt disable operation 이 수행되면 금지는 해재된다.
- latch_state : 특정 operation 이 수행되면 nand 내부 latch 에 data 가 load 되고, 그 data 를 사용하기 전까지 또 다른 data 가 latch 내부에 load 되지 않도록 latch load 관련 특정 operation 이 금지되고 latch_state 가 변경된다. 이후 latch data 가 특정 operation 이 의해 사용이 되면, latch_state 가 해제된다.