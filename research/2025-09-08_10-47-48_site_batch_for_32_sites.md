---
date: 2025-09-08T10:47:25+0900
researcher: codex
git_commit: cba55e0dc0e35d8697e8aeac3f7004443a7f3a80
branch: main
repository: nandseqgen_v2
topic: "Run all 32 sites with unique seeds via main()"
tags: [research, codebase, main, seeding, sites]
status: complete
last_updated: 2025-09-08
last_updated_by: codex
---

# 연구: 32개 site를 서로 다른 seed로 일괄 실행하기

**Date**: 2025-09-08T10:47:25+0900
**Researcher**: codex
**Git Commit**: cba55e0dc0e35d8697e8aeac3f7004443a7f3a80
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
시뮬레이션을 실제 테스트로 돌릴 때 ATE site를 1–32로 나눠 병렬 수행한다. 각 site는 서로 다른 seed로 시작하는 결과물이 필요하다. main 함수를 32개 site 모두에 대한 결과물을 한 번의 실행으로 생성하도록 변경하는 방안은?

## 요약
- 현재 main은 `--num-runs`로 반복 실행과 run별 seed 증가를 지원한다(`seed_i = base + i`). 하지만 AddressManager의 RNG는 고정값/비결정 값으로 설정되어 있어 run별 완전한 분기 일관성이 부족하다.
- 32개 site 배치를 위해서는 “site 차원”을 명시적으로 도입해 다음을 충족해야 한다:
  - site별 독립 상태와 출력 디렉터리 분리(`out/site_01`, …, `out/site_32`).
  - site별 고유 seed 적용(프로포저 RNG + AddressManager RNG 모두), 필요 시 run 반복도 유지.
- 최소 변경안: main에 `--site-count`(또는 `--site-range`)와 `--site-dir-pattern`을 추가하고, site 루프 안에서 RM/AM을 재생성, seed를 `(base_seed + site_id - 1 + run_idx)`로 설정해 결과물을 site 하위 폴더에 저장한다. 기존 옵션과 완전 호환된다.

## 상세 발견

### Seeding 경로와 현재 동작
- `main.py:708` — CLI에 `--seed` 존재.
- `main.py:795` — run별 프로포저 RNG는 `random.Random(seed_i)`로 분기.
- `addrman.py:178` — AddressManager는 기본적으로 `np.random.default_rng()`(비결정)로 초기화.
- `main.py:39-45` — AddressManager 사용 시 `_mk_addrman`가 `am._rng = np.random.default_rng(1234)`로 고정 시드 설정(전역 동일 경로).
- 결론: 현재는 프로포저 RNG만 run별로 분기하고, AM은 고정/비결정 경로라 site별 고유성/재현성이 약함. TODO에도 동일 이슈가 기록됨.
  - `docs/TODO.md:139` — “RNG 결정성 일원화(NumPy/Random 동기화)”

### 상태 공유와 출력 레이아웃
- `main.py:744` — “Shared state across runs for continuity”: RM은 run들 사이에 공유됨. site 간에는 독립 실행이 필요하므로 site 루프에서는 RM/AM을 재생성해야 함.
- 출력 파일명은 run 인덱스 기반으로 suffix(`..._0000001.csv`)가 붙는다. 디렉터리 단위로 site를 분리하면 파일명 스킴은 그대로 재사용 가능.
  - export 호출: `main.py:801-809`

### 제안 변경(안 A: 최소 변경, 권장)
- CLI 확장:
  - `--site-count`(int, 기본 0=비활성)
  - `--site-start`(int, 기본 1)
  - `--site-dir-pattern`(str, 기본 `site_{site_id:02d}`)
- 실행 구조:
  - if `site_count > 0`:
    - for `site_id` in `[site_start, ..., site_start+site_count-1]`:
      - RM/AM 재생성(사이트 독립 상태 보장)
      - for `i` in `range(num_runs)`:
        - `seed_i = base_seed + (site_id - site_start) + i`
        - AM RNG도 동일 seed로 설정: `am._rng = np.random.default_rng(seed_i)`
        - 출력 루트: `out_dir_site = os.path.join(out_dir, site_dir_pattern.format(site_id=site_id))`
        - 이 하위 폴더에 로그/CSV/snapshot을 기록(파일명 suffix는 기존 로직 유지)
