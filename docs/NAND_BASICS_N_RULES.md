# NAND_RULES.md

## Summary
- NAND 는 non-volatile memory device 로 IO bus 를 통해 CMD 를 던져 내부 memory cell array 에 데이터를 쓰고 읽을 수 있다.
- memory cell array 에 접근하기 위해서는 CMD, address 가 필요하다.
- CMD 를 입력하면 대부분의 경우 ISSUE->CORE_BUSY->END 의 state 변화가 시간순으로 일어난다.

## Addressing
- target → 하나 이상의 die → 각 die는 2~4 plane → plane 안에 여러 block → block 내 다수 page → page 당 TLC 셀(3bit/cell) → page 안에 LSB/CSB/MSB
- dies: NAND device 내 여러 개의 die 존재.
- block: die 내 Erase 의 대상이 되는 주소 단위.
- plane: die 내 block group 주소 단위. e.g) block0 - plane0, block1 - plane1, ..., block4 - plane0 / in 4-plane topology
- page: block 내 Program, 또는 Read 대상이 되는 주소 단위.
- column: page 내 byte 주소 단위. dout, datain 시 column 지정 가능
- cell_mode: 기본 동작에 cell mode 를 지정하여 동작 가능. e.g) erase_slc, erase_tlc, read_slc, read_tlc.

## Cell mode
- block 별로 cell mode 를 지정하여 erase/program/read 할 수 있다.
- 종류: TLC/FWSLC/SLC/AESLC/A0SLC/ACSLC
- cell mode 별로 busy latency 가 다르다.

