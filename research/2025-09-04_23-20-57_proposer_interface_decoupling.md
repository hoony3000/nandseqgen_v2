---
date: 2025-09-04T23:20:57+09:00
researcher: Codex CLI
git_commit: fc87b4969fa59323631ac3e587b7df024f3d5cc1
branch: main
repository: nandseqgen_v2
topic: "Proposer 구현 시 ResourceManager/AddressManager에 복잡성을 전파하지 않는 인터페이스 설계"
tags: [research, codebase, proposer, resourcemanager, addressmanager, validator, scheduler, cfg]
status: complete
last_updated: 2025-09-04
last_updated_by: Codex CLI
last_updated_note: "Admission window 탐색 전략에 대한 후속 연구 추가"
---

# 연구: Proposer 구현 시 RM/AM 복잡성 전파 방지 인터페이스

**Date**: 2025-09-04T23:20:57+09:00
**Researcher**: Codex CLI
**Git Commit**: fc87b4969fa59323631ac3e587b7df024f3d5cc1
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
Proposer 를 구현할 때, 기존에 구현된 ResourceManager, AddressManager 에 복잡성을 전파하지 않도록 하는 경계/인터페이스는 어떻게 설계해야 하는가?

## 요약
- 포트/어댑터 원칙으로 경계 설정: Proposer는 순수(read-only) 구성요소로서 RNG/CFG/조회 전용 뷰를 주입받고, 상태 변경은 Scheduler+ResourceManager 트랜잭션 경계에서만 수행한다.
- 읽기 전용 `ResourceView` + 순수 `AddressSampler` + 독립 `Validator` 조합을 사용하면 RM/AM에 새로운 복잡도를 추가하지 않고 Proposer의 의사결정(후보 생성/사전검증/재시도)을 내부에 캡슐화할 수 있다.
- RM은 이미 제공하는 질의(`feasible_at`, `op_state`, `odt_state` 등)만 노출하고, AM은 순수 샘플러(`sample_*`)를 사용한다. 검증(EPR/상태금지)은 Validator에 위임하고 RM 내부 훅은 기존 스켈레톤을 유지한다.

## 상세 발견

### 구성 요소 경계 및 책임
- Proposer: 확률적 제안 생성, 주소 샘플링, 사전 검증(dry-run). 상태 변경 없음.
- Scheduler: 시간 전진/훅 드라이브, 트랜잭션 경계 설정(`begin→reserve→commit`), PHASE_HOOK 생성.
- ResourceManager: 타임라인/버스/래치/배제 윈도우의 단일 진실. 읽기 질의 + 예약/커밋 API 제공.
- AddressManager: 주소 상태/모드 기반의 후보 샘플링. 순수 `sample_*`와 명시적 `apply_*` 분리.
- Validator: Proposer가 만든 가안 배치에 대해 규칙 평가(EPR, 상태 금지, 배타/래치 중복 등).

### Proposer가 의존해야 하는 최소 포트
- `ResourceView`(읽기 전용; RM 얇은 래퍼)
  - `op_state(die:int, plane:int, at_us:float) -> Optional[str]` (`resourcemgr.py:483`)
  - `feasible_at(op, targets, start_hint, scope) -> Optional[float]` (`resourcemgr.py:338`)
  - `has_overlap(scope, die, plane_set, start, end, pred?) -> bool` (`resourcemgr.py:486`)
  - `odt_state() -> Optional[str]` (`resourcemgr.py:512`), `cache_state(die, plane, at_us?) -> Optional[str]` (`resourcemgr.py:521`), `suspend_states(die, at_us?) -> Optional[str]` (`resourcemgr.py:570`)
- `AddressSampler`(AM 얇은 래퍼; 순수 샘플만)
  - `sample_erase(sel_plane|planes, mode, size, sel_die?) -> List[Address]` (`addrman.py:412`)
  - `sample_pgm(sel_plane|planes, mode, size, sequential:bool, sel_die?) -> List[List[Address]]` (`addrman.py:492`)
  - `sample_read(sel_plane|planes, mode, size, offset?, sequential?, sel_die?) -> List[List[Address]]` (`addrman.py:630`)
