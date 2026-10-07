# -*- coding: utf-8 -*-
"""
OME Time Query (src/logger/state.py) 테스트 [명세서 1·18절, 14절 Level 2 '특정 시각 상태 = 이벤트 로그 손추적'].

손추적 기준: data/sample_orders.csv + 고정 설정 LOGIC_CFG (24시간 시계, JA 1명 1h, 배치 4부품/24h, 준비 2h,
출력 5h, 이동 0.5h, 세척 2h(로드 2부품, 1대), UV 3h, 후공정 2명). 배치:
  B00001 = O001, O002, O003 x2   확정 3  출력 6~11 (P1) -> 세척 로드 W1 13~15, W2 15~17 (둘 다 13 에 대기열 진입)
  B00003 = O005~O008              확정 11 준비 11~13 (JA1) 출력 13~18 (P2)
  B00002 = O004, O010 (Resin_B) 형성 중 / B00004 = O009 (14 에 열림)
  O009: 12 도착, JA1 이 B00003 준비 중이라 1h 대기 -> JA 13~14 / O010: 14 도착, JA 14~15
"""
import random

import pytest

from src.analysis.kpi import MACHINE_STATES
from src.entities.order import Order
from src.logger.state import format_time, parse_time, state_at
from src.model.simulation import VPPSimulation


# ---------------------------------------------------------------- 시각 형식
@pytest.mark.parametrize("text, t", [("Day 1 09:00", 0.0), ("Day 1 23:30", 14.5), ("Day 3 14:25", 48 + 14 + 25 / 60 - 9),
                                     ("day 2 10:00", 25.0), ("25.5", 25.5)])
def test_parse_time(text, t):
    assert parse_time(text) == pytest.approx(t)


@pytest.mark.parametrize("bad", ["Day 1 08:59", "Day 0 10:00", "Day 2 24:00", "Tuesday 10:00", "Day 2 9"])
def test_parse_time_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        parse_time(bad)


def test_format_time_round_trip():
    assert format_time(0.0) == "Day 1 09:00 (월)"
    assert format_time(parse_time("Day 3 14:25")) == "Day 3 14:25 (수)"
    assert format_time(parse_time("Day 6 11:10")).endswith("(토)")


# ---------------------------------------------------------------- 손추적 (Level 2)
def test_state_hand_trace_day1_2330(run_logic):
    res = run_logic()
    st = state_at(res, parse_time("Day 1 23:30"))              # t = 14.5
    where = {o: (v["process"], v["state"]) for o, v in st["orders"].items()}
    assert where == {
        "O001": ("Washing", "Processing"), "O002": ("Washing", "Processing"),   # 로드 W1 세척 중
        "O003": ("Washing", "Waiting"),                                          # 로드 W2 세척기 대기
        "O004": ("Batch Formation", "Waiting"),
        "O005": ("VPP Build", "Processing"), "O006": ("VPP Build", "Processing"),
        "O007": ("VPP Build", "Processing"), "O008": ("VPP Build", "Processing"),
        "O009": ("Batch Formation", "Waiting"),
        "O010": ("Job Assignment", "Processing"),
    }
    assert (st["wip"], st["completed"], st["late_orders"]) == (10, 0, [])
    assert st["machines"] == {"P1": "Idle", "P2": "Running", "WASH1": "Running", "UV1": "Idle"}
    assert st["workers"] == {"JA1": "JOB_ASSIGNMENT O010", "PP1": None, "PP2": None, "QI1": None}
    assert st["queues"] == {"printer": 0, "washing": 1, "uv": 0, "job_assignment_workers": 0,
                            "post_process_workers": 0, "quality_inspectors": 0}
    assert st["resin_L"] == pytest.approx(sum(v for s, v, _ in res.resin_log if s <= 14.5) / 1e6)
    assert st["resin_L"] > 0                                               # B00001·B00003 출력 시작분


def test_state_hand_trace_worker_queue(run_logic):
    """t = 12.5: JA1 은 B00003 출력 준비 중, O009 는 12 에 도착해 JA 대기 (1명 대기)."""
    st = state_at(run_logic(), 12.5)
    assert st["workers"]["JA1"] == "BUILD_PREPARATION B00003"
    assert st["queues"]["job_assignment_workers"] == 1
    assert st["orders"]["O009"]["process"] == "Order Reception"
    assert st["machines"]["P1"] == "Idle" and st["machines"]["P2"] == "Idle"


def test_late_orders(logic_cfg):
    orders = [Order("O1", "P1", 0.0, 1, "Resin_A", 1.0, "NORMAL", 900.0, 40.0),
              Order("O2", "P1", 0.0, 1, "Resin_A", 999.0, "NORMAL", 900.0, 40.0)]
    res = VPPSimulation(logic_cfg, orders=orders).run()
    assert state_at(res, 0.5)["late_orders"] == []
    assert state_at(res, 5.0)["late_orders"] == ["O1"]                    # 납기 1 경과 + 미완료
    end = state_at(res, res.end_time)
    assert end["late_orders"] == [] and end["completed"] == 2 and end["wip"] == 0


# ---------------------------------------------------------------- 다른 기록과 일치
def _sample_times(res, n=60, seed=0):
    rng = random.Random(seed)
    return [rng.uniform(0, res.end_time) for _ in range(n)]


@pytest.mark.parametrize("extra", [{}, dict(USE_WORK_CALENDAR=True, LOAD_HANDLING_TIME=("const", 0.2, "h")),
                                   dict(BREAKDOWN_ENABLED=True, CLEANING_LIQUID_CHANGE_EVERY_LOADS=2,
                                        CLEANING_LIQUID_CHANGE_TIME=("const", 0.25, "h"),
                                        EQUIPMENT_FAILURE={k: {"mtbf": 3.0, "repair": ("const", 1, "h"),
                                                               "pm_every": 20.0, "pm": ("const", 0.5, "h"),
                                                               "pm_offsets": [0.0]}
                                                           for k in ("printer", "washing", "uv_curing")})])
def test_machine_states_match_phase_record(run_logic, extra):
    """이벤트 로그로 재구성한 설비 상태 = 시뮬레이션 중 기록한 상태 구간 (캘린더 야간 멈춤 포함)."""
    res = run_logic(**extra)
    for t in _sample_times(res):
        st = state_at(res, t)
        for unit, state in st["machines"].items():
            cover = [s for n, s, a, b in res.machine_phases if n == unit and a <= t < b]
            assert state in MACHINE_STATES and cover == [state], (unit, t, state, cover)


def test_workers_and_load_queues_match_records(run_logic):
    res = run_logic(USE_WORK_CALENDAR=True)
    for t in _sample_times(res):
        st = state_at(res, t)
        for w, task in st["workers"].items():
            busy = any(p == w and a <= t < b for p, _, a, b, _ in res.log.person_busy)
            assert (task is not None) == busy, (w, t, task)
        for machine, key in (("washing_machines", "washing"), ("uv_curing_machines", "uv")):
            n = sum(1 for m, a, b in res.load_queue if m == machine and a <= t and (b is None or t < b))
            assert st["queues"][key] == n, (key, t)


def test_requires_event_log(logic_cfg):
    res = VPPSimulation(logic_cfg, keep_events=False).run()
    with pytest.raises(ValueError, match="이벤트 로그"):
        state_at(res, 1.0)
