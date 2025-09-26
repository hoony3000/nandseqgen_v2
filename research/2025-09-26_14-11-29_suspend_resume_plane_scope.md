---
date: 2025-09-26T14:11:29+0900
researcher: Codex
git_commit: 82bceb7cc7334dca08a6c48c438549d4f17c9f77
branch: main
repository: nandseqgen_v2
topic: "Suspend/Resume op_state plane 범위"
tags: [research, codebase, proposer, resourcemgr, suspend-resume]
status: complete
last_updated: 2025-09-26
last_updated_by: Codex
---

# 연구: Suspend/Resume op_state plane 범위

**Date**: 2025-09-26T14:11:29+0900
**Researcher**: Codex
**Git Commit**: 82bceb7cc7334dca08a6c48c438549d4f17c9f77
**Branch**: main
**Repository**: nandseqgen_v2

## 연구 질문
SUSPEND, RESUME 동작이 예약될 때 op_state 가 특정 plane 에만 적용되는지, 아니면 die 전체 plane 으로 확장되는지를 확인한다.

## 요약
- SUSPEND base 는 `scope: "DIE_WIDE"` 로 정의되어 있지만 동시에 `instant_resv` 로 표시되어 있어, proposer 가 PHASE_HOOK 에서 전달받은 단일 (die, plane) 좌표만 사용한다. 따라서 die 전역 스코프임에도 실제 예약은 한 plane 만 대상으로 한다(config.yaml:556,577; proposer.py:1484-1534).
- ResourceManager 의 instant 예약 경로는 plane/die 광역 윈도우를 만들지 않고, 전달받은 target 에 대해서만 state 타임라인을 기록하므로 SUSPEND op_state 구간이 plane 단위로 남는다(resourcemgr.py:645-689).
- RESUME 제안도 동일한 hook 폴백에 의존하며, resume 핸들러가 suspend 당시 meta 의 target 목록을 그대로 재사용하기 때문에 모든 plane 으로 확장되지 않고 원래 plane 집합만 재예약된다(proposer.py:1484-1534; resourcemgr.py:1584-1650).
- 실행 산출물에서 PROGRAM_SUSPEND/PROGRAM_RESUME 행이 die 의 모든 plane 이 아닌 해당 plane 별로만 등장함을 확인했다(out/op_state_timeline_250926_0000001.csv:80,82,86).

## 상세 발견

### Proposer 타깃 선택은 plane 단위로 유지
- `PROGRAM_SUSPEND`/`PROGRAM_RESUME` 는 `scope: "DIE_WIDE"` 를 상속하지만 SUSPEND base 만 instant 로 표시되어 있다(config.yaml:556-605).
- ERASE/PROGRAM/READ 샘플링 계열이 아닌 base 의 경우 proposer 는 hook 좌표를 읽고, 풍부한 target 목록이 없으면 단일 `Address(die, plane, block=0, page=None)` 를 만든다(proposer.py:1484-1534).
- PROGRAM/ERASE 용 PHASE_HOOK 은 한 번에 한 plane 씩만 전달하므로, 각각의 SUSPEND/RESUME 제안도 단일 plane 컨텍스트를 유지한다.

### ResourceManager 예약 및 축 좌표 관리
- SUSPEND 에 사용되는 instant 예약 경로는 plane/die 배타 윈도우를 건너뛰고 전달된 target 에 대해서만 state 타임라인을 추가한다(resourcemgr.py:645-689).
- 커밋 시점의 suspend 처리에서는 suspend 된 meta 의 target 목록을 확인해 잘라낼 plane 을 결정하므로, 활성 plane 만 영향을 받는다(resourcemgr.py:804-844).
- RESUME 실행 시에도 suspend 된 meta 의 `targets` 목록을 복제해 일반 예약에 넘기므로, 모든 plane 대신 원래 plane 집합만 복원된다(resourcemgr.py:1584-1650).

### 관측된 op_state 타임라인 출력
- 생성된 타임라인에서 `PROGRAM_SUSPEND.*` 행은 suspend 를 트리거한 plane 에만 나타난다(예: plane 0 의 80-81행, plane 1 의 90행) (out/op_state_timeline_250926_0000001.csv:80,90).
- `PROGRAM_RESUME.*` 행도 영향을 받은 plane 별로만 등장하며 plane 0-3 전체에 동시에 방송되지 않는다(out/op_state_timeline_250926_0000001.csv:82,86,92).
- operation_timeline 내 `Program_Suspend_Reset` 행 역시 단일 plane 칼럼에 귀속되어 있다(out/operation_timeline_250926_0000001.csv:5,10).

## 코드 참조
- `config.yaml:556` – ERASE/PROGRAM SUSPEND/RESUME base 스코프 및 instant 설정.
- `proposer.py:1484` – 비 ERASE/PROGRAM/READ base 의 단일 plane 타깃 폴백.
- `resourcemgr.py:645` – instant 예약 경로가 전달된 target 에만 state 타임라인을 기록.
- `resourcemgr.py:804` – suspend 커밋 시 suspend meta 의 plane 에 대해서만 타임라인을 절단.
- `resourcemgr.py:1584` – resume 시 suspend meta 타깃을 재사용해 재예약.
- `out/op_state_timeline_250926_0000001.csv:80` – plane 별 PROGRAM_SUSPEND 행 예시.

## 아키텍처 인사이트
- suspend/resume 축 게이팅은 die 수준 상태(`_pgm_susp`/`_erase_susp`)로 제어되지만 op_state 타임라인은 plane 단위로 유지되어 관측 도메인과 배타 도메인을 분리한다.
- instant SUSPEND 예약은 hook 컨텍스트에 의존하므로, 다중 plane suspend 스텁을 모델링하려면 더 풍부한 hook target 정보가 필요하다.

## 역사적 맥락(thoughts/ 기반)
- `research/2025-09-26_13-02-33_suspend_state_op_end.md` – suspend_state 타이밍 변경과 ResourceManager 축 동작에 대한 이전 분석.

## 관련 연구
- `research/2025-09-26_13-02-33_suspend_state_op_end.md`

## 미해결 질문
- 이 데이터셋에서는 다중 plane ERASE 가 suspend/resume 되는 사례를 확인하지 못했으므로, 해당 시나리오를 수집하여 축 절단과 타깃 동작이 축별로 동일한지 검증할 필요가 있다.