- `Validator`
  - `validate(batch, res_view, cfg) -> ValidationReport` (EPR 포함; RM 내부 훅은 no-op 게이팅)

이 포트들만 사용하면 Proposer 로직(확률 샘플링/시퀀스 전개/윈도우 스캔/사전검증)이 Proposer 내부에 국한되어 RM/AM 변경을 요구하지 않는다.

### 인터페이스 스케치(제안)
- DTO
  - `Address(die:int, plane:int, block:int, page:Optional[int])` (`resourcemgr.py:12` 유사)
  - `StateSeg(name:str, dur_us:float, bus:bool=False)`
  - `ProposedOp(op_name:str, base:str, targets:List[Address], scope:Scope, start_us:float)`
  - `ProposedBatch(ops:List[ProposedOp], source:str, hook:dict)`
- Proposer API
  - `propose(now:float, hook:dict, cfg:Cfg, res:ResourceView, addr:AddressSampler, rng:Rng) -> ProposedBatch | None` (전부 수락 또는 전부 거절)

### 동작 흐름(PRD 정합)
1) `Scheduler`가 이벤트 훅에서 `propose` 호출 (`docs/PRD_v2.md:261`)
2) Proposer는 `op_state` 확인 후 `CFG[phase_conditional]` 기반 후보 집합 구성 (`docs/PRD_v2.md:259`, `docs/PRD_v2.md:263`)
3) `exclusions_by_*`/cache/ODT/suspend는 RM 상태 질의로 후보에서 제거 (`docs/PRD_v2.md:263-265`)
4) 후보 `op_name` 선택 후, 필요 시 AM의 `sample_*`로 타깃 주소 샘플링(멀티플레인 포함) (`docs/PRD_v2.md:266`)
5) 시퀀스가 있는 경우 `CFG[op_specs][op_name][sequence]`와 `CFG[generate_seq_rules]`로 후속 op 전개 (`docs/PRD_v2.md:267-277`)
6) 가안 배치에 대해 Validator 사전 검증(윈도우 내 동일 틱 전부/없음 원칙) (`docs/PRD_v2.md:256-258`, `docs/PRD_v2.md:278-279`)
7) 통과 시 `ProposedBatch` 반환, 실패 시 대안 샘플 재시도(`CFG[policies][maxtry_candidate]`) (`docs/PRD_v2.md:265`, `docs/PRD_v2.md:278`)

### RM/AM에 복잡성 전파를 막는 포인트
- RM
  - Proposer는 `reserve/commit`를 직접 호출하지 않는다 → 상태 변경은 Scheduler의 트랜잭션 경계에서만 발생(`resourcemgr.py:335`, `resourcemgr.py:363`, `resourcemgr.py:415`).
  - 사전 검증은 `feasible_at`/조회 조합으로 충분하며, 상세 규칙(EPR/상태금지)은 Validator에서 처리 → RM은 기존 스켈레톤 유지(`resourcemgr.py:884` 이후 규칙 훅).
- AM
  - Proposer는 `sample_*`만 사용하고 `apply_*`는 Scheduler 커밋 시점에서만 호출하여 상태 변이가 Proposer에 누수되지 않음 (`addrman.py:471`, `addrman.py:606`).
  - EPR 규칙은 AM의 순수 `check_epr`로 제공되어, RM에서는 콜백 주입만 수행(`addrman.py:1200`, `resourcemgr.py:858`).

### 의존성 주입(Decoupling)
- RNG: `(global_seed, hook_counter)` 분기 스트림을 Proposer에 주입(시스템 시간 금지) (`docs/PRD_v2.md:220-221`, `docs/PRD_v2.md:283`).
- CFG: 정규화된 뷰에서 `op_specs/groups_by_base/phase_conditional/generate_seq_rules/policies`만 읽기 (`docs/PRD_v2.md:165`, `docs/PRD_v2.md:251-277`).
- ResourceView/AddressSampler/Validator: 인터페이스에만 의존(구현체 교체 용이; 테스트에서 페이크 주입 가능).

