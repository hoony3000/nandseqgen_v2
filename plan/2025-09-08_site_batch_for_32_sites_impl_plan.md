---
title: "Plan — 32-site batch run with unique seeds"
date: 2025-09-08
author: Codex
status: draft
topic: main multi-site execution (1→32 sites)
related: research/2025-09-08_10-47-48_site_batch_for_32_sites.md, docs/PRD_v2.md, docs/TODO.md, main.py, addrman.py
---

# Problem 1‑Pager

- 배경: 실측 ATE는 site 1–32 병렬 운용을 전제로 하며, 각 site는 서로 다른 seed로 시작하는 독립 결과물이 필요하다. 현재 CLI는 `--num-runs`로 반복 실행과 run별 seed 증가(`base+i`)는 지원하나, AddressManager(AM)의 RNG는 고정/비결정 경로라 완전한 분기 일관성이 부족하다.
- 문제: 단일 실행으로 32개 site 결과를 생성하는 “site 차원”이 부재. 프로포저(Random)만 시드 적용되고 AM(NumPy RNG)은 고정/비결정(`default_rng()` or `_mk_addrman`의 `1234`)이라 site 간 고유성·재현성이 약함. 또한 현재 run 루프는 RM/AM을 공유하여 site 독립성 요구와 상충.
- 목표: main에서 site 차원을 도입해 단일 실행으로 32개 site 결과를 생성. 각 site는 (1) 독립 상태(RM/AM 재생성), (2) 고유 seed(프로포저+AM 동일 값), (3) 독립 출력 디렉터리를 가진다. 기존 옵션과 완전 호환.
- 비목표: 병렬 실행(동시 프로세스/스레드), 스케줄러/리소스 정책 변경, CSV 스키마 변경.
- 제약: 변경 최소화, 결정성 유지(프로포저/AM 동일 seed), 파일≤300 LOC/함수≤50 LOC 준수, 기존 CLI/워크플로우 호환.

# 호출/참조 경로 요약

- CLI/Runner: `main.py` — argparse 정의(700±), run 루프(760±), `run_once`(680±), `_mk_addrman`(top)에서 AM 내부 RNG가 `np.random.default_rng(1234)`로 고정.
- AM: `addrman.py:~177` — `self._rng = np.random.default_rng()` 초기화(비결정). 외부에서 시드 설정 공식 API 없음.
- TODO: `docs/TODO.md` — “RNG 결정성 일원화(NumPy/Random 동기화)” 항목 존재(본 작업으로 해소 대상).

# 대안 비교(결정 전 최소 두 가지)

- 옵션 A(권장, 최소 변경): main에 site 루프와 CLI를 추가하고, site별 RM/AM 재생성 + seed 공식화 + site 하위 디렉터리에 export.
  - 장점: 구현 작고 기존과 호환. 결정성/재현성 강화(프로포저+AM 동일 seed). 다운스트림 영향 최소.
  - 단점: `_rng` 직접 접근(캡슐화 약함). 이후 AM에 `set_seed()` 추가로 개선.
  - 위험: 낮음 — 루프 구조/경로만 추가.
- 옵션 B(코드 변경 없음): 쉘에서 32회 반복 실행(`--seed base+site-1`, `--out-dir out/site_XX`).
  - 장점: 즉시 가능.
  - 단점: 단일 실행 불가. 로그/결과 수집 분산. 운영 복잡.
  - 위험: 낮음.
- 옵션 C(유연성 강화): `--sites 1-32`/`1,2,5,8-12` 파서 + `--seed-formula` 노출.
  - 장점: 대상 선택/가시성 향상.
  - 단점: 파서/도움말/테스트 증가(복잡도 상승).
  - 위험: 중간.

=> 선택: 옵션 A. 옵션 C는 후속 확장으로 검토.

# 구현 사양(옵션 A)

## 1) CLI 확장
- 인자 추가(기본 비활성):
  - `--site-count` (int, 기본 0) — 0이면 기존 단일 경로 유지.
  - `--site-start` (int, 기본 1) — site 시작 번호.
  - `--site-dir-pattern` (str, 기본 `"site_{site_id:02d}"`) — site 하위 폴더 이름 패턴.
- 도움말 예시: “Enable multi-site batch. Creates outputs under `<out-dir>/<pattern>` per site.”

## 2) 실행 구조
- if `site_count > 0`:
  - for `offset in range(site_count)`: `site_id = site_start + offset`
    - RM/AM 재생성(사이트 독립):
      - `rm = ResourceManager(cfg=cfg, dies, planes)`
      - `am = _mk_addrman(cfg)`
      - EPR 등록: `rm.register_addr_policy(am.check_epr)` (가능 시)
    - for `i in range(num_runs)`:
      - `seed_i = base_seed + offset + i`
      - 프로포저 RNG: `rng_seed=seed_i`로 `run_once(..., rng_seed=seed_i)` 전달
      - AM RNG 동기화: 가능한 경우 `am._rng = np.random.default_rng(seed_i)` (NumPy 가용 시 try/except)
      - 출력 디렉터리: `out_dir_site = os.path.join(args.out_dir, pattern.format(site_id=site_id))`
      - 모든 export/로그/snapshot에 `out_dir_site` 사용
      - 요약 로그에 `site_id` 포함
