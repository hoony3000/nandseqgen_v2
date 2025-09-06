title: 계획 — 집계 전용 Virtual END (Q1)
date: 2025-09-06
owner: Codex CLI
status: proposed
related:
  - research/2025-09-06_15-26-30_followup_q1_op_state_end_aggregation.md
  - research/2025-09-06_15-01-20_op_state_timeline_end_risks.md
  - docs/PRD_v2.md
  - docs/TODO.md
---

# 목표
내부 상태 타임라인이나 proposer 동작을 바꾸지 않고도, 제안 시점(proposal‑time)의 (op_state × op_name) 집계를 안전한 가상 `<BASE>.END` 분류와 함께 안정적으로 제공한다.

# 비목표
- RM 타임라인에 END 세그먼트를 실제로 추가하지 않는다.
- proposer의 phase key 선택 또는 라이브 스케줄링 동작을 변경하지 않는다.
- 기존 CSV 스키마를 깨지 않는다(새 CSV는 추가만 한다).

# 접근 개요
- “집계 전용 Virtual END”(연구안 A) 채택.
- 시각 `t`에서 직전 세그먼트를 이용해 가상 phase key를 계산하는 RM 헬퍼를 추가(세그먼트 밖이면 `<BASE>.END`).
- 가상 키 재현을 위해 제안 시점 최소 컨텍스트를 row에 기록.
- “사용된 키”와 “가상 키”를 모두 집계하는 신규 exporter 추가.

# 산출물
- 신규 API: `ResourceManager.phase_key_at(die, plane, t, default="DEFAULT", derive_end=True) -> str`.
- Row 컨텍스트 전파: 제안 정보가 스케줄러/로우를 통해 보존·내보내짐.
- 신규 CSV: `phase_proposal_counts_YYMMDD_RUNID.csv`
  - 컬럼: `phase_key_used, phase_key_virtual, die, plane, propose_time, op_name, count`.
- 문서화(PRD 주석): END는 집계/분석 전용이며, 기본 타임라인에는 포함하지 않음.

# 구현 계획
1) RM 헬퍼(≤30 LOC)
   - 파일: `resourcemgr.py`
   - 메서드: `ResourceManager.phase_key_at(die:int, plane:int, t:float, default:str="DEFAULT", derive_end:bool=True) -> str`
   - 로직:
     - `t = quantize(t)`; `st = self._st.state_at(die, plane, t)` → 값이 있으면 그대로 반환.
     - `t` 시점에 세그먼트가 없고 `derive_end=True`면, 이진 탐색으로 `t` 이전 마지막 세그먼트를 찾음. 존재하고 `seg.end_us ≤ t`면 `f"{seg.op_base}.END"` 반환, 아니면 `default`.
   - 제약: ≤50 LOC 유지, 기존 starts 인덱스 재사용, 타 모듈 동작 변화 없음.

2) 제안 컨텍스트 최소 기록
   - 파일: `scheduler.py` (`_propose_and_schedule`)
     - 각 `resv_records[]`에 아래를 함께 보존:
       - `phase_key`(이미 산출)
       - `phase_hook`: 훅에 포함된 `{die, plane, label}`(있을 때만)
       - `propose_now`: 제안에 사용된 현재 `now`
   - 파일: `main.py` → `InstrumentedScheduler._emit_op_events`
     - 각 row에 위 필드 포함(예: `phase_key`, `phase_hook_die`, `phase_hook_plane`, `phase_hook_label`, `phase_key_time`).
   - 참고: 스케줄링 로직 변화 없음. 순수 분석/수출용 필드.

3) 신규 Exporter: Proposal Counts
   - 파일: `main.py`
   - 함수: `export_phase_proposal_counts(rows, rm: ResourceManager, *, out_dir: str, run_idx: int) -> str`
   - 동작:
     - 각 row에 대해, 훅 컨텍스트(`phase_hook_*`)가 있으면 `(d,p,t)`로 사용, 없으면 오퍼의 `die,plane,start_us`로 폴백.
     - `used = row.get('phase_key', 'DEFAULT')`
     - `virt = rm.phase_key_at(d, p, t)`
     - 키 `(used, virt, d, p, op_name)`로 카운트하여 `[phase_key_used, phase_key_virtual, die, plane, propose_time, op_name, count]` 필드로 CSV 기록.
   - 정렬: `(die, plane, op_name, phase_key_used, phase_key_virtual)`.

4) CLI 플로우에 연결
   - 파일: `main.py`
   - 기존 export 뒤에 `export_phase_proposal_counts(...)` 호출 및 경로 출력.
   - 기본적으로 추가적이며 플래그 없이 작동(행동 변화 없음).

5) 문서 업데이트
   - PRD 주석: END는 집계용 가상 분류이며, 내부 타임라인은 기본적으로 유한/비중첩을 유지함을 명시.
   - docs/TODO.md: END 관련 연구 항목을 본 계획으로 처리(구현 대기) 표시.

# 테스트 계획
- 경량 단위 검증:
  - 오퍼 사이의 빈 구간에서 제안이 발생하는 짧은 시나리오 구성. `op_state(...)`는 `None`, `phase_key_at(...)`은 `<last_base>.END` 반환을 확인.
  - 선행 세그먼트가 전혀 없는 경우 예외 없이 `DEFAULT` 반환 확인.
- CSV 검증:
  - `--pc-demo` 프리셋으로 실행해 `phase_proposal_counts_*.csv` 생성 및 used/virtual 키 모두 존재 확인.
  - 동일 시드에서 기존 CSV들의 스키마/정렬이 변하지 않음을 확인.
- 로그 상관:
  - `phase_key_used` 값이 `out/proposer_debug_*.log`에 기록된 phase key 선택과 일치함을 대조.

# 수용 기준
- `ResourceManager.phase_key_at(...)`이 아래를 만족:
  - 세그먼트 내부: `op_state(...)`와 동일한 `BASE.STATE` 반환.
  - 과거 세그먼트가 있는 빈 구간: `<LAST_BASE>.END` 반환.
  - 선행 세그먼트 없음: `DEFAULT` 반환.
- 실행이 존재하는 런에서 `phase_proposal_counts_*.csv`가 생성되고 비어 있지 않음.
- proposer 동작/기존 CSV 스키마 불변, 시각화 정상 렌더링.

# 위험 및 완화
- 훅 컨텍스트 부재 시 `(d,p,t)` 오인식 가능 → `start_us` 폴백은 세그먼트 내부로 분류될 수 있음. 단기적으로 허용, 가능하면 훅 컨텍스트를 우선 사용.
- 성능: 메모리 내 조회와 이진 탐색만으로 영향 미미.
- “used vs virtual” 키 혼동 가능 → 두 컬럼을 모두 유지하고 문서로 명확화.

# 비교한 대안
- 실제 END 세그먼트 추가: 중첩/무한 duration/다모듈 회귀 위험으로 기각.
- proposer가 빈 구간에 `.END`를 내보내도록 변경: 기능 변화로 기각.

# 예상 공수
- RM 헬퍼: 0.5h
- 컨텍스트 전파 + exporter: 1.5–2h
- 문서 + 검증 패스: 0.5h

# 롤백 계획
- 변경은 추가적 성격. 롤백 시 exporter 호출과 row 필드 추가를 제거하고, `phase_key_at`는 무해한 유틸로 남긴다.