## `Basic operation` (not fully listed)
- erase: block 에 존재하는 모든 page data 를 지운다. ERASE.CORE_BUSY->ERASE.END
- datain: program 하고자 하는 data 를 cache latch 에 채운다. DATAIN.END
- program: erase 된 page 에 data 를 기록한다. datain 후 execution CMD 를 atomic set 로 구성한다. PROGRAM.CORE_BUSY->PROGRAM.END
- cache_program: PROGRAM.CORE_BUSY 동작이 진행되는 동안 datain 을 허용하는 phase 를 둔 program 의 종류이다. CACHE_PROGRAM.CORE_BUSY->CACHE_PROGRAM.DATAIN->CACHE_PROGRAM.END
- oneshot_program_lsb: cell type 이 TLC 인 경우 oneshot_program 의 data 중 lsb data 를 NAND 내부 lsb latch 에 적재한다. ONESHOT_PROGRAM_LSB.CORE_BUSY->ONESHOT_PROGRAM_LSB.END
- oneshot_program_csb: program_lsb 이후 csb data 를 csb latch 에 적재한다. ONESHOT_PROGRAM_CSB.CORE_BUSY->ONESHOT_PROGRAM_CSB.END
- oneshot_program_msb: program_csb 이후 msb data 를 msb latch 에 적재한다. PONESHOT_PROGRAM_CSB.CORE_BUSY->ONESHOT_PROGRAM_MSB.END
- oneshot_program_execution_msb: program_csb 이후 msb data 를 msb latch 에 적재하면서 동시에 oneshot_program 을 진행시킨다. ONESHOT_PROGRAM.CORE_BUSY->ONESHOT_PROGRAM.END
- oneshot_cache_program: ONESHOT_PROGRAM.CORE_BUSY 동작이 진행되는 동안 datain 을 허용하는 phase 를 둔 oneshot_program 의 종류이다. ONESHOT_PROGRAM_LSB->ONESHOT_PROGRAM_CSB->(ONESHOT_PROGRAM_MSB)->ONESHOT_PROGRAM_EXEC_MSB->ONESHOT_PROGRAM.CORE_BUSY->ONESHOT_PROGRAM.DATAIN->ONESHOT_PROGRAM.END
- read: program 된 page 에 data 를 읽어와 cache latch 에 채운다. READ.CORE_BUSY->READ.DOUT->READ.END
- 4k_read: page 에서 4k chunk data 만 read 한다. READ.CORE_BUSY->READ.DOUT->READ.END
- cache_read: read 시 READ.CORE_BUSY 동작이 진행되는 동안 dout 을 허용하는 phase 를 둔 read 의 종류이다. CACHE_READ.CORE_BUSY->CACHE_READ.DOUT->CACHE_READ.END
- dout: read 이후 cache latch 에 있는 데이터를 출력한다. DOUT.END
- reset: NAND 내부 cell 동작(Erase/Program/Read/etc.) 을 중단하고 IDLE state 로 돌아간다. RESET.CORE_BUSY->RESET.END
- erase_suspend: erase 동작을 중단하고, ERASE_SUSPEND.CORE_BUSY->ERASE_SUSPEND.SUSPENDED 로 state 를 변경하고 유지한다. erase 진행 상태를 내부적으로 기록해 둬 resume 시에 해당 시점부터 재개한다. 
- erase_resume: ERASE_SUSPEND.SUSPENDED state 를 중단하고 다시 ERASE_RESUME.CORE_BUSY->ERASE.CORE_BUSY 상태로 재개한다.
- program_suspend: program 동작을 중단하고, PROGRAM_SUSPEND.CORE_BUSY->PROGRAM_SUSPEND.SUSPENDED 로 state 를 변경하고 유지한다. program 진행 상태를 내부적으로 기록해 둬 resume 시에 해당 시점부터 재개한다.
- program_resume: PROGRAM_SUSPEND.SUSPENDED state 를 중단하고 다시 PROGRAM_RESUME.CORE_BUSY->PROGRAM.CORE_BUSY 상태로 재개한다.
- read_status: NAND state 를 데이터로 출력한다. CORE_BUSY, SUSPENDED, target address 를 입력해 해당 block 의 BADBLOCK 여부 등을 확인 가능하다.
- set_para: NAND 내부 동작에 관한 configuration register 데이터를 설정한다.
- planelevel_set_para: NAND 내부 동작에 관한 configuration register address 의 데이터를 설정한다. IDLE 상태인 plane 을 target address 로 하여 동작 시킬 수 있다. target address 가 아닌 별도 address 사용
- get_para: NAND 내부 동작에 관한 configuration register 데이터를 읽어온다.
- set_feature: NAND 동작 feature 에 대한 설정을 한다.
- get_feature: NAND 동작 feature 에 대한 설정을 출력한다.
- set_feature_lun_level: NAND 동작 feature 에 대한 설정을 한다. target address 지정
- get_feature_lun_level: NAND 동작 feature 에 대한 설정을 출력한다. target address 지정
- odt_enable: 입출력 ODT 설정을 켠다.
- odt_disable: 입출력 ODT 설정을 끈다.
- camread: configuration register 의 모든 값을 기본 설정으로 되돌린다. CAMREAD.CORE_BUSY->CAMREAD.END
- read_id: device 고유 id 를 출력한다. READID.DOUT
- zq_calibration: 출력 impedance 보정 동작을 수행한다. erase/program/read CORE_BUSY 중에도 수행가능
- write_traininig_din: erase/program/read CORE_BUSY 중에도 수행가능
- write_traininig_dout: erase/program/read CORE_BUSY 중에도 수행가능
- read_traininig: erase/program/read CORE_BUSY 중에도 수행가능
- dcc_traininig: erase/program/read CORE_BUSY 중에도 수행가능
- write_protection: erase/program/read CORE_BUSY 중에 수행하면 reset 동작 수행. 이후에는 erase/program 이 globally 금지된다.
- read_write_conflict_fire_N_forget: PROGRAM_SUSPENDED.SUSPENDED state 에서 lsb/csb/msb latch 에 입력된 data 를 read 한다. READ_WRITE_CONFLICT.CORE_BUSY->READ_WRITE_CONFLICT.END
- copyback_read: read 동작 중의 한 종류로 page data 를 다른 block 으로 copy 하기 위한 동작. 이후 후속 동작으로 copyback_program 을 진행한다. COPYBACK_READ.CORE_BUSY->COPYBACK_READ.DOUT->COPYBACK_READ.END
- copyback_program: copyback_read 한 data 를 latch 에 둔 상태로 datain 없이 바로 program 한다. COPYBACK_PROGRAM.CORE_BUSY->COPYBACK_PROGRAM.END
- oneshot_copyback_program_lsb: copyback_read 한 데이터를 lsb latch 에 적재한다. COPYBACK_PGM_LSB
- oneshot_copyback_program_csb: copyback_read 한 데이터를 csb latch 에 적재한다. COPYBACK_PGM_CSB
- oneshot_copyback_program_msb: copyback_read 한 데이터를 msb latch 에 적재한다. COPYBACK_PGM_MSB
- oneshot_copyback_program_msb: copyback_read 한 데이터를 msb latch 에 적재하면서 동시에 oneshot_copyback_program 을 진행시킨다. ONESHOT_COPYBACK_PROGRAM.CORE_BUSY->ONESHOT_COPYBACK_PROGRAM.END
- oneshot_copyback_program: copyback_read 한 데이터를 oneshot_program 방식으로 program 한다. COPYBACK_PGM_LSB->COPYBACK_PGM_CSB->(COPYBACK_MSB)->ONESHOT_COPYBACK_PROGRAM.CORE_BUSY->ONESHOT_COPYBACK_PROGRAM.END
- data_transfer_to_cache_1st_page_tlc: oneshot_program_lsb 데이터를 read 한다. TRANSFER_LSB.CORE_BUSY->TRANSFER_LSB.DOUT->TRANSFER_LSB.END
- data_transfer_to_cache_2nd_page_tlc: oneshot_program_csb 데이터를 read 한다. TRANSFER_CSB.CORE_BUSY->TRANSFER_CSB.DOUT->TRANSFER_LSB.END
- data_transfer_to_cache_3rd_page_tlc: oneshot_program_msb 데이터를 read 한다. TRANSFER_MSB.CORE_BUSY->TRANSFER_MSB.DOUT->TRANSFER_LSB.END
- all_wl_dummy_program: block 전체 page 들에 coerce program 을 한다. ALLWL_DUMMY_PROGRAM.CORE_BUSY->ALLWL_DUMMY_PROGRAM.END
- dsl_vth_check: monitoring 목적의 system page data 를 출력한다. DSLVTH_CHECK.CORE_BUSY->DSLVTH_CHECK.END

