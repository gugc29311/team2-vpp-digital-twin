# Team 2 VPP Digital Twin

VPP(Vat Photopolymerization) 공정 SimPy 시뮬레이션. ㈜링크솔루션 연계 캡스톤디자인.

현재 버전 **v0.1.2** (변경 내역: [CHANGELOG.md](CHANGELOG.md)).
대시보드(고객용 요약 / 개발자용 검증 · 2D/3D Factory Replay)는 아래 [대시보드](#대시보드) 참고.

## 실행 (프로젝트 최상위 폴더에서. `main.py` 는 어느 폴더에서 실행해도 됨)

```bash
pip install -r requirements.txt
python main.py                                  # data/sample_orders.csv 10건으로 공정 통과 확인 (추적 출력)
python main.py --mode random                    # Normal 시나리오 1회 (5주 워밍업 + 100주, 약 20초)
python main.py --mode random --scenario "High Demand"
python main.py --mode random --reps 30          # 30회 독립 반복 + 95% CI (병렬, 8코어 약 1분 · 4코어 약 8분)
python main.py --mode random --weeks 2 --keep-events   # 2주만 실행 + 이벤트 로그 저장
python main.py --preset "Rush Order" --reps 30  # 실험 프리셋: Normal λ + 긴급 비율 30% (가정값)
python main.py --quiet --at "Day 2 10:00"       # 시각 조회: 그 시각의 주문 위치·설비 상태·작업자·대기열 (csv 또는 --keep-events)
python main.py --quiet --trace O001             # 주문 1건 전체 이벤트 추적 (명세서 14절 Level 2)
python -m streamlit run dashboard/app.py        # 대시보드 (사이드바: 고객용 요약 / 개발자용 검증, http://localhost:8501)
python -m src.analysis.capacity                 # 프린터 유효 처리용량 재측정 (설비·캘린더·고장 설정 변경 시)
python -m pytest                                # 로직 테스트 (수십 초 이내)
python -m pytest -m slow                        # 가정값 회귀 테스트 (약 30초~1분)
python -m tools.verify_claims                   # 분석 해석 검증 실험 (시드 5개) -> outputs/verify_claims.md
```

- 검증 근거: [docs/VPP_검증_문서.md](docs/VPP_검증_문서.md) (손계산 비교 · 설정값↔관측값 교차 검증 · 경계 조건 · 순간이동 검사).

- `python src/model/simulation.py` 처럼 파일 경로로 실행하면 `ModuleNotFoundError: config` — `python -m ...` 사용.
- `--reps` 병렬 실행은 스크립트로만 동작 (Jupyter 셀에서는 `run_replications(cfg, jobs=1)` 로 순차 실행).
- 결과: `outputs/` 에 `order_summary.csv`, `batch_summary.csv` (+ csv 모드·`--keep-events` 는 `event_log.csv`,
  random 모드는 `daily_summary.csv`, `--reps` 는 `replications_<시나리오>.csv`).
- 옵션 조합 오류는 실행 전에 멈춤(종료코드 2): `--orders` + random 모드, `--reps` 없는 `--jobs`, csv 모드의 `--weeks`,
  `--preset` + csv 모드 또는 `--scenario`, 이벤트 로그 없는 `--at`·`--trace`(random 모드에 `--keep-events` 없음), `--at`·`--trace` + `--reps`.
- `event_log.csv` 열: `sim_time, order_id, entity_type, entity_id, event, process, location, state, resource, detail`
  (resource = 작업자 개인 ID `JA1`/`PP2`/`QI1` 또는 설비 `P1`/`WASH1`/`UV1`, 설비 상태 6종 Idle/Setup/Running/Waiting/Down/Maintenance).
  location 은 `parameters.LOCATIONS` 의 가정 방 이름. 주문은 온라인 접수라 주문 데스크가 없고,
  Order Reception · Batch Formation 은 `Virtual`(실물 없음), Job Assignment 는 `Print Room` 으로 기록.
  이동 이벤트: `WALK`(작업자 빈손 이동) · `HANDOFF_*`(작업 위치 -> 문 앞 선반) · `TRANSPORT_*`(운반, resource = AMR1/AMR2) ·
  `RECEIVE_*`(문 앞 선반 -> 작업 위치) · `AMR_MOVE`(AMR 빈 차 이동).

## 폴더 구조

```
config/parameters.py          공장·공정 설정 기본값 (주문별 정보는 넣지 않음)
data/sample_orders.csv        공정 통과 확인용 주문 10건 (재료 2종, 수량 2개, 긴급 주문 포함)
main.py                       실행 진입점
src/entities/order.py         Order(주문), Part(부품)
src/entities/batch.py         Batch(빌드플레이트 1장)
src/resources/resources.py    설비(대기열 + 설비 객체 풀, 대당 고장·PM·상태 추적)·인력 자원(대기열 + 개인 ID)
src/model/config.py           SimConfig — 값을 바꿔 실험할 때 사용, 설정 검증
src/model/order_source.py     CSV 읽기 / 무작위 주문 생성 (근무일 기준 납기)
src/model/simulation.py       공정 흐름 (VPPSimulation)
src/scheduler/dispatch.py     프린터 대기열 규칙 (FCFS / SPT / EDD / URGENT_FIRST)
src/utils/random_utils.py     분포 정의 -> 난수, 용도별 독립 난수 스트림
src/utils/calendar.py         근무 캘린더 (근무시간 소비, 근무시간 더하기)
src/analysis/event_log.py     이벤트 로그 (명세서 10절 형식)
src/analysis/event_schema.py  이벤트 -> 공정명·상태 규칙
src/analysis/kpi.py           KPI (가동률, 부하율 ρ, 리드타임, 납기, 대기, 장비 상태, 작업자, 레진, 고장)
src/logger/state.py           시각 조회 state_at(res, t) — OME Time Query
src/analysis/capacity.py      프린터 유효 처리용량 측정
src/experiments/replications.py  30회 독립 반복 · 95% CI · 예측구간
src/experiments/scenarios.py  실험 프리셋 (Rush Order 등, parameters.py 수정 없이 설정 묶음)
src/validation/invariants.py  불변식 검사 check(res) (용량 초과·재료 혼합·정비 중 작업 등)
src/validation/crosscheck.py  설정값 <-> 관측값 교차 검증 cross_checks(res) (불량률·도착률·작업시간·고장·이동시간 등)
src/validation/locations.py   위치 연속성: 부품·작업자 순간이동 (이동 이벤트 없이 방 변경)
tools/verify_claims.py        분석 해석 검증 실험 (원인을 바꿔 결과가 예측대로 바뀌는지, 시드 5개)
docs/                         검증 문서 (VPP_검증_문서.md), 실험 결과표 (verify_claims.md)
dashboard/app.py              Streamlit 대시보드 화면 (고객용 / 개발자용)
dashboard/data.py             대시보드용 실행·표 가공, Replay 시각별 상태 재구성 (방 배치 ROOM_LAYOUT), 실시간 궤적 replay_tracks
dashboard/replay.py           2D Factory Replay (plotly 애니메이션)
dashboard/replay3d.py         3D Factory Replay (Canvas 직접 렌더링, 외부 3D 라이브러리 없음)
tests/                        로직 테스트 (고정 설정) + 회귀 테스트 (가정값, -m slow)
```

## 대시보드

```bash
python -m streamlit run dashboard/app.py        # 프로젝트 최상위 폴더에서
```

브라우저가 자동으로 열림(안 열리면 `http://localhost:8501`). 왼쪽 사이드바에서 화면(고객용 / 개발자용) · 시나리오 · 기간(1~4주) ·
시드 · 프린터 순서 규칙을 고르고 **[실행]**. 워밍업 없이 N주 실행하므로 KPI 에 초기 빈 공장 상태가 포함됨
(발표용 숫자는 `python main.py --mode random --reps 30`). 끌 때는 터미널에서 `Ctrl + C`.

| 화면 | 탭 | 내용 |
|---|---|---|
| 고객용 (요약) | 요약 | 주당 처리량 · 납기 준수율 · 리드타임(근무일) · WIP, 생산 시작 전 대기 vs 시작 후 납품, 주문 단계별 평균 시간, 일별 완료 수 |
| | 3D 공장 Replay | 전체 기간 재생 |
| 개발자용 (검증) | KPI 상세 | 핵심 KPI, 자원별 가동률 · 평균 대기, 병목 판정 |
| | 설비·작업자 상세 | 설비 대별 가동률 · 처리량 · 상태 시간, AMR 운반 횟수 · 운행 시간, 작업자 개인별 가동률 |
| | 주문 대기시간 분해 | 접수 -> 작업 배정 -> 배치 형성 -> 프린터 대기 -> 출력 -> 후공정~포장 (평균 · 중앙값 · 95% · 최대) |
| | 시간 추이 | WIP · 대기열 · 일별 처리량 · 프린터 가동률 |
| | 장비 상태 타임라인 | 설비별 상태 6종 간트 차트, 상태별 누적 시간 |
| | 2D 공장 Replay | 평면도 위 설비 · 작업자 · 방별 주문 수 재생 [명세서 13절], 주문 추적 |
| | 3D 공장 Replay | 전체 기간 재생 + 실시간 모드 [명세서 13절] |
| | 검증 | 설정값 <-> 관측값 교차 검증, 부품 · 작업자 순간이동, 불변식 (4주 실행 권장) |

Factory Replay (2D · 3D 공통)
- SimPy Simulation -> Event Log -> State Reconstruction (`state_at`) -> Animation 방식 (시뮬레이션 종료 후 재생).
- 날짜 선택 없이 **전체 기간(Day 1 09:00 ~ 종료)을 연속 재생**. 슬라이더의 `D1(월)`, `D2(화)` … 는 각 날짜의 시작.
- 시간 간격 15/30/60/120분 (기본: 1주 = 30분, 2주 이상 = 60분). 프레임이 1000개를 넘으면 느려질 수 있어 경고 표시.
- 방 배치(위치 · 크기)는 화면용 임의 배치 (모델에는 좌표 없이 공정 -> 방 이름만 있음, 실제 배치는 인터뷰 확인 필요).
- 2D Replay 탭 아래의 "주문 추적" 에서 주문 ID 를 입력하면 해당 주문의 전체 이벤트 표시 [명세서 14절 Level 2].

3D 공장 Replay
- 설비 = 상자(상태 6종 색, 가동 중 초록 불 · 고장 빨간 불), 작업자 = 사람 모양(작업 중/대기, 다른 방으로 갈 때 복도 경유),
  주문 = 방 안 작은 상자(남색 작업 중 · 주황 대기), 방 위에 주문 수와 설비 대기열 표시.
- Play / Pause · 속도 ×1 ×2 ×5 ×10 · 타임라인 슬라이더 · 시점 버튼(기본 시점 / 위에서 / 자동 회전).
- 마우스: 드래그 = 회전, 휠 = 확대/축소, 오른쪽 드래그(또는 Shift + 드래그) = 이동, 올리면 상태 표시. 스페이스바 = 재생/정지.
- 실시간 모드 (개발자용): 고른 구간(30분 ~ 8시간)을 이벤트 시각 그대로 재생 (실시간 ×1 · ×10 · ×60 · ×600).
  작업자는 작업 위치 <-> 문 앞 <-> 다른 방으로 걷고(빈손 / 짐), AMR 은 복도에서만 문 앞 선반 사이를 오감.
  이동 이벤트 없이 위치가 바뀌면 순간이동으로 표시 (0 이 정상).
- 접수 · 작업 배정 · 배치 구성 · 출력 대기 주문은 실물이 없어 방에 두지 않고 '실물 없음(전산)' 숫자로 표시.
- 외부 3D 라이브러리를 쓰지 않아 인터넷 연결 없이 동작하고, 추가로 설치할 패키지도 없음.

## 공정 흐름

주문(온라인 접수, 근무시간에만 도착) -> Job Assignment -> 배치 형성(면적 70% 또는 대기 1근무일, 같은 재료, 도착 순서대로)
-> 프린터 대기열(FCFS) -> 사용 가능한 프린터(고장·PM 점검) -> 출력(무인운전: 밤·주말에도 진행)
-> ① 이동(AMR) + Part Removal -> 세척(로드 10개, 세척액 50로드마다 교체) -> UV(로드 15개)
-> ④ 이동 -> Support Removal -> Surface Treatment -> ⑤ 이동 -> Inspection -> ⑥ 이동(합격품) -> Packaging(검사원 겸직)
-> 불량 4% -> 같은 부품 재출력(Job Assignment 부터)

이동: 작업자는 다른 위치의 작업을 하러 갈 때 걸어감(빈손 이동 포함). 운반은 기본 AMR —
사람이 작업 위치 -> 문 앞 선반, AMR 이 복도로 도착 문 앞 선반까지, 받는 사람이 선반 -> 작업 위치.
시간 = 거리 ÷ 속도 (가정 배치도 `ROOM_DOOR_X_M`, ⚠️). `TRANSPORT_MODE="worker"` 면 사람이 직접 운반.

## 파라미터 규칙

- 시간은 분포 튜플 + 단위: `("tri", 2, 5, 12, "min")`. 내부 단위는 hour.
- 캘린더 사용 시 시계는 **달력시간**(t=0 = 월 09:00). 사람 작업·세척·UV·정비는 근무시간만 소비.
  λ[건/주]·T·납기는 **근무시간** 기준.
- 가동률: 사람·세척·UV = 근무시간 대비. 프린터 부하율 ρ = 주당 출력시간 ÷ 유효 처리용량(정의 B).
- 실험은 `parameters.py` 를 고치지 말고:

```python
from src.model.config import SimConfig
from src.model.simulation import VPPSimulation
from src.analysis.kpi import kpis

cfg = SimConfig.from_parameters().replace(ORDER_SOURCE="random", SCENARIO="High Demand", POST_PROCESS_WORKER_COUNT=4)
print(kpis(VPPSimulation(cfg, keep_events=False).run()))
```

- 가정값을 바꾸면: (1) 프린터·캘린더·고장 관련이면 `python -m src.analysis.capacity` 로 용량 재측정,
  (2) `tests/test_regression.py` 기대 범위 갱신, (3) 가정값 로그 갱신.

## CSV 형식

필수: `order_id, product_id, arrival_time, quantity, material, due_date, priority` / 선택: `area_mm2, height_mm`
(면적 기준 배치·height 출력시간 모드에서는 필수), `required_process` (필요한 후공정 `;` 구분, 비우면 전체). `arrival_time`, `due_date` 는 시뮬레이션 시각 절대값(hour).
`priority` 는 `NORMAL`/`URGENT`. 엑셀 "CSV UTF-8" 저장 파일도 그대로 읽힘.