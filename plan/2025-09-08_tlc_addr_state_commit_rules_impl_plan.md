---
title: "TLC addr_state 커밋 규칙(최종 단계 1회) — 구현 계획"
date: 2025-09-08
author: codex
source: research/2025-09-08_13-16-45_tlc_addr_state_commit_rules.md
status: draft
owners: ["Codex"]
---

# Problem 1‑Pager

- 배경: PRD v2 원칙에 따라 AddressManager 반영은 OP_END 시점에 수행된다. 현재 `Scheduler._am_apply_on_end`는 base 문자열에 "PROGRAM"이 포함되면 모두 PROGRAM 커밋으로 간주하여 `am.apply_pgm(...)`을 호출한다.
- 문제: TLC one‑shot 체인의 중간 단계(ONESHOT_PROGRAM_LSB/CSB/MSB 등) OP_END에서도 addr_state가 증가하여 "최종 단계에서 딱 1회만 커밋" 요구를 위반한다.
- 목표: PROGRAM 계열 중 최종 단계들에서만 addr_state 커밋을 허용한다.
  - 허용: `PROGRAM_SLC`, `COPYBACK_PROGRAM_SLC`, `ONESHOT_PROGRAM_MSB_23h`, `ONESHOT_PROGRAM_EXEC_MSB`, `ONESHOT_CACHE_PROGRAM`, `ONESHOT_COPYBACK_PROGRAM_EXEC_MSB`
  - 차단: 그 외 모든 PROGRAM 계열(L/C/MSB 중간 단계 포함)
- 비목표: ResourceManager의 예약 오버레이 규칙 변경, config.yaml 구조 변경, AddressManager 내부 API 변경.
- 제약: 변경 범위를 `scheduler.py` 내로 한정, 함수 ≤ 50 LOC 유지, 기존 로깅/메트릭 유지.

# 설계 요약(대안 비교)

- A) Scheduler에서 화이트리스트로 PROGRAM 커밋 제한
  - 장점: 변경 국소적/명확, 회귀 영향 최소, 즉시 효과
  - 단점: 신규 base 추가 시 리스트 갱신 필요(운영 규율 필요)
- B) CFG(op_bases)로 "commit_on_end" 플래그를 도입하여 데이터 구동
  - 장점: 유연/확장성, 코드 변경 빈도↓
  - 단점: CFG 전파/검증 비용, 기존 CFG 대량 수정 필요
- C) AddressManager가 단계 식별 후 내부적으로 중간 단계를 무시
  - 장점: 단일 책임화
  - 단점: 계층 침투, 현재 인터페이스와 분리, AM 내부 지식 증대

선택: A (가장 단순하고 안전한 변경)

# 변경 사항 상세(Where & What)

- 파일: `scheduler.py`
  - 위치: `_am_apply_on_end` (약 `scheduler.py:199`–`scheduler.py:360` 구간)
  - 기존: `is_program = ("PROGRAM" in b) and ...` → 모든 PROGRAM 계열에 대해 `am.apply_pgm(...)` 호출
  - 변경: 허용 base 집합에서만 `am.apply_pgm(...)` 호출

```python
# scheduler.py (개요)
ALLOWED_PROGRAM_COMMIT = {
    "PROGRAM_SLC",
    "COPYBACK_PROGRAM_SLC",
    "ONESHOT_PROGRAM_MSB_23H",
    "ONESHOT_PROGRAM_EXEC_MSB",
    "ONESHOT_CACHE_PROGRAM",
    "ONESHOT_COPYBACK_PROGRAM_EXEC_MSB",
}

b = str(base or "").upper()
is_erase = (b == "ERASE")
is_program_commit = b in ALLOWED_PROGRAM_COMMIT
...
if is_erase and hasattr(am, "apply_erase"):
    am.apply_erase(addrs, mode=mode)
elif is_program_commit and hasattr(am, "apply_pgm"):
    am.apply_pgm(addrs, mode=mode)
```

- 선택적 가드(확장성): `features.extra_allowed_program_bases: [ ... ]`를 병합하여 운영 중 확장 가능(초기값은 비어 있음).

# 구현 단계(Tasks)

1. 상수 정의: `ALLOWED_PROGRAM_COMMIT`를 `scheduler.py` 상단(모듈 전역) 또는 `Scheduler` 클래스 상수로 추가
2. 커밋 조건 변경: `_am_apply_on_end`에서 `is_program` 판정을 `b in ALLOWED_PROGRAM_COMMIT`로 교체
3. 선택적 기능 플래그: `cfg['features'].get('extra_allowed_program_bases', [])`를 읽어 합집합 처리(있다면)
4. 문서 반영: `docs/PRD_v2.md`에 OP_END 커밋 정책 각주(최종 단계 1회) 추가
5. 회귀 테스트: 중간 단계(OP_END)에서 addr_state 미증가, 최종 단계(OP_END)에서 1회 증가 검증

# 테스트 계획(회귀 필수)

- 단위(스텁 기반)
  - 대상: `_am_apply_on_end`
  - 방법: 가짜 `addrman`(메서드 호출 기록)과 더미 `targets`를 주입한 `Scheduler` 인스턴스 생성 후 호출
  - 케이스
    - ONESHOT_PROGRAM_LSB → `apply_pgm` 호출 0회
    - ONESHOT_PROGRAM_CSB → `apply_pgm` 호출 0회
    - ONESHOT_PROGRAM_MSB_23h → `apply_pgm` 호출 1회
    - ONESHOT_PROGRAM_EXEC_MSB → `apply_pgm` 호출 1회
    - ONESHOT_CACHE_PROGRAM → `apply_pgm` 호출 1회
    - ONESHOT_COPYBACK_PROGRAM_EXEC_MSB → `apply_pgm` 호출 1회
    - PROGRAM_SLC/COPYBACK_PROGRAM_SLC → `apply_pgm` 호출 1회(회귀 보장)

- 통합(시뮬 짧은 러닝)
  - 구성: `config.yaml` 기본값으로 1~2개 배치를 생성하는 시나리오 실행
  - 관찰: `metrics['last_commit_bases']`에 LSB/CSB가 포함되지 않으며, 최종 단계만 포함

# 영향도/리스크

- 장점: TLC one‑shot에서 중간 단계 커밋 제거로 정합성 회복, 오버레이/스케줄러 역할 분리 명확화
- 리스크: 신규 PROGRAM base가 추가될 경우 커밋 누락 가능 → 리뷰 체크리스트에 반영하거나 `extra_allowed_program_bases`로 임시 대응
- 성능: 분기 추가는 미미, 영향 없음

# 검증 기준(완료 정의)

- ONESHOT_PROGRAM_LSB/CSB/MSB(plain) OP_END에서 `addr_state` 불변
- ONESHOT_PROGRAM_MSB_23h/EXEC_MSB/CACHE_PROGRAM/COPYBACK_EXEC_MSB OP_END에서 `addr_state` +1
- PROGRAM_SLC/COPYBACK_PROGRAM_SLC 동작에는 변화 없음
- PRD v2: AddressManager 반영 시점(OP_END) 원칙 유지, 위배 없음

# 참고

- 근거 연구: `research/2025-09-08_13-16-45_tlc_addr_state_commit_rules.md`
- 코드 참조: `scheduler.py:199`(OP_END 핸들러), `scheduler.py:238`/`scheduler.py:356`(기존 PROGRAM 매칭/커밋 경로)
- PRD: `docs/PRD_v2.md` — AddressManager 반영은 OP_END 시점