## Cache operation
- cache 동작 중 dout 파이프라이닝이 가능하다.
- cache_read 이후 이어지는 dout 동작은 이전 read 에 대한 data 이다.
- cache_read 동작 중에 target plane 에 대해서 celltype 변경이나, 기본 read 동작은 허용되지 않고, cache_read_end 동작으로 cache 상태를 종료하고 나서 기본 read 동작이 허용된다.
- cache_program 동작 중 datain 파이프라이닝이 가능하다.
- cache_program 동작이 진행되는 동안에는 cache_program/program 은 celltype 이 같아야 하며, 기본 program 동작으로 cache 상태를 종료하고 나서 기본 program 동작이 허용된다.

## Multi-plane operation
- 여러 plane 을 선택하여 동시에 erase/program/read 을 각 plane 마다 동작하게 가능하다.
- multi-plane 동작이 끝나는 시점은 plane 마다 완전히 동일하다.

## plane_interleave_read
- single-plane read/cache_read 동작은 plane wide 로 동작되며, 동작 중이지 않는 plane 에 single-plane read/cache_read 동작을 추가할 수 있다.

## Resources & Rules

### addr_state, addr_mode: erase/program/read address dependencies
- erase 는 cell_mode 에 상관없이 모든 block 에 대하여 수행될 수 있지만, 초기 상태가 BADBLOCK 인 경우에는 금지된다
- erase 되지 않은 block 에 program 할 수 없다.
- erase 가 특정 block 에 특정 cell mode 로 수행됐다면, 해당 block 에 program/read 동일한 cell mode 로 수행해야 된다.
- program 되지 않은 page 에 read 할 수 없다.
- 특정 block 에 program 할 때는 page 0 -> last page 의 오름차순으로만 program 해야한다.
- 동일 page 에 두번 이상 program 할 수 없다.
- NAND 의 특성상 block 내 마지막 program 된 page 에서 offset 을 준 아래 page 에서 read 하는 것이 권장된다.
- erase 가 특정 block 에 특정한 cell mode 로 수행됐다면, 해당 block 에는 program/read 도 동일한 cell mode 로 동작해야한 한다.
- multi-plane 동작을 할 때 target address 간의 group 제약이 존재한다.
    - address group 내 plane/block 순서는 오름차순이어야 한다.
    - page address 는 모두 동일해야 한다.
    - group 내 block address 는 다음의 값이 동일해야 한다: block_address // num_planes
    - good: ((die0,plane0,block4,page32), (die0,plane2,block6,page32), (die0,plane3,block7,page32))
    - bad: ((die0,plane2,block6,page32), (die0,plane0,block4,page31), (die0,plane3,block7,page28))

