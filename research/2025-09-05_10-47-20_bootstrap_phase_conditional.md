---
date: 2025-09-05T10:47:20+09:00
researcher: codex
git_commit: 84a1e345077390ddd40d3d21259297d6c13dd35b
branch: main
repository: nandseqgen_v2
topic: "Bootstrap 적용을 위한 단계별 phase_conditional 동적 변경 방안"
tags: [research, codebase, proposer, resourcemgr, bootstrap, phase_conditional]
status: complete
last_updated: 2025-09-05
last_updated_by: codex
last_updated_note: "미해결 질문에 대한 후속 연구 추가"
---

# 연구: Bootstrap 적용을 위한 단계별 phase_conditional 동적 변경 방안

**Date**: 2025-09-05T10:47:20+09:00
**Researcher**: codex
**Git Commit**: 84a1e345077390ddd40d3d21259297d6c13dd35b
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
PRD에 명시된 bootstrap을 적용하기 위해서, 실행 초기에 ERASE/PROGRAM/READ/DOUT를 최우선으로 소화하고 기아·쏠림을 방지하도록, 단계별로 동적으로 `phase_conditional` 값을 변경하는 방식으로 구현할 수 있는가? 가능하다면 구체적 설계와 적용 지점을 제시하라.

## 요약
- `proposer.propose`는 `CFG['phase_conditional']`를 그대로 사용한다. 런타임에서 이 맵을 단계별 오버레이로 교체/갱신하면 제안 후보가 즉시 바뀐다.
- Bootstrap 컨트롤러를 Scheduler 측에 두고, 각 단계(ERASE→PROGRAM→READ(+DOUT)) 목표 달성도(비율/개수)를 추적하며 `phase_conditional`을 동적으로 스왑한다.
- 다이/플레인 분산과 멀티플레인 강제는 (1) `op_names.multi=true`인 후보만 가중치>0으로 두고, (2) `AddressManager.sample_*`가 멀티플레인 셈플링을 수행하므로 자연 달성된다.
- DOUT는 READ의 시퀀스(inherit: same_page,multi)로 자동 전개 가능하며, 필요 시 DOUT 전용 단계 오버레이도 지원한다.
- PRD의 “bootstrap 동안 runtime propose 불허”는 “bootstrap 단계에서는 일반 후보의 확률을 0으로” 해석해 충족한다. 마지막 단계 완료 후 원래 분포로 복귀한다.

## 상세 발견

### 소비 지점: phase_conditional → Proposer
- `proposer.py:512` `_phase_dist(cfg, key)`에서 `cfg['phase_conditional']`을 조회해 상태키별 분포를 사용한다.
- `proposer.py:534` `propose()`는 이 분포에서 상위 후보를 평가하여 earliest feasible을 고른다.
- 상태키는 `proposer.py:496` `_phase_key()`가 `ResourceManager.op_state`를 기반으로 `BASE.STATE` 형식으로 산출한다.

### 상태 키 산출과 훅 구동
- `resourcemgr.py:483` `op_state(die, plane, at_us)`가 현재 (die,plane) 시점의 `BASE.STATE`를 반환하여 phase 키로 쓰인다.
- Scheduler가 PHASE_HOOK을 언제 발생시키는지는 PRD 설계 기준이며(코드 외부), propose 호출 시점의 key만 맞으면 동적 분포 교체가 그대로 반영된다.

### 주소 샘플링과 멀티플레인 강제
- `addrman.py:412` `sample_erase`, `addrman.py:492` `sample_pgm`, `addrman.py:630` `sample_read`는 `sel_plane`(리스트)로 멀티플레인 샘플링을 지원한다. Proposer는 op_name의 `multi`를 보고 최대 k부터 축소 시도한다.
- Proposer의 타깃 샘플링은 멀티플레인 실패 시 k를 줄이며(최소 2) 재시도해 멀티플레인을 우선 달성한다.

### READ→DOUT 전개
- READ 계열은 `config.yaml`에서 sequence로 DOUT을 내장하고 상속 규칙에 `['same_page', 'multi']`를 둔다. Proposer는 `proposer.py:456` `_expand_sequence_once`로 한 스텝 시퀀스를 확장해 DOUT을 자동 추가한다.

## 구현 방안

