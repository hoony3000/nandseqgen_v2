# NANDSim 리팩터링 계획 — Sampling, Validation, Scheduling, Unified Resources

작성자: Engineering
상태: 초안(구현 준비 완료)

## 1) Problem 1-Pager
- 배경: `nandsim_demo.py`에는 주소 샘플링, 검증/제외/의무(Obligation), 스케줄링/버스 게이팅이 얽혀 있는 대형 구현이 있습니다. 이제 독립 모듈 `addrman.py`가 다이 인지(die-aware) 멀티‑플레인 샘플링(`random_*`)을 빠르게 제공하므로, 이를 시뮬레이터에 도입하면서 구조를 유지보수 가능하고 테스트 친화적으로 개편하려 합니다.
- 문제:
  - 샘플링/검증/스케줄링 로직이 결합되어 있어 각각을 독립적으로 테스트하기 어렵습니다.
  - 주소/상태 모델이 중복되거나 일부 겹칩니다(데모 내부 AddressManager vs `addrman.py`).
  - 리소스 관리(다이/플레인 가용시간, 버스 예약)가 분산되어 있습니다.
  - 동시성, 멀티‑플레인 의미론, 다이‑와이드 범위를 파악하기 어렵습니다.
- 목표:
  - 코드를 Sampling, Validation, Scheduling의 3계층으로 분리하고, 통합 `ResourceManager`를 둡니다.
  - 다이/멀티‑플레인 의미론을 포함한 주소 선택을 `addrman.py`로 일원화합니다.
  - 좁고 테스트 가능한 인터페이스, 결정적 동작(시드 고정 RNG, 순수 헬퍼)을 보장합니다.
  - 반환 형태 일관성: 샘플 주소는 (die, block, page), 형태는 `(#, 1|k, 3)`입니다.
- 비목표:
  - 인터페이스 분리를 넘는 정책/타이밍 모델 전면 재설계
  - 필요한 플래그(예: dies) 외 외부 포맷/UX 변경
- 제약:
  - 함수 ≤ 50 LOC, 낮은 순환복잡도, 명시적 코드, 매직 없음
  - 보안/로깅: 구조화 로그, 비밀정보 금지
  - 테스트: 결정적/독립, 외부 의존은 목/계약 테스트

## 2) 현재 상태 요약
- `nandsim_demo.py`는 커밋/퓨처 레일, 플레인 반복자, 버스 게이팅, 상태 관찰 유틸 등을 갖춘 자체 AddressManager(v2)를 포함합니다.
- `addrman.py`는 다음을 제공합니다:
  - `random_erase/random_pgm/random_read`가 (die, block, page)을 반환하며 `sel_plane`, `sel_die` 필터를 지원
  - 토폴로지: `num_blocks`=다이당 블록 수, `num_dies`=다이 수. `blocks_per_die % num_planes == 0`이면 멀티‑플레인 그룹이 다이 경계를 넘지 않음 보장
  - 멀티‑플레인 의미론: 한 다이 내 동일 스트라이프 인덱스에서 선택 플레인마다 정확히 한 블록
- 중복/충돌 위험: 두 주소/상태 소스, 커밋/퓨처(데모) vs 즉시 적용(`addrman.py`) 모델 상이

## 3) 목표 아키텍처
- sampling/
  - `Sampler`: `addrman.AddressManager`를 래핑하여 시뮬레이터용 안정 API 제공
  - 역할: RNG 시드, 단일/멀티‑플레인 × 다이 샘플링, 메타데이터(oversample, counts) 노출, 시뮬레이터 리소스 불변
- validation/
  - `Validator`: Exclusion/Obligation/속성 규칙(mode/offset/page 경계) 조합
  - 역할: 순수 체크와 필터 출력, 스케줄링 부수효과 없음
- scheduling/
  - `Scheduler`: 시간 전진, 버스 예약, (die,plane) 가용성 관리
  - 역할: Sampler에게 후보 요청 → Validator 통과 → Unified Resource로 예약 → 스케줄 결과 산출
- core/resources/
  - `ResourceManager`: (die,plane) 가용성, 글로벌 버스, 락/래치, (선택) 커밋/퓨처 어댑터의 단일 소스
- core/types/
  - `Address(die,int, block,int, page,int)`, `PlaneSet(list[int])`, `OpSpec(name, mode, size, sequential, sel_plane, sel_die)`
- io/config & logging/
  - 경량 매핑, 요청 ID가 있는 구조화 로깅

