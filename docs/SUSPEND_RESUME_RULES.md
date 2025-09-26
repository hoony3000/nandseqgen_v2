# ERASE SUSPEND->RESUME 동작 규칙
- ERASE 가 예약되면 ongoing_ops 에 정보를 등록한다.
- die 가 ERASE.CORE_BUSY state 일때 SUSPEND 동작이 수행될 수 있다.
- SUSPEND 동작이 수행되면, 진행중이던 ERASE 동작은 중단되고, SUSPENDED 상태로 변경된다. 이 떄 중단된 ERASE 는 종료까지 얼마나 남았었는지 `remaining` 시간을 저장하고, 예정된 종료 이벤트는 무기한 연장된다. ongoing_ops 등록된 정보는 suspended_ops_erase 에 저장되고, 저장됐던 ongoing_ops 의 정보는 비워진다.
- SUSPEND 시점 이후에 실행되지 않은 state/bus 구간은 `remaining` 정보와 함께 보존되고, plane/bus/die 배타 예약은 중단 시각까지만 유지하도록 잘린다.
- SUSPENDED 상태에서는 RESUME 되기 전까지 해당 die 는 ERASE 동작이 금지된다.
- SUSPENDED 상태에서 RESUME 동작이 수행될 수 있다.
- RESUME 동작이 수행되면 ResourceManager 가 중단된 state/bus 정보를 기반으로 내부 트랜잭션을 생성해 plane/bus/die 예약을 다시 적용하고, `remaining` 시간이 지난 후 종료되도록 스케줄한다. 예약 성공 시 다시 ongoing_ops 에 재등록되어 후속 SUSPEND 대상이 된다.
- 재예약이 실패하면 suspended 큐에 그대로 남기고 proposer 디버그 로그(`proposer_debug_*.log`)에 축약된 이유를 기록한다.
- SUSPEND, RESUME 동작은 최초 등록됐던 ERASE 가 종료될 때까지 반복될 수 있으며, 종료가 되면 SUSPEND, RESUME 의 대상이 될 수 없다.

# PROGRAM SUSPEND->RESUME 동작 규칙
- ERASE 가 SUSPENDED 된 상황에서 SUSPENDED ERASE 와 다른 target (die, block) 에 대해 PROGRAM 동작이 수행될 수 있다. 즉, ERASE SUSPENDED 와 PROGRAM SUSPENDED state 가 중첩될 수 있다. 하지만, 중단된 동작을 다시 재개할 떄는 PROGRAM RESUME 으로 먼저 중단됐던 PROGRAM 을 완료하고, ERASE RESUME 을 수행할 수 있다.
- ERASE SUSPEND 와 규칙은 동일하나, suspend_ops_erase 가 아닌 suspend_ops_program 에 정보를 저장한다.
- PROGRAM RESUME 역시 잔여 plane/bus/die 예약을 재적용하며 실패 시 proposer 디버그 로그에 사유를 남긴다.
