# ERASE SUSPEND->RESUME 동작 규칙
- ERASE 가 예약되면 ongoing_ops 에 정보를 등록한다.
- die 가 ERASE.CORE_BUSY state 일때 SUSPEND 동작이 수행될 수 있다.
- SUSPEND 동작이 수행되면, 진행중이던 ERASE 동작은 중단되고, SUSPENDED 상태로 변경된다. 이 떄 중단된 ERASE 는 종료까지 얼마나 남았었는지 remaining 시간을 저장하고, 예정된 종료 이벤트는 무기한 연장된다. ongoing_ops 등록된 정보는 suspended_ops_erase 에 저장되고, 저장됐던 ongoing_ops 의 정보는 비워진다.
- SUSPENDED 상태에서는 RESUME 되기 전까지 해당 die 는 ERASE 동작이 금지된다.
- SUSPENDED 상태에서 RESUME 동작이 수행될 수 있다.
- RESUME 동작이 수행되면, RESUME 자체 동작이 끝난 직후 중단됐던 suspended_ops_erase 의 정보를 토대로 ERASE 가 재개되고, remaining 시간이 지난 후에 종료되도록 스케쥴된다. 그리고, 다시 ongoing_ops 에 재등록 되어, 후속 SUSPEND 동작의 대상이 될 수 있게 한다.
- SUSPEND, RESUME 동작은 최초 등록됐던 ERASE 가 종료될 때까지 반복될 수 있으며, 종료가 되면 SUSPEND, RESUME 의 대상이 될 수 없다.

# PROGRAM SUSPEND->RESUME 동작 규칙
- ERASE 가 SUSPENDED 된 상황에서 SUSPENDED ERASE 와 다른 target (die, block) 에 대해 PROGRAM 동작이 수행될 수 있다. 즉, ERASE SUSPENDED 와 PROGRAM SUSPENDED state 가 중첩될 수 있다. 하지만, 중단된 동작을 다시 재개할 떄는 PROGRAM RESUME 으로 먼저 중단됐던 PROGRAM 을 완료하고, ERASE RESUME 을 수행할 수 있다.
- ERASE SUSPEND 와 규칙은 동일하나, suspend_ops_erase 가 아닌 suspend_ops_program 에 정보를 저장한다.
