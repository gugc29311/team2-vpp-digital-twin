# Team 2 VPP Digital Twin

VPP(Vat Photopolymerization) 공정 SimPy 시뮬레이션. ㈜링크솔루션 연계 캡스톤디자인.
현재 버전 **v0.1.0** (변경 내역: [CHANGELOG.md](CHANGELOG.md)).
파라미터는 가정값 로그(`vpp-assumptions-log.md`)의 **가정값**(인터뷰 확인 전) — 근거·검증 결과는 로그 참고.

> **처음이라면 [GUIDE.md](GUIDE.md)부터** — 전체 흐름, 핵심 개념, 파일별 설명, 자주 하는 작업이 정리되어 있습니다.

## 실행 (반드시 프로젝트 최상위 폴더에서)

```bash
pip install -r requirements.txt
python main.py                                  # data/sample_orders.csv 10건으로 공정 통과 확인 (추적 출력)
python main.py --mode random                    # Normal 시나리오 1회 (5주 워밍업 + 100주, 약 20초)
python main.py --mode random --scenario "High Demand"
python main.py --mode random --reps 30          # 30회 독립 반복 + 95% CI (병렬, 8코어 약 1분 · 4코어 약 8분)
python main.py --mode random --weeks 2 --keep-events   # 2주만 실행 + 이벤트 로그 저장
python main.py --preset "Rush Order" --reps 30  # 실험 프리셋: Normal λ + 긴급 비율 30% (가정값)
python -m src.analysis.capacity                 # 프린터 유효 처리용량 재측정 (설비·캘린더·고장 설정 변경 시)
python -m pytest                                # 로직 테스트 (1초 미만)
python -m pytest -m slow                        # 가정값 회귀 테스트 (약 15초)
```

- `python src/model/simulation.py` 처럼 파일 경로로 실행하면 `ModuleNotFoundError: config` — `python -m ...` 사용.
- `--reps` 병렬 실행은 스크립트로만 동작 (Jupyter 셀에서는 `run_replications(cfg, jobs=1)` 로 순차 실행).
- 결과: `outputs/` 에 `order_summary.csv`, `batch_summary.csv` (+ csv 모드·`--keep-events` 는 `event_log.csv`,
  random 모드는 `daily_summary.csv`, `--reps` 는 `replications_<시나리오>.csv`).
- 옵션 조합 오류는 실행 전에 멈춤(종료코드 2): `--orders` + random 모드, `--reps` 없는 `--jobs`, csv 모드의 `--weeks`, `--preset` + csv 모드 또는 `--scenario`.

## 폴더 구조

```
config/parameters.py          공장·공정 설정 기본값 (주문별 정보는 넣지 않음)
data/sample_orders.csv        공정 통과 확인용 주문 10건 (재료 2종, 수량 2개, 긴급 주문 포함)
main.py                       실행 진입점
src/entities/order.py         Order(주문), Part(부품)
src/entities/batch.py         Batch(빌드플레이트 1장)
src/resources/resources.py    설비(대기열 + 설비 객체 풀, 대당 고장·PM 추적)·인력 자원
src/model/config.py           SimConfig — 값을 바꿔 실험할 때 사용, 설정 검증
src/model/order_source.py     CSV 읽기 / 무작위 주문 생성 (근무일 기준 납기)
src/model/simulation.py       공정 흐름 (VPPSimulation)
src/scheduler/dispatch.py     프린터 대기열 규칙 (FCFS / SPT / EDD / URGENT_FIRST)
src/utils/random_utils.py     분포 정의 -> 난수, 용도별 독립 난수 스트림
src/utils/calendar.py         근무 캘린더 (근무시간 소비, 근무시간 더하기)
src/analysis/event_log.py     이벤트 로그
src/analysis/kpi.py           KPI (가동률, 부하율 ρ, 리드타임, 납기, 레진, 고장)
src/analysis/capacity.py      프린터 유효 처리용량 측정
src/experiments/replications.py  30회 독립 반복 · 95% CI · 예측구간
src/experiments/scenarios.py  실험 프리셋 (Rush Order 등, parameters.py 수정 없이 설정 묶음)
src/validation/invariants.py  불변식 검사 check(res) (용량 초과·재료 혼합·정비 중 작업 등)
tests/                        로직 테스트 (고정 설정) + 회귀 테스트 (가정값, -m slow)
```

## 공정 흐름

주문(근무시간에만 도착) -> Job Assignment -> 배치 형성(면적 70% 또는 대기 1근무일, 같은 재료, 도착 순서대로)
-> 프린터 대기열(FCFS) -> 사용 가능한 프린터(고장·PM 점검) -> 출력(무인운전: 밤·주말에도 진행)
-> ① 이동 + Part Removal -> 세척(로드 10개, 세척액 50로드마다 교체) -> UV(로드 15개)
-> ④ 이동 -> Support Removal -> Surface Treatment -> ⑤ 이동 -> Inspection + Packaging(검사원 겸직)
-> 불량 4% -> 같은 부품 재출력(Job Assignment 부터)

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
(면적 기준 배치·height 출력시간 모드에서는 필수). `arrival_time`, `due_date` 는 시뮬레이션 시각 절대값(hour).
`priority` 는 `NORMAL`/`URGENT`. 엑셀 "CSV UTF-8" 저장 파일도 그대로 읽힘.