- 장점/단점/위험:
  - 장점: 구현 작고 기존 CLI와 호환. 각 site 완전 분리. seed 제어 일관성(프로포저+AM).
  - 단점: `am._rng` 접근은 사실상 내부 속성 접근이므로 캡슐화 약함(현 구조에서 허용/관행).
  - 위험: site 간 독립성을 위해 RM/AM 재생성 필요. 기존 “run 연속성”은 site 내부에서만 유지됨.

### 대안 변경(안 B: 기존 기능 활용, 코드 변경 최소화)
- 코드 변경 없이 쉘 레벨에서:
  - `for s in $(seq 1 32); do python main.py -t ... -n 1 --seed $((BASE+s-1)) --out-dir out/site_$(printf %02d $s); done`
- 장점: 즉시 가능, 코드 변경 없음.
- 단점: 단일 실행으로 일괄 수행 불가. 워크플로우/로그 수집 분산.

### 대안 변경(안 C: `--sites` 목록/범위 + seed 포뮬러 선택)
- `--sites 1-32` 또는 `--sites 1,2,5,8-12` 파서 추가, `--seed-formula base+site+run` 선택지 제공.
- 장점: 유연한 대상 선택, seed 규칙 가시화.
- 단점: 파서/도움말/테스트 증가(복잡도 상승).

### 구현 아웃라인(안 A 기준)
1) argparse 확장: `--site-count`, `--site-start`, `--site-dir-pattern` 추가.
2) site 루프 신설: site별로 RM/AM 생성 및 EPR 정책 등록.
3) run 루프 내부 seed 계산 변경: `seed_i = base + (site_idx) + i`.
4) AM RNG seeding: `am._rng = np.random.default_rng(seed_i)`.
5) 출력 디렉터리: `args.out_dir`를 `out_dir_site`로 바꿔 export/로그 경로 전달.
6) 로그 요약에 site_id 표기 추가(가독성).

### 예시 호출
- 32개 site, site당 1 run:
  - `python main.py --site-count 32 --num-runs 1 --seed 1000 --out-dir out/sites -t 20000`
- 32개 site, site당 2 run(시드가 site/run에 따라 모두 달라짐):
  - `python main.py --site-count 32 --num-runs 2 --seed 1000 --out-dir out/sites -t 20000`

## 코드 참조
- `main.py:700` — argparse 정의 시작, `--seed`/`--num-runs`/`--out-dir` 존재.
- `main.py:744` — RM/AM 생성(현재는 run 전역 1회 생성).
- `main.py:795` — run별 seed 계산(`seed_i = base + i`).
- `main.py:801` — 결과물 export 경로 생성 지점.
- `addrman.py:178` — `self._rng = np.random.default_rng()`(AM 내부 RNG 초기화).
- `main.py:39-45` — `_mk_addrman`에서 `am._rng = np.random.default_rng(1234)`로 고정 시드 설정.

## 아키텍처 인사이트
- 결정성: 프로포저(Random)와 AM(NumPy RNG)을 동일 seed로 제어해야 site 간 고유성과 재현성이 보장된다.
- 상태 격리: site 간 결과 독립성을 위해 RM/AM을 site마다 재생성해야 하며, run 연속성은 동일 site 내에서만 유지한다.
- 출력 조직화: 파일명 스키마는 유지하고 디렉터리로 site를 분리하면 다운스트림 도구 호환성을 보존하기 쉽다.

## 역사적 맥락(thoughts/ 기반)
- `docs/TODO.md:139` — RNG 결정성 일원화 필요 항목이 기록되어 있어, 본 변경과 방향성이 일치함.

## 관련 연구
- `plan/2025-09-08_main_second_run_no_ops_impl_plan.md:76` — 다중 run 시나리오 언급(동일 맥락).

## 미해결 질문
- site 간 bootstrap 단계를 공유할 필요가 있는가? 없다면 site 내 run에만 bootstrap을 허용하면 된다. -> (검토완료) 불필요
- seed 포뮬러를 고정(예: `base + site_idx + run_idx`)할지 CLI로 노출할지? -> (검토완료) 고정
- `am._rng` 외부 설정을 공식 API로 승격할지(AddressManager에 `set_seed()` 도입)? -> (검토완료) 승격