## 4) 모듈 인터페이스(제안)
- sampling/sampler.py
  - class Sampler:
    - ctor(cfg, addr: AddressManager)
    - `sample_erase(op: OpSpec) -> np.ndarray  # (#, k, 3)`
    - `sample_pgm(op: OpSpec) -> np.ndarray`
    - `sample_read(op: OpSpec) -> np.ndarray`
    - 비고: `random_*` 래핑; `sel_plane`, `sel_die`, `size`, `sequential`, `mode`, `offset` 지원
- validation/validator.py
  - class Validator:
    - ctor(cfg, excl: ExclusionManager, obl: ObligationManager)
    - `validate_erase(addrs, op) -> tuple[bool, np.ndarray|None, list[str]]`
    - `validate_pgm(addrs, op) -> ...`
    - `validate_read(addrs, op) -> ...`
    - 비고: 순수 필터/사유 리포트; 상태 변경 없음; (die,block,page) 수용
- core/resources.py
  - class ResourceManager:
    - Tracks: availability[(die,plane)], bus_reservations, optional future/committed state adapter.
    - `can_reserve(op, addrs, at_time)->bool` and `reserve(op, addrs, at_time)`
    - `available_at(die, plane)->float` and helpers.
    - (선택) (die,block,page) → 데모의 future/committed 레일 매핑 어댑터
- scheduling/scheduler.py
  - class Scheduler:
    - ctor(cfg, sampler, validator, resources, policy_engine)
    - `plan(op: OpSpec, now_us: float) -> ScheduledOp | None`
    - `tick(now_us: float)`; applies policy to select next ops and commit reservations.

## 5) 멀티‑플레인 + 다이 의미론(상세)
- 토폴로지:
  - `blocks_per_die = cfg.num_blocks`(다이당 블록 수), `total_blocks = dies × blocks_per_die`
  - `die_index = global_block // blocks_per_die`, `within_die = global_block % blocks_per_die`, `plane_index = within_die % num_planes`
- 멀티‑플레인 그룹핑:
  - `groups = arange(total_blocks).reshape(-1, num_planes)`로 구성하면, 각 행은 동일 다이의 동일 스트라이프 인덱스에서 플레인별 블록 1개씩을 담습니다.
  - 제약: `blocks_per_die % num_planes == 0`이면 행이 다이 경계를 넘지 않습니다. 아니면 조기 실패시킵니다.
- 샘플링 조건:
  - random_erase: 선택 플레인 블록 모두 `!= BAD` 및 `!= ERASE`
  - random_pgm: 선택 플레인 블록 모두 상태 동일, `[ERASE .. pagesize-2]` 범위, 모드 일치
  - random_read: 선택 플레인 블록 모두 `state > ERASE`, `state >= offset`, 모드 일치; 읽기 용량은 플레인 최소값 기준
- 다이 필터:
  - 기본(`sel_die=None`)은 다이 가로 샘플링. 값이 주어지면(정수/리스트) 해당 다이의 행만 유지합니다.
- 출력:
  - 항상 `(die, block, page)`, 형태는 `(#, 1|k, 3)`; plane은 `block % num_planes`로 유도 가능

## 6) 마이그레이션 계획(단계별)
- Phase 0 — Foundations
  - Add new packages: `sampling/`, `validation/`, `scheduling/`, `core/` (or split `nandsim_demo.py` into these modules).
  - Introduce types: `Address`, `OpSpec` (light dataclasses or typed dicts), `ResourceManager` skeleton.
  - Wire `--dies` in any CLIs (bench already supports it).
- Phase 1 — Sampling Integration
  - Create `Sampler` that wraps `addrman.AddressManager`.
  - Replace demo’s internal candidate enumeration pathways with `Sampler.sample_*` calls in one or two code paths behind a feature flag (e.g., `CFG['feature']['sampler_v2']=True`).
  - Return addresses as `(die,block,page)`; adapt downstream consumers (mostly mapping to plane via `block % planes`).
- Phase 2 — Validation Extraction
  - Move exclusion/obligation checks into `Validator` with pure interfaces.
  - Replace inline checks with `Validator.validate_*` results; preserve reason codes for debugging.
- Phase 3 — Unified Resource Manager
  - Centralize per‑(die,plane) availability and global bus reservations in `ResourceManager`.
  - Migrate methods like `available_at`, `bus_precheck`, `bus_reserve` from demo to `ResourceManager`.
- Phase 4 — Scheduler Isolation
  - Refactor to `Scheduler.plan/tick`, consuming `Sampler`, `Validator`, and `ResourceManager`.
  - Keep `PolicyEngine` intact but decouple from raw state arrays; pass `OpSpec` + filtered addresses.
- Phase 5 — Remove Legacy Paths
  - Delete or deprecate the demo’s internal AddressManager and candidate expansion.
  - Narrow `nandsim_demo.py` to orchestration and visualization hooks.