## 코드 참조
- `docs/PRD_v2.md:248` - Proposer 섹션 시작 및 워크플로
- `docs/PRD_v2.md:285` - ResourceManager 책임과 상태 항목
- `resourcemgr.py:338` - `feasible_at`(사전 슬롯 검증용)
- `resourcemgr.py:363` - `reserve`(Scheduler 경계에서만 사용)
- `resourcemgr.py:512` - `odt_state`(상태 금지 질의)
- `resourcemgr.py:521` - `cache_state`(상태 금지 질의)
- `resourcemgr.py:570` - `suspend_states`(상태 금지 질의)
- `addrman.py:342` - `from_topology`(AM 초기화)
- `addrman.py:412` - `sample_erase`
- `addrman.py:492` - `sample_pgm`
- `addrman.py:630` - `sample_read`
- `addrman.py:1200` - `check_epr`(AM EPR 규칙 진입점)

## 아키텍처 인사이트
- 복잡성 분리는 “순수(샘플/검증)”와 “커밋(상태변경)”의 물리적 경계에서 확보된다. Proposer는 순수 함수 경계를 유지하고, Scheduler가 트랜잭션을 소유한다.
- `ResourceView`와 `AddressSampler`는 테스트 가능성이 높은 포트로서, RM/AM의 내부 표현 변경이 Proposer에 전파되지 않게 한다.
- EPR/상태금지와 같은 규칙은 Validator에 집중시키고, RM/AM은 콜백 게이트/조회만 제공한다(최소 권한).

## 역사적 맥락(thoughts/ 기반)
- `research/2025-09-02_23-58-12_interfaces.md` - 요소별 인터페이스 분리 전략과 Proposer API 스케치 재확인.
- `research/2025-09-04_22-04-48_validator_in_resourcemgr.md` - Validator를 RM에 통합하되, EPR은 AM 콜백으로 분리하는 결정.

## 관련 연구
- `research/2025-09-02_23-58-12_interfaces.md`

## 미해결 질문
- Admission window 내 다수 후보가 있는 경우 탐색 전략(최적/탐욕/확률 혼합)을 어떻게 구성할지? 성능/다양성 트레이드오프.
- 시퀀스 전개 시 부분 실패 발생 시 재샘플링의 범위(전부 재샘플 vs 일부만 대체)와 재시도 한도 정책(`CFG[policies][maxtry_candidate]`)의 튜닝.
- 단일 틱 내 “전부/없음” 원칙을 지키면서, 예약된 작업과 런타임 제안의 우선순위 정책(오픈 이슈; `docs/PRD_v2.md:421-424`).

## 후속 연구 2025-09-04T23:38:58+09:00 — Admission window 내 다수 후보 탐색 전략

### 연구 질문
Admission window(`[t, t+W)`) 내에서 다수 후보(operation/address/sequence)가 존재할 때, 어떤 탐색 전략(최적/탐욕/확률 혼합)을 사용해 가장 이른 무충돌 슬롯을 선정할 것인가? 재현성, 성능, 다양성 간 트레이드오프를 고려한다.

### 요약(권장안)
- 기본: 확률 가중 후보 Top-N(작은 N) + Earliest-Feasible Greedy.
  - 후보는 `phase_conditional` 확률로 정렬 후 상위 N만 평가.
  - 각 후보에 대해 주소 샘플(AM `sample_*`) → `RM.feasible_at`로 earliest t0 ∈ `[t, t+W)` 계산.
  - 시퀀스가 있으면 전체 시퀀스 시간을 전개하고 사전검증(Validator) 후 전부/없음 원칙 준수.
  - 선택 기준: earliest t0(시간 우선) → 동률 시 확률 가중 무작위(훅별 RNG)로 tie-break.
- 혼합: ε-greedy(소량 탐색) 옵션을 두되, 기본 ε=0(완전 탐욕)로 시작. 설정으로 점진 도입.
- 캡: `CFG[policies][maxtry_candidate]`와 연동하여 후보 평가 수를 상한.

### 제약/정합성
- PRD 윈도우/결정성: `docs/PRD_v2.md:215-221` — 동일 틱 결정성, window 기반 탐색, RNG 분기 규칙 준수.
- 전부/없음: `docs/PRD_v2.md:219`, `docs/PRD_v2.md:256-258` — 배치 단위 원자성 보장.
- RM 사전 검증: 무충돌 슬롯 탐색은 RM 상태 질의로 일관 구현(`resourcemgr.py:338`).

