# Operation Sequence Validation Rules

## 대상
- operation_sequence_*csv 파일

## 항목

### 1. EPR dependency violation cases
- mode mismatch between erase, and program : 동일 (die,block) 내에 program 할 때는 TLC, AESLC, FWSLC erase 된 곳에는 동일하게, SLC erase 된 곳은 SLC erase 된 곳은 SLC, A0SLC, ACSLC 로만 program 해야한다.
- mode mismatch between program, and read : 동일 (die,block) 내에는 동일한 celltype 으로 program/read 해야한다.
- program before erase : addr_state(die,block)=-1 이 아닌 block 에 program 금지addr_state(die,block)=-1 이 아닌 block 에 program 금지
- programs on same page : 동일 (die,block,page) 에 erase 하지 않은 상태에서 두 번 이상 program 금지
- program page not ascending : 동일 (die,block) 에 program 할 때에 target page address 는 오름차순으로 +1 씩 증하여야 한다.
- read before program : 동일 (die,block) 내 addr_state 보다 같거나 작은 page 에만 program 할 수 있다.
- mismatch between dout page and read page
- read missing dout : read 후에는 동일한 target

#### 방법 & workflow
- addr_state, addr_mode_erase, addr_mode_program, suspend_state, cache_state, latch_state 를 runtime 으로 업데이트
- 