## 7) API 스케치(예시)
```python
# core/types.py
@dataclass(frozen=True)
class Address:
    die: int; block: int; page: int

@dataclass
class OpSpec:
    name: str           # ERASE|PGM|READ
    mode: str           # TLC|SLC|...
    size: int = 1
    sequential: bool = False
    sel_plane: list[int] | None = None
    sel_die: int | list[int] | None = None
    offset: int | None = None   # for READ
```
```python
# sampling/sampler.py
class Sampler:
    def __init__(self, addr: AddressManager): self.addr = addr
    def sample_erase(self, op: OpSpec) -> np.ndarray:
        return self.addr.random_erase(sel_plane=op.sel_plane, mode=op.mode, size=op.size, sel_die=op.sel_die)
    def sample_pgm(self, op: OpSpec) -> np.ndarray:
        return self.addr.random_pgm(sel_plane=op.sel_plane, mode=op.mode, size=op.size, sequential=op.sequential, sel_die=op.sel_die)
    def sample_read(self, op: OpSpec) -> np.ndarray:
        return self.addr.random_read(sel_plane=op.sel_plane, mode=op.mode, size=op.size, sequential=op.sequential, offset=op.offset, sel_die=op.sel_die)
```
```python
# validation/validator.py
class Validator:
    def __init__(self, excl, obl): self.excl=excl; self.obl=obl
    def validate_pgm(self, addrs: np.ndarray, op: OpSpec) -> tuple[bool, np.ndarray, list[str]]:
        # filter via exclusion rules, obligations; return reasons
        ...
```
```python
# scheduling/scheduler.py
class Scheduler:
    def __init__(self, resources, sampler, validator, policy): ...
    def plan(self, op: OpSpec, now_us: float):
        cand = self.sampler.sample_pgm(op)  # example
        ok, addrs, reasons = self.validator.validate_pgm(cand, op)
        if not ok: return None
        if not self.resources.can_reserve(op, addrs, now_us): return None
        return self.resources.reserve(op, addrs, now_us)
```

## 8) 테스트 전략
- Unit tests per layer:
  - Sampler: deterministic sampling with fixed seeds; shape/filters (sel_plane/die) correctness.
  - Validator: rule coverage including failure paths and boundary conditions.
  - ResourceManager: bus collision detection, availability propagation, multi‑plane/die gating.
  - Scheduler: simple policy with known outcomes.
- Contract tests: ensure `(die,block,page)` is consistently handled across all layers.
- E2E: a minimal config with 2 dies × 2 planes × few blocks; include at least one failure and one success path.

## 9) 성능 & 동시성
- Use vectorized sampling from `addrman.py` to avoid candidate expansion.
- Keep `ResourceManager` decisions O(log N) or O(1) per reservation; avoid per‑page loops.
- Concurrency hazards: ensure multi‑plane rows never span dies; check lock/availability updates atomically within `reserve()`.

## 10) 위험 & 완화
- Divergent state models (demo’s committed/future vs. `addrman.py` immediate apply):
  - Mitigation: keep `addrman.py` for candidate selection; scheduling apply updates live in `ResourceManager` (future rails). Only persist back to `addrman.py` if we intentionally simulate state mutation at commit.
- API churn:
  - Mitigation: adapters and feature flags; migrate incrementally.
- Grouping assumptions:
  - Mitigation: validate `blocks_per_die % num_planes == 0` early; assert in CI.

## 11) 점진적 적용(마일스톤)
1) Land `Sampler`, wire to demo behind flag; add unit tests.
2) Extract `Validator`; move exclusion/obligation checks; add tests.
3) Implement `ResourceManager`; migrate availability/bus; add tests.
4) Introduce `Scheduler.plan/tick`; connect to `PolicyEngine`; add E2E.
5) Delete legacy candidate generation; update docs; enable new path by default.

## 12) 오픈 쿼스천
- 스케줄링에서 "apply" 동작이 `addrman.py`의 상태(시각화를 위해)를 실제로 변경해야 할까, 아니면 `ResourceManager` 안에서만 별도로 유지하고 필요할 때만 선택적으로 반영하는 게 맞을까? -> 논의 필요
- READ와 PGM 간의 겹침(overlap)에 필요한 불변 조건은 무엇일까? (예: 미래의 rail 에서 PGM 이 예약된 블록에서는 READ가 금지되는 것처럼?) -> 논의 필요
- 멀티플레인(multi-plane) PGM 동작에서 다이(die) 간의 동일성을 얼마나 엄격하게 요구해야 할까? (현재는 그룹 내에서 단일 다이까지만 제한하고 있는데, 이 방식을 유지하는 것이 권장되는가?) -> 그룹 내에서 단일 다이까지 제한 유지.
