---
date: 2025-09-06T15:26:30+09:00
researcher: Codex CLI
branch: main
repository: nandseqgen_v2
topic: "Follow‑up: Q1 — END 목적과 안전한 집계 설계"
tags: [research, design, ResourceManager, proposer, exporters, metrics]
status: draft
last_updated: 2025-09-06
last_updated_by: Codex CLI
---

# Follow‑up Research: Q1 — op_state.END의 목적과 안전한 집계 설계

## Problem 1‑Pager
- 배경: PRD §5.5에 "각 예약 오퍼레이션의 logic_state 뒤에 op_name.END를 무한 꼬리로 추가" 요구가 있음. 기존 연구에서 이는 내부 타임라인(유한·비중첩)과 충돌하고, proposer/시각화/스냅샷에 부작용을 유발할 수 있음을 확인.
- 문제: Q1의 1차 목적은 "어떤 operation이 어떤 op_state에서 제안됐는지 집계하여 다양성 정도를 보고"임. 이 목적 달성을 위해 END 개념이 필요한지, 필요하다면 어디에서 어떻게 안전하게 도입할지 불명확.
- 목표: 내부 RM 구조를 바꾸지 않고도 제안 시점의 상태 분포(옵션: 가상 END 포함)를 안정적으로 집계/내보내는 설계를 제시.
- 비목표: proposer의 의사결정 로직(phase_conditional 키 선택)을 변경하거나 동작 분포를 실제로 바꾸는 것.
- 제약: 파일 ≤300 LOC, 함수 ≤50 LOC 유지. 기존 CSV 스키마는 파괴적 변경 금지. 신규 산출물은 추가로 제공.

## 현행 동작 요약(근거 코드)
- 상태 조회: `resourcemgr.py:483` `ResourceManager.op_state(die, plane, at_us)` → 내부 `_StateTimeline.state_at` 사용(`resourcemgr.py:30`).
- proposer phase key: `proposer.py:564` `_phase_key(...)`가 `res.op_state(...)` 결과를 우선 사용, 없으면 훅 라벨(BASE.STATE) 또는 `DEFAULT`.
- 타임라인/집계 Exporters:
  - `main.py:156` `export_operation_timeline(...)`: 각 row에 `op_state`를 기록하되 `rows[].phase_key`를 우선, 없으면 `rm.op_state(die, plane, start)` 폴백.
  - `main.py:195` `export_op_state_timeline(...)`: RM 스냅샷 타임라인을 그대로 CSV로 변환(END 없음).
  - `main.py:304` `export_op_state_name_input_time_count(...)`: 오퍼 시작 시각이 포함된 세그먼트 기준으로 `(op_state, op_name, input_time)` 카운트. 빈 구간이면 스킵.
- 현 시료: `out/operation_timeline_250906_0000001.csv:1` 에서 `op_state=DEFAULT`만 관찰됨(phase_conditional 키 자체가 DEFAULT만 사용된 상황).

## 관찰과 해석
- 제안 시점(now) 기준 분포를 알고 싶다면, "오퍼 시작 시각" 기반 집계만으로는 빈 구간(상태 밖) 맥락을 포착할 수 없음. 또한 proposer가 사용하는 `phase_key`를 그대로 바꾸면 기능 변경 위험.
- 따라서 "집계 전용 가상 END"를 도입해, 조회/집계 경로에서만 END를 파생시키는 접근이 안전함(내부 타임라인 변경 금지).

## 대안 비교
- 접근 A(권장): 집계 전용 가상 END
  - 요점: RM에 조회 유틸을 추가해 now 시점 상태가 None이면 직전 세그먼트의 BASE로 `<BASE>.END`를 반환. proposer 사용 경로와 분리해 exporter/분석에서만 사용.
  - 장점: 내부 타임라인 유한성 유지, 회귀 위험 최소. 시각화/스냅샷 영향 없음.
  - 단점: 가상 상태이므로 타임라인 자체에는 존재하지 않음(개념 분리 필요).
- 접근 B: END를 실제 세그먼트로 추가/유지
  - 장점: PRD 모델과 형식적으로 일치.
  - 단점: 중첩/무한 duration/정렬/축 계산/제안 키 변경 등 다수 회귀 위험. 구현 복잡.

결론: A 선택(집계 계층에서만 END 가상화).

## 제안 설계(접근 A 상세)
1) RM 조회 유틸 추가(집계/분석 전용)
   - 시그니처(아이디어):
     - `ResourceManager.phase_key_at(die:int, plane:int, t:float, *, default:str="DEFAULT", derive_end:bool=True) -> str`
   - 동작:
     - `st = self.op_state(die, plane, t)`이 있으면 그대로 반환.
     - 없고 `derive_end=True`면, 내부 인덱스로 직전 세그먼트(시작≤t 인 최대 index)를 찾아 `f"{seg.op_base}.END"` 반환. 없으면 `default`.
   - 구현 포인트: `_StateTimeline`의 시작시각 리스트에 `bisect_right(starts, t)-1`로 index 계산하여 `seg.end_us<=t` 확인. LOC≈20 내 구현 가능.

