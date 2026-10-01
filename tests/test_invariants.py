# -*- coding: utf-8 -*-
"""
불변식(src/validation/invariants.py) 테스트.
  - 여러 설정 조합으로 돌린 결과에 위반이 없어야 함
  - 결과를 일부러 망가뜨리면 검사가 잡아내야 함 (검사 자체가 동작하는지)
"""
import pytest

from src.analysis.event_log import Event
from src.model.config import SimConfig
from src.model.simulation import VPPSimulation
from src.validation.invariants import check

FAIL = {"printer": {"mtbf": 3.0, "repair": ("const", 2, "h"), "pm_every": 20.0, "pm": ("const", 1, "h"),
                    "pm_offsets": [0.0, 10.0]},
        "washing": {"mtbf": 2.0, "repair": ("const", 1, "h"), "pm_every": 15.0, "pm": ("const", 0.5, "h"),
                    "pm_offsets": [0.0]},
        "uv_curing": {"mtbf": 2.0, "repair": ("const", 1, "h"), "pm_every": 15.0, "pm": ("const", 0.5, "h"),
                      "pm_offsets": [0.0]}}

CASES = {
    "logic": {},
    "calendar": dict(USE_WORK_CALENDAR=True, PRINTER_UNATTENDED=True),
    "calendar_attended": dict(USE_WORK_CALENDAR=True, PRINTER_UNATTENDED=False),
    "breakdown_cleaning": dict(BREAKDOWN_ENABLED=True, EQUIPMENT_FAILURE=FAIL, USE_WORK_CALENDAR=True,
                               CLEANING_LIQUID_CHANGE_EVERY_LOADS=2, CLEANING_LIQUID_CHANGE_TIME=("const", 0.25, "h")),
    "rework": dict(INSPECTION_FAILURE_RATE=0.3, PRINT_FAILURE_RATE=0.1, REWORK_ENABLED=True, RANDOM_SEED=3),
    "scrap": dict(INSPECTION_FAILURE_RATE=0.3, REWORK_ENABLED=False, RANDOM_SEED=3),
    "area_batching": dict(BUILD_PLATE_MAX_PARTS=None, BUILD_PLATE_AREA_MM2=4000, BATCH_FILL_RATIO=0.7),
    "packer": dict(PACKER_COUNT=1, USE_WORK_CALENDAR=True),
    "spt": dict(DEFAULT_SCHEDULING_RULE="SPT"),
    "edd": dict(DEFAULT_SCHEDULING_RULE="EDD"),
    "urgent_first": dict(DEFAULT_SCHEDULING_RULE="URGENT_FIRST", ORDER_SOURCE="random", SIMULATION_TIME=300,
                         ORDER_ARRIVAL_RATE_PER_WEEK=15, URGENT_PROBABILITY=0.3),
    "random_calendar": dict(ORDER_SOURCE="random", SIMULATION_TIME=400, ORDER_ARRIVAL_RATE_PER_WEEK=15,
                            USE_WORK_CALENDAR=True, INSPECTION_FAILURE_RATE=0.2, REWORK_ENABLED=True),
}


@pytest.mark.parametrize("name", list(CASES))
def test_no_invariant_violations(run_logic, name):
    assert check(run_logic(**CASES[name])) == []


def test_no_violations_with_assumed_parameters():
    """parameters.py 가정값 그대로 (csv 샘플 + random 2주)."""
    base = SimConfig.from_parameters()
    assert check(VPPSimulation(base.replace(ORDER_SOURCE="csv")).run()) == []
    cfg = base.replace(ORDER_SOURCE="random", SCENARIO="Normal", WARMUP_TIME=0, SIMULATION_TIME=2 * 168)
    assert check(VPPSimulation(cfg).run()) == []


# ---------------------------------------------------------------- 검사가 실제로 잡아내는지
def test_detects_capacity_overflow(run_logic):
    res = run_logic()
    r, s, e, h = next(x for x in res.log.busy if x[0] == "quality_inspectors")
    res.log.busy.append((r, s, e, h))                           # 검사원 1명인데 같은 구간 2번 점유
    assert any("동시 점유" in m for m in check(res))


def test_detects_print_time_mismatch(run_logic):
    res = run_logic()
    res.batches[0].print_end += 1.0
    assert any("출력시간" in m for m in check(res))


def test_detects_mixed_material(run_logic):
    res = run_logic()
    a = next(b for b in res.batches if b.material == "Resin_A")
    b = next(b for b in res.batches if b.material == "Resin_B")
    a.parts.append(b.parts[0])
    msgs = check(res)
    assert any("재료 혼합" in m for m in msgs)
    assert any("번 배치에 들어감" in m for m in msgs)            # 같은 부품이 두 배치에


def test_detects_off_hours_start(run_logic):
    res = run_logic(USE_WORK_CALENDAR=True)
    ev = next(e for e in res.log.events if e.event == "INSPECTION_START")
    assert not res.calendar.is_open(10.0)                       # t=10 = 월 19:00
    res.log.events.append(ev._replace(sim_time=10.0))
    assert any("근무시간 밖" in m for m in check(res))


def _shift(res, event, dt):
    i, ev = next((i, e) for i, e in enumerate(res.log.events) if e.event == event)
    res.log.events[i] = ev._replace(sim_time=ev.sim_time + dt)


@pytest.mark.parametrize("event, dt, label", [
    ("TRANSPORT_2_TO_WASHING_END", 0.3, "이동② 도착 -> 세척 대기열"),      # 도착 전에 세척 대기열 진입
    ("TRANSPORT_5_TO_INSPECTION_END", 1.0, "이동⑤ 도착 -> 검사 시작"),      # 도착 전에 검사 시작
    ("TRANSPORT_1_PRINT_TO_REMOVAL_START", -2.0, "출력 종료 -> 이동① 출발"),  # 출력 끝나기 전에 출발
])
def test_detects_transport_order_violation(run_logic, event, dt, label):
    """명세서 14절 L3 '이동하기 전에 다음 위치 도착'."""
    from src.validation.invariants import check_transport
    res = run_logic()
    assert check_transport(res) == []
    _shift(res, event, dt)
    assert any(label in m for m in check_transport(res))


def test_detects_start_during_downtime(run_logic):
    res = run_logic(BREAKDOWN_ENABLED=True, EQUIPMENT_FAILURE=FAIL)
    u = next(u for u in res.units if u.log)
    _, s, e = u.log[0]
    res.log.events.append(Event(round((s + e) / 2, 6), "", "BATCH", "BX", "VPP_BUILD_START", "VPP Build",
                                "Print Room", "Processing", u.name, ""))
    assert any("중" in m and u.name in m for m in check(res))