### 방안 A: 런타임 오버레이(권장)
- 개념: Scheduler 런타임에 `BootstrapController`를 두고, 각 단계마다 `cfg['phase_conditional']`을 “오버레이 맵”으로 교체한다.
- 방식: 기본분포 `pc_base = cfg['phase_conditional']`를 보관하고, 단계별 오버레이 `pc_stageX`를 생성해 `cfg['phase_conditional'] = pc_stageX`로 스왑. 단계 완료 시 다음 오버레이로 교체. 최종 완료 시 `pc_base` 복원.
- 장점: Proposer 비침투(시그니처/로직 무변경), 테스트/롤백 용이, PRD 적합성 높음.
- 단점/위험: 동시성에서 cfg 공유 시 레이스 여지(단일 스레드/틱 기반이면 무시 가능). 단계 판정 지표 설계 필요.

구체 절차:
1) 단계 정의 및 목표
   - Stage E(Erase): 멀티플레인 ERASE만 허용. 목표: 다이별 또는 전체 기준 ‘erase_ratio’ 달성(예: 다이당 블록의 X% ERASE).
   - Stage P(Program): 멀티플레인 PROGRAM만 허용. 목표: ‘program_ratio’ 달성(예: 다이당 페이지/블록 진행 비율).
   - Stage R(Read): 멀티플레인 READ만 허용(시퀀스로 DOUT 동반). 목표: ‘read_ratio’ 달성.
   - Stage DO(Optional): 잔여 DOUT 소화. 필요 시 DOUT만 허용하는 얇은 오버레이 사용.

2) 오버레이 생성 규칙(의사코드)
   - 입력: `cfg['op_names']`에서 base∈{ERASE, PROGRAM_*, READ, PLANE_READ, READ4K, PLANE_READ4K, CACHE_READ 등 READ 계열}, `multi=true`인 op_name만 활용.
   - 셀타입 필터: PRD의 bootstrap 설정값(celltype)을 반영하여 해당 `celltype`만 허용.
   - 분포 키: 모든 상태키에 동일한 분포를 적용하거나 최소 `DEFAULT`에만 주어도 작동(구현 용이). 정밀 제어가 필요하면 `*.*` 전 키에 동일 분포를 매핑.
   - 정규화: 양수 항목 합이 1이 되도록 정규화.

   예시(Stage E; 단순 DEFAULT만 교체):
   ```yaml
   phase_conditional:
     DEFAULT:
       Block_Erase_Multi_Plane_Legacy_SLC: 0.5
       Block_Erase_Multi_Plane_ONFI_SLC:   0.5
   ```

3) 단계 완료 판정(지표 두 가지 대안)
   - 대안1(단순/권장): AddressManager 스냅샷 질의로 상태 기반 비율 계산
     - ERASE: `addrman.addrstates == ERASE` 비율(다이별/전체) ≥ 목표치
     - PROGRAM: `addrman.addrstates > ERASE` 진행 블록/페이지 비율 ≥ 목표치(또는 샘플링 카운터)
     - READ: `addrman.addrstates >= offset`에서 READ 수행 카운터로 누적 ≥ 목표치
     - 장점: 간단하고 외부 종속 없음. 단점: 정확한 “오퍼레이션 완료”와는 약간 다를 수 있음.
   - 대안2(정확): Scheduler가 OP_END 이벤트로 완료 카운터를 유지하여 목표치 도달 시 단계 전환
     - 장점: 실제 완료 기준. 단점: 이벤트 수집/집계 구현 필요.

4) 제약/우선순위
   - 멀티플레인 강제: 오버레이 후보를 `multi=true` op_name으로만 구성.
   - 최우선 실행: 필요 시 해당 base들의 `instant_resv=true`를 일시 적용(옵션). 구현은 오버레이와 별개로 `cfg['op_bases'][base]['instant_resv']=true`를 단계 동안만 설정/복원.

5) 적용 시퀀스(최상위 루프)
   - (런 시작) `if enable_bootstrap and num_runs>2 and run_idx==0:` 부트스트랩 모드 진입, 일반 propose 허용분포→0으로 수렴.
   - Stage E 오버레이 적용 → 판정 충족 시 Stage P로 교체 → Stage R → (Optional) Stage DO → 완료 시 `pc_base` 복원, 부트스트랩 종료 플래그 set.

6) 실패/부족 케이스 보호
   - 샘플링 실패가 반복되면(후보 고갈) `maxplanes`를 낮춰 fallback 허용(현재 Proposer가 자동 축소).
   - 각 단계에 타임아웃/최대 반복 횟수 설정 후, 목표치에 못 미치면 “최소치”만 달성한 것으로 간주하고 다음 단계로 이행.

### 방안 B: 사전 예약(Plan-first) + propose 차단
- 개념: 부트스트랩 전체를 Proposer 바깥에서 AddressManager/ResourceManager를 사용해 예약·커밋하고, 해당 창 동안은 propose를 완전히 호출하지 않음.
- 장점: “예약 우선” PRD 문맥에 가장 충실, 빠르고 결정적.
- 단점: 구현량 큼(시퀀스/타이밍 계획 필요), 기존 propose 경로 재사용성 낮음.

### 방안 C: Proposer 훅 기반 런타임 오버라이드
- 개념: `propose(now, hook, cfg, ...)`의 `hook`에 `phase_conditional_override` 또는 `bootstrap_stage`를 넣어 분포를 상황별로 덮어씀.
- 장점: cfg 돌연변이 없이 호출별 미세 제어 가능.
- 단점: Proposer 시그니처/로직 변경 필요(테스트 영향), 호출자 전원 수정 필요.

## 코드 참조
- `proposer.py:496` - `_phase_key`: RM 상태→phase 키(`BASE.STATE`) 산출
- `proposer.py:512` - `_phase_dist`: `cfg['phase_conditional']` 조회 지점(오버레이 반영됨)
- `proposer.py:534` - `propose`: phase 분포 소비, 후보 평가, 시퀀스 한 단계 확장
- `resourcemgr.py:483` - `op_state`: 상태 타임라인 조회(phase 키 근거)
- `addrman.py:412` - `sample_erase`: 멀티/단일 플레인 ERASE 샘플링
- `addrman.py:492` - `sample_pgm`: PROGRAM 샘플링(모드 일관성 보장)
- `addrman.py:630` - `sample_read`: READ 샘플링(오프셋/모드 기반)
- `config.yaml:4295` - `phase_conditional: {}` (오토필 전제; 런타임 오버레이 대상)

## 아키텍처 인사이트
- phase_conditional은 “소비 지점이 단일”이어서(즉, Proposer), 런타임에서 맵 교체만으로 시스템 전역의 제안 성향을 전환할 수 있다.
- 멀티플레인 보장은 후보 세트 구성(오버레이)로 달성하는 것이 간단하고 안전하다.
- DOUT는 READ 시퀀스 확장으로 자연 전개되므로 별도 DOUT 단계를 강제하지 않아도 되나, 잔여가 있다면 얇은 DOUT 오버레이로 마무리할 수 있다.

## 역사적 맥락(thoughts/ 기반)
- `research/2025-09-04_23-20-57_proposer_interface_decoupling.md` - Proposer가 RM/AM과 느슨 결합, CFG 파생 키를 단일 진실로 사용하도록 설계함(phase_conditional 소비 지점 고정).
- `research/2025-09-05_01-35-43_scheduler_standalone_vs_embedded.md` - Scheduler가 PHASE_HOOK/QUEUE_REFILL 관리 및 결정적 시간 분기를 담당(부트스트랩 컨트롤러 배치 위치와 부합).

## 관련 연구
- `research/2025-09-03_01-51-14_resourcemgr_vs_prd_latch.md`
- `research/2025-09-04_22-04-48_validator_integration_in_resourcemgr.md`

## 미해결 질문
- 비율 정의의 정밀도: “die 비율”을 다이 커버리지 기준으로 볼지, 다이 내 블록/페이지 비율로 볼지 명확화 필요. -> (검토완료) PRD 에 명시.
- 부트스트랩 대상 셀타입 복수 지정 시 혼합 가중치(균등 vs 우선순위)의 정책. -> (검토완료) 균등
- 부트스트랩 중 SR/RESET 등 유지보수 계열 허용 여부(완전 차단 vs 소량 허용) 정책. -> (검토완료) 완전 차단
- num_runs/run_until이 실제 Scheduler 런타임에서 어떻게 노출되는지(드라이버 인터페이스). -> (TODO) 후속 research 필요

