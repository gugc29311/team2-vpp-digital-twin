# VPP Digital Twin — Team 2

이산사건시스템(SimPy) 기반 3D 프린팅 파운드리 Digital Twin 구축 프로젝트
(캡스톤디자인, 참여기업: ㈜링크솔루션)

> 버전별 변경 사항은 [CHANGELOG.md](CHANGELOG.md) 참고.

## 팀 정보

| 항목 | 내용 |
|---|---|
| 대상 공정 | VPP (Vat Photopolymerization) |
| 주요 소재 | Resin |
| 주요 후공정 | Part Removal, Washing, UV Curing, Support Removal, Surface Treatment, Inspection |

## 개발 환경

Python · SimPy · Streamlit/Plotly · Pandas · GitHub

## Process Route (기본안)

Order Reception → Job Assignment → Build Preparation/Batch Formation → VPP Build →
Part Removal → Washing → UV Curing → Support Removal → Surface Treatment → Inspection → Packaging

> 기업 인터뷰 결과에 따라 수정 예정

## 폴더 구조

```
config/parameters.py             공장·공정 설정 기본값 (주문별 정보는 넣지 않음)
data/sample_orders.csv           공정 통과 확인용 주문 10건 (재료 2종, 수량 2개, 긴급 주문 포함)
main.py                          실행 진입점
src/entities/order.py            Order(주문), Part(부품)
src/entities/batch.py            Batch(빌드플레이트 1장)
src/resources/resources.py       설비(대기열 + 설비 객체 풀, 대당 고장·PM 추적)·인력 자원
src/model/config.py              SimConfig — 값을 바꿔 실험할 때 사용, 설정 검증
src/model/order_source.py        CSV 읽기 / 무작위 주문 생성 (근무일 기준 납기)
src/model/simulation.py          공정 흐름 (VPPSimulation)
src/utils/random_utils.py        분포 정의 -> 난수, 용도별 독립 난수 스트림
src/utils/calendar.py            근무 캘린더 (근무시간 소비, 근무시간 더하기)
src/analysis/event_log.py        이벤트 로그
src/analysis/kpi.py              KPI (가동률, 부하율 ρ, 리드타임, 납기, 레진, 고장)
src/analysis/capacity.py         프린터 유효 처리용량 측정
src/experiments/replications.py  30회 독립 반복 · 95% CI · 예측구간
tests/                           로직 테스트 (고정 설정) + 회귀 테스트 (가정값, -m slow)
docs/                            문서 (가정값 로그 등)

(예정) src/scheduler, src/logger, src/validation, dashboard/, replay/, results/
```

## 목표

Validated SimPy-Based Digital Twin of VPP Foundry Operations

## 참고

기업 원본 자료 및 보안 필요 자료는 본 레포에 업로드하지 않습니다.

## 현재 버전

v0.0.2 — 가정값 기반 VPP 공정 시뮬레이션 모델 구현 ([CHANGELOG](CHANGELOG.md))

- 파라미터는 가정값 로그 기반이며, 일부 조정 사항은 CHANGELOG에 기록
- 모든 값은 가정값 — 기업 인터뷰 후 갱신 예정

## 설치 방법

```
git clone https://github.com/gugc29311/team2-vpp-digital-twin.git
cd team2-vpp-digital-twin
pip install -r requirements.txt
```

## 실행 방법

반드시 **프로젝트 최상위 폴더**에서 실행합니다.

```bash
python main.py                                  # data/sample_orders.csv 10건으로 공정 통과 확인 (추적 출력)
python main.py --mode random                    # Normal 시나리오 1회 (5주 워밍업 + 100주, 약 20초)
python main.py --mode random --scenario "High Demand"
python main.py --mode random --reps 30          # 30회 독립 반복 + 95% CI (병렬, CPU에 따라 1~4분)
python -m src.analysis.capacity                 # 프린터 유효 처리용량 재측정 (설비·캘린더·고장 설정 변경 시)
python -m pytest                                # 로직 테스트 (1초 미만)
python -m pytest -m slow                        # 가정값 회귀 테스트 (약 15초)
```

- `python src/model/simulation.py` 처럼 파일 경로로 실행하면 `ModuleNotFoundError: config` — `python -m ...` 사용.
- `--reps` 병렬 실행은 스크립트로만 동작 (Jupyter 셀에서는 `run_replications(cfg, jobs=1)` 로 순차 실행).
- 결과(1회 실행 시): `outputs/` 에 `order_summary.csv`, `batch_summary.csv` (+ csv 모드는 `event_log.csv`).
  `--reps` 반복 실행 결과는 화면에만 출력.

## 공정 흐름 (현재 모델)

```
주문(근무시간에만 도착) -> Job Assignment
-> 배치 형성(면적 70% 또는 대기 1근무일, 같은 재료, 도착 순서대로)
-> 프린터 대기열(FCFS) -> 사용 가능한 프린터(고장·PM 점검) -> 출력(무인운전: 밤·주말에도 진행)
-> ① 이동 + Part Removal
-> ② 이동 + 세척(로드 10개, 세척액 50로드마다 교체)
-> ③ 이동 + UV 경화(로드 15개)
-> ④ 이동 -> Support Removal -> Surface Treatment
-> ⑤ 이동 -> Inspection + Packaging(검사원 겸직)
-> 불량 4% -> 같은 부품 재출력(Job Assignment 부터)
```

## 파라미터 규칙

- 시간은 분포 튜플 + 단위: `("tri", 2, 5, 12, "min")`. 내부 단위는 hour.
- 캘린더 사용 시 시계는 **달력시간**(t=0 = 월 09:00). 사람 작업·세척·UV·정비는 근무시간만 소비.
  λ[건/주]·T·납기는 **근무시간** 기준.
- 가동률: 사람·세척·UV = 근무시간 대비. 프린터 부하율 ρ = 주당 출력시간 ÷ 유효 처리용량.
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

- 필수 열: `order_id, product_id, arrival_time, quantity, material, due_date, priority`
- 선택 열: `area_mm2, height_mm` — **현재 기본 설정(면적 기준 배치, 높이 기반 출력시간)에서는 필수**
- `arrival_time`, `due_date` 는 시뮬레이션 시각 절대값(hour, t=0 = 월 09:00)
- `priority` 는 `NORMAL`/`URGENT` (대소문자 무관)
- 엑셀 "CSV UTF-8" 저장 파일도 그대로 읽힘