### 알고리즘 대안 비교
1) Earliest-only Greedy(단일 후보)
   - 장점: 단순/고속/결정성 높음
   - 단점: 다양성 낮음, 지역 최적화에 갇힘
   - 위험: 희귀 후보가 장기간 배제될 수 있음

2) Top-N Greedy(권장)
   - 장점: 다양성 확보와 성능 균형, 구현 단순
   - 단점: N 튜닝 필요, 여전히 근사해
   - 위험: 큰 N은 지연 증가; 작은 N은 탐색 부족

3) Weighted Best-of-K(확률 가중으로 K 샘플)
   - 장점: 분포 보존, 소폭의 탐색성
   - 단점: 샘플 복원·중복 처리, 재현성 관리 필요
   - 위험: K가 커지면 비용 상승

4) Beam Search(시퀀스 전개 동시 최적화)
   - 장점: 시퀀스 품질 향상
   - 단점: 구현 복잡/비용 큼
   - 위험: 틱 시간 초과, 결정성 관리 부담

5) MAB/우선순위 적응(bandit)
   - 장점: 장기 효율 개선
   - 단점: 상태/통계 관리 필요, 초기 비결정성 인상
   - 위험: PRD 결정성 요구와 충돌 소지(시드로 결정화 가능하나 복잡)

결정: 2) Top-N Greedy 채택, 선택·타이브레이크는 확률 가중을 존중. ε 탐색은 off 기본.

### 의사코드(훅 처리)
```
def propose(now, hook, cfg, res: ResourceView, addr: AddressSampler, rng):
    t0 = now; W = cfg.policies.admission_window_us
    cand = generate_candidates(hook, cfg.phase_conditional, res, rng)  # [(op_name, prob, scope)]
    cand = filter_by_states(cand, res, cfg)  # exclusions_by_* / cache/odt/suspend
    cand = sorted(cand, key=lambda x: -x.prob)[:cfg.policies.topN]  # small N
    best = None
    for c in cand:
        targets = sample_targets(c, addr, cfg, rng)  # AM sample_* only
        if not targets: continue
        op = build_op(c.op_name, targets, cfg)
        t_feas = res.feasible_at(op, targets, start_hint=t0, scope=c.scope)
        if t_feas is None or t_feas >= t0 + W: continue
        seq = expand_sequence_if_any(op, cfg, rng)
        if not validator_preflight(seq, res, cfg, window=(t0, t0+W)): continue
        if (best is None) or (t_feas < best.t):
            best = (t_feas, seq)
        elif best and (t_feas == best.t):
            # tie-break by prob-weighted RNG
            if rng.random() < c.prob / (c.prob + best.prob):
                best = (t_feas, seq)
        if tried() >= cfg.policies.maxtry_candidate: break
    return batch_from(best) if best else None
```

### 구성/튜닝 포인트(신규/활용)
- `policies.admission_window_us` — 윈도우 폭(`docs/PRD_v2.md:217`).
- `policies.topN` — Top-N 후보 평가 상한(기본 4~8 권장).
- `policies.maxtry_candidate` — 재시도 한도(기존 활용; `docs/PRD_v2.md:278`).
- `policies.epsilon_greedy` — ε 탐색 비율(기본 0.0).

### 메트릭/관측(권장)
- per-hook: 시도 후보 수, 성공/실패 사유 분포(planescope/bus/excl/latch/epr), 선택된 시작시각 히스토그램.
- 전역: 성공률, 평균 탐색 비용, 후보 다양도(선택된 op_name/plane_set의 엔트로피).

### 코드 참조
- `docs/PRD_v2.md:215-221` — Admission window/결정성/RNG 분기.
- `docs/PRD_v2.md:256-258` — 시퀀스 전개와 사전검증.
- `resourcemgr.py:338` — `feasible_at`로 earliest feasible time 계산.
- `resourcemgr.py:486` — `has_overlap`(필요 시 추가 제약 확인에 활용 가능).
- `addrman.py:412`, `addrman.py:492`, `addrman.py:630` — 주소 샘플 API.