## 후속 연구 2025-09-05T11:02:33+09:00

### 비율 정의(커버리지 vs 볼륨) — 제안 정의와 계산 지점
- ERASE 커버리지(블록 기준): die별 “ERASE 상태 블록 수 / 유효 블록 수(BAD 제외)”. AddressManager 배열에서 즉시 계산. 참조: `addrman.py:1`, `addrman.py:140`, `addrman.py:151`, `addrman.py:174`.
- PROGRAM 커버리지(블록 기준): die별 “(addrstates >= 0) 블록 수 / 유효 블록 수”.
- PROGRAM 볼륨(페이지 기준): die별 “Σ(max(addrstate+1,0)) / (pagesize × 유효 블록 수)”. 마지막 프로그램 페이지 인덱스를 페이지 수로 변환(state+1). 참조: `addrman.py:1188`.
- READ 커버리지/볼륨(타임라인 기반): TimelineLogger 로그에서 산출.
  - 커버리지: die별 “READ 1회 이상 발생 블록 수 / 유효 블록 수”.
  - 볼륨: READ 타깃 총 개수 또는 페이지 수. 보조: 쏠림 통계(`viz_tools.py:864`, `viz_tools.py:972`).
- 권고 판정식: 각 단계 완료를 “커버리지 임계치 + 볼륨 최소치”로 조합(쏠림 완화).

예시 의사코드:
```
def erased_coverage_by_die(am):
    good = (am.addrstates != BAD)
    res = []
    for d in range(am.num_dies):
        idx = (am._die_index == d) & good
        tot = int(idx.sum()); era = int((am.addrstates[idx] == ERASE).sum())
        res.append((d, era / max(tot,1)))
    return res

def program_volume_by_die(am):
    good = (am.addrstates != BAD)
    s = am.addrstates.copy(); s[s < 0] = -1
    s = s + 1
    out = []
    for d in range(am.num_dies):
        idx = (am._die_index == d) & good
        tot_pages = am._blocks_per_die * am.pagesize
        out.append((d, float(s[idx].sum()) / max(1, tot_pages)))
    return out
```

### 복수 셀타입 혼합 정책
- 집합 S(허용 셀타입)로 필터한 op_name만 오버레이에 포함. 혼합 방식은:
  - 균등 혼합: 기본. 단순/결정적.
  - 우선순위 혼합: `bootstrap.celltype_weights`로 비율 반영 후 정규화(예: `{SLC:0.6, TLC:0.4}`).
  - 적응 혼합: 진행률 낮은 셀타입 가중치 소폭 증대(±epsilon, 범위 제한). AddressManager의 SLC→A0/AC 허용 규칙 고려. 참조: `addrman.py:514`, `addrman.py:680`.

### SR/RESET 허용 여부
- 권장: 부트스트랩 동안 SR/RESET 확률 0(분포 제외). SR은 `instant_resv`지만 후보 미포함 시 제안되지 않음. 참조: `config.yaml:480` 인근.
- 보조: `phase_hook_disabled_kinds`로 SR 훅 자체 차단 유지. 참조: `nandsim_demo.py:2957` 부근.
- 예외 허용 시: SR에 극소량(예: 0.01) 부여, RESET은 금지 유지.

### num_runs/run_until 통합
- 현재 데모:
  - run_until 계산: 기본값 + 부트스트랩 의무 데드라인 + 마진. 참조: `nandsim_demo.py:3247`, `nandsim_demo.py:3254`, `nandsim_demo.py:3258`, `nandsim_demo.py:3263`, `nandsim_demo.py:3264`, 실행: `nandsim_demo.py:2957`.
  - num_runs: 미구현(단일 런). PRD 요구사항은 상위 드라이버에서 반복 실행/스냅샷 필요.
- 제안(표준 Scheduler):
  - run_until: `t_boot_end`(부트스트랩 예약 종료시각) + `margin_per_op × N_boot_ops` 포함해 부트스트랩 drain 보장.
  - num_runs: 드라이버 루프에서 첫 run에만 overlay 활성화, 완료 후 원 분포 복원 및 스냅샷 지속.