- else: 기존 단일 경로 유지(완전 호환)

## 3) AddressManager 시드 설정(최소 변경)
- 즉시 경로: main에서 `try: import numpy as np; am._rng = np.random.default_rng(seed_i)`(가드 포함)
- 후속 개선(선택): `addrman.py`에 `def set_seed(self, seed: int): self._rng = np.random.default_rng(int(seed))` 추가 후 main이 해당 API 호출.

## 4) 출력/로그
- 파일명 스키마 유지, 디렉터리만 site 분리: `out/site_01/operation_sequence_...csv` 등.
- proposer 로그 파일도 `out/site_01/proposer_debug_...log` 경로에 기록.

## 5) 시드 포뮬러(고정)
- `seed_i = base + (site_id - site_start) + run_idx` — site/run 모두에 대해 고유하며, 동일 입력 시 재현 가능.

# 변경 범위/영향

- 변경 파일: `main.py`(argparse + 루프 구조 + out_dir 전달 경로 + AM 시드 설정), (선택) `addrman.py`(`set_seed`) 문서화.
- 호출: `run_once`와 export 함수 시그니처 불변(인자 `out_dir`만 site별 값으로 변경 전달).
- 호환성: `--site-count=0` 기본값으로 기존 행위 100% 유지.

# 작업 순서(작고 안전한 커밋 단위)

1) argparse 확장만 추가(기본값으로 비활성) — 동작 변화 없음.
2) site 루프 도입 + RM/AM 재생성 + out_dir_site 적용 — 단일 run 케이스로 스모크.
3) seed 포뮬러 적용(프로포저 RNG만) — 결과 결정성 확인.
4) AM RNG 시드 동기화(NumPy 가드) — TODO 항목 “RNG 결정성 일원화” 해소.
5) 요약 로그에 `site_id` 표기 — 가시성.
6) (선택) `AddressManager.set_seed` API 추가 + main에서 교체 — 캡슐화 강화.
7) 문서 갱신: PRD §6 Workflow에 “multi-site batch(직렬)” 섹션/실행 예시 추가, TODO 갱신.

# 수용 기준(검증 가능 항목)

- 동일 명령을 두 번 실행 시(동일 CFG/seed) 모든 `out/site_XX/*csv`/`*.json` 파일 해시가 동일(결정성).
- 서로 다른 site 간(`site_01` vs `site_02`) 핵심 CSV(`operation_sequence`) 내용이 달라진다(시퀀스/시간/UID 차이) — 시드가 반영됨.
- `--site-count=0` 또는 미지정 시 기존 단일 디렉터리 하위에만 파일 생성(회귀 없음).
- `--site-count=32 --num-runs=2`로 실행 시 각 site 디렉터리에 6개 CSV + 1 snapshot + 1 proposer 로그가 존재.

# 테스트 계획

- 단위(E2E 가벼운 실행)
  - A: `--site-count 2 --num-runs 1 --seed 1000 -t 20000` — `out/site_01`와 `out/site_02` 각각에 산출물 생성, `operation_sequence` 파일 길이/해시가 서로 다름.
  - B: A를 동일 명령으로 2회 반복 — 각 site에서 해시 동일(결정성 검증).
  - C: `--site-count 0` — 기존 경로에만 산출물 생성.
  - D: NumPy 미가용 환경(강제 모킹) — AM 시드 설정이 안전하게 무시되고 실행 지속(예외 없음).
- 회귀
  - 기존 단일 실행(`--num-runs=N`) 결과와 비교: 폴더 루트만 다르고 파일 스키마/정렬/열 순서 동일.

# 리스크와 완화

- `_rng` 직접 접근으로 내부 변경에 취약 — 추후 `AddressManager.set_seed()` 도입으로 캡슐화.
- site 루프 도입으로 경로 계산/로그 경합 위험 — 디렉터리 생성 `exist_ok=True`와 1-site 단위 직렬 실행으로 완화.
- 시드 포뮬러 오프바이원 — 단위 테스트에서 site0/site1 차이 검증으로 완화.

# 구현 메모

- out_dir 전달 경로: proposer 로그/모든 export/snapshot에서 `out_dir_site` 변수를 일관 적용.
- run 간 연속성: RM은 site 내부 run들 사이에만 연속 — site 간에는 항상 새 RM/AM.
- 성능: 32개 site 직렬 실행(단일 프로세스). 병렬화는 후속 과제.

# 예시 명령

- 32개 site, site당 1 run:
  - `python main.py --site-count 32 --num-runs 1 --seed 1000 --out-dir out/sites -t 20000`
- 32개 site, site당 2 run:
  - `python main.py --site-count 32 --num-runs 2 --seed 1000 --out-dir out/sites -t 20000`

# 다음 단계(선택)

- `--sites 1-32`/`1,2,5,8-12` 구문 파서 추가(대상 유연화).
- `--seed-formula` 옵션 제공(문서화된 프리셋 묶음).
- 병렬 실행 모드(`--parallel N`)와 충돌 방지(로그/파일 락, 프로세스 별 out_dir).