### IO_bus
- 제한: CMD 입력 시 ISSUE 하는 시간이 필요하고, 동시에 여러 CMD 를 중첩하여 ISSUE 할 수 없다.

### latches
- 제한: plane-level 에서 page read 후 dout 전에 erase/program 을 할 수 없다.
- NAND 의 입출력을 도와주는 cache/lsb/csb/msb latch 가 존재한다.
- page read 후 data 는 cache latch 에 저장된다. dout 동작 전에 erase/program 수행 시 cache latch 데이터는 없어진다.
- oneshot_program_lsb/csb/msb 에 data 는 임시로 cache latch 에 저장되고, 최종으로 lsb/csb/msb latch 로 이동된다.

### states
- 제한: 어떤 state, phase 이냐에 따라 허용되는 CMD 가 바뀌고, state 에 따라 일부 CMD 가 제한된다.
- operation 종류에 따라 logic state 를 변하게 하는 것과 안 변하는 것으로 나뉜다.
- operation 동작 시 state 는 여러 phase 를 가지고, 시간에 따라서 변한다. e.g) READ.ISSUE->READ.CORE_BUSY->READ.END
- operation 종류에 따라 plane-level, die-level, global-level 에서 state 가 바뀌게 된다.
- erase/program 은 single-plane, multi-plane 상관없이 동작 시 die-wide 로 일부 CMD 를 제외하고 모든 plane 에서 erase/program/read 가 금지된다.
- read 는 single-plane 동작에 한해서, 동작하지 않는 IDLE 상태의 plane 에 동시에 read 가 가능하다. 이는 multi-plane 동작과는 다른 것으로 plane_interleave_read 라고 부른다
- read_status, reset 등의 operation 은 state 에 상관없이 동작 가능하다.
- single-plane 동작과 multi-plane 동작, multi-plane 동작과 multi-plane 동작은 overlap 될 수 없다.
- 그 외 state 별 operation 제약은 별도의 파일에 정의한다; `op_specs.yaml`

#### state 종류 (not fully listed)
- IDLE
- ERASE.CORE_BUSY
- PROGRAM.CORE_BUSY
- READ.CORE_BUSY
- RESET.CORE_BUSY
- SUSPEND.CORE_BUSY
- 그 외 `Basic operation` 에서 언급한 것들
- 추후 추가할 예정

### cache_state
- 제한
  - cache_read 이 진행 중일 때는 cache_read_end 동작 없이 기본 read 는 허용되지 않고, cache_read, dout 만 허용하되 target plane 을 바꾸거나 celltype 을 바꾸어서는 안된다.
  - cache_program 이 진행 중일 때는 기본 program 동작으로 cache_program 상태를 종료할 수 있다. cache_program 종료 없이 target plane 이나 celltype 을 바꾸어서는 안된다.
- target plane 에 대해서 cache_read 진행중임을 나타내는 상태 값이 필요하다
- target die 에 대해서 cache_program 진행중임을 나타내는 상태 값이 필요하다

### suspend_states
- 제한: 아래 세가지 종류가 있고, 종류에 따라 허용되는 operation 이 달라진다.
- erase_suspend: erase 동작이 중단된 상태
- program_suspend: program 동작이 중단된 상태
- nested_suspend: erase_suspend->program_suspend 순서로 suspend 가 중단된 상태
- resume 동작이 수행되면, 멈춰있던 erase/program 동작이 재개된다. state, phase 는 마지막 저장된 것으로 복원된다.
- reset 동작이 수행되면 suspend states 는 모두 없어지고 erase/proogram 동작은 reset 된다.

### ODT_state
- 제한: ODT_disable operation 동작 시 특정 operation 의 허용이 제한되고, ODT_reenable 입력 시 그 제한이 풀린다.

### write_protect_state
- 제한: write_protection operation 동작 시 erase, program 이 제한된다.

### etc_states
- 추후 디자인 필요하지만 현재는 필요 없음: 특정 set_parameter, set_feature operation 에 의해 상태가 변할 수 있다.