2) proposer 키와의 분리(기능 불변)
   - `proposer.py:564` `_phase_key`는 그대로 유지해 동작 분포에 영향 없음.
   - 집계/분석에서만 `phase_key_at(...)`을 사용해 가상 END를 생성.

3) 신규 Exporter(제안 분포 관측치)
   - 목적: "제안 시점(now) 기준 (가상)op_state × op_name 분포"를 CSV로 제공.
   - 최소안(선정된 오퍼 기준):
     - 스케줄러가 이미 `rows[].phase_key`를 담고 있음(제안 시 사용 키). 여기에 제안 당시 `now`와 훅 컨텍스트(die, plane)를 함께 기록하면, `phase_key_at(d,p,now)`로 가상 END 키도 병렬 산출 가능.
     - 스키마(예시): `phase_key_used, phase_key_virtual, die, plane, propose_time, op_name, count`
   - 확장안(시도된 후보까지):
     - `proposer.propose()`의 `metrics['attempts']`(이름/이유/예정 시작 t0 포함)를 구조화하여, "시도된(op_name)×phase_key_virtual" 분포를 추가 집계.
   - 구현 위치:
     - `scheduler.py:_propose_and_schedule`에서 `resv_records`에 `hook`(die,plane,label)과 `propose_now`를 보존.
     - `InstrumentedScheduler._emit_op_events`에 위 항목을 row 필드로 전달.
     - `main.py`에 `export_phase_proposal_counts(...)`(신규) 추가: 위 row들을 사용해 제안 시점 분포 집계. END는 `rm.phase_key_at(d,p,propose_time)`로 가상화.

4) input_time 처리 방안(옵션)
   - 실제 상태 세그먼트 내에 있을 때만 `(t - s0)/(s1 - s0)`로 정의. 가상 END에는 `1.0` 또는 `NA` 표기. 혼동 방지를 위해 `state_kind` 컬럼(SEG|END)을 함께 추가 권장.

## 구현 스케치(의사코드 수준)
```
# resourcemgr.py (새 메서드)
def phase_key_at(self, die:int, plane:int, t:float, default:str="DEFAULT", derive_end:bool=True) -> str:
    t = quantize(t)
    st = self._st.state_at(die, plane, t)
    if st:
        return st
    if not derive_end:
        return default
    # last segment before t
    key = (die, plane)
    lst = self._st.by_plane.get(key, [])
    if not lst:
        return default
    starts = self._st._starts_by_plane.get(key)
    if starts is None or len(starts) != len(lst):
        starts = [s.start_us for s in lst]
        self._st._starts_by_plane[key] = starts
    import bisect as b
    i = b.bisect_right(starts, t) - 1
    if 0 <= i < len(lst):
        seg = lst[i]
        if seg.end_us <= t:
            return f"{seg.op_base}.END"
    return default
```

```
# scheduler.py (요약)
# _propose_and_schedule(...):
pk = extracted_phase_key
resv_records.append({
  ...,
  "phase_key": pk,
  "phase_hook": {"die": int(hook.get("die",0)), "plane": int(hook.get("plane",0)), "label": str(hook.get("label",""))},
  "propose_now": float(now),
})

# InstrumentedScheduler._emit_op_events(...): row에 위 3개 필드 전달
```

```
# main.py (신규)
def export_phase_proposal_counts(rows, rm, *, out_dir, run_idx):
    # key = (phase_key_used, phase_key_virtual, die, plane, op_name)
    cnt = {}
    for r in rows:
        d = int(r.get("phase_hook_die", r["die"]))
        p = int(r.get("phase_hook_plane", r["plane"]))
        t = float(r.get("phase_key_time", r["start_us"]))
        used = str(r.get("phase_key", "DEFAULT"))
        virt = rm.phase_key_at(d, p, t)
        key = (used, virt, d, p, str(r["op_name"]))
        cnt[key] = cnt.get(key, 0) + 1
    # CSV로 출력
```

## 검증 계획
- 시나리오: `--pc-demo` 3종(erase-only/mix/pgm-read), `--seed 42`, 단일 런.
- 확인:
  - 기존 CSV(`operation_timeline`, `op_state_timeline`, `op_state_name_input_time_count`)는 스키마/열 불변.
  - 신규 `phase_proposal_counts_*.csv` 생성 및 `phase_key_used`와 `phase_key_virtual`의 차이가 존재(빈 구간에서 virtual이 `<BASE>.END`).
  - `DEFAULT` 외 키가 있는 설정에서 분포가 합리적으로 분산되는지 로그(`out/proposer_debug_*.log`)와 대조.

## 영향 요약(사전/사후)
- proposer 동작: 변화 없음(단지 제안 시점 컨텍스트를 행에 기록).
- RM 타임라인: 변화 없음(END 세그먼트 비도입).
- Exporters: 신규만 추가. 기존 CSV 소비자 회귀 영향 0.
 
## 추후 작업
- PRD 스펙 반영: "END는 집계/분석용 파생 키이며, 기본 타임라인에는 포함하지 않는다" 문구 추가.
- 시각화: END는 기본 필터링. 옵션으로만 표기.

## 참고 파일
- main.py:156
- main.py:195
- main.py:304
- proposer.py:564
- resourcemgr.py:483
- resourcemgr.py:30

