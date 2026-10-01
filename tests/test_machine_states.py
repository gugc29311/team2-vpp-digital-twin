# -*- coding: utf-8 -*-
"""
장비 상태 6종 (Idle / Setup / Running / Waiting / Down / Maintenance) 테스트 [명세서 11절].
고정 설정 LOGIC_CFG 기반.
"""
from collections import defaultdict

import pytest

from src.analysis.kpi import MACHINE_STATES, kpis, machine_state_hours
from src.model.simulation import VPPSimulation

HEIGHT = dict(VPP_BUILD_TIME_MODE="height", BUILD_SETUP_TIME=("const", 0.5, "h"), LAYER_THICKNESS_MM=0.05,
              TIME_PER_LAYER=("const", 8, "s"))
FAIL = {k: {"mtbf": 3.0, "repair": ("const", 1, "h"), "pm_every": 20.0, "pm": ("const", 0.5, "h"),
            "pm_offsets": [0.0]} for k in ("printer", "washing", "uv_curing")}
BREAKDOWN = dict(BREAKDOWN_ENABLED=True, EQUIPMENT_FAILURE=FAIL, CLEANING_LIQUID_CHANGE_EVERY_LOADS=2,
                 CLEANING_LIQUID_CHANGE_TIME=("const", 0.25, "h"))


def _phases(res):
    by = defaultdict(list)
    for name, state, s, e in res.machine_phases:
        by[name].append((s, e, state))
    return {k: sorted(v) for k, v in by.items()}


def _total(res, unit, state):
    return sum(e - s for n, st, s, e in res.machine_phases if n == unit and st == state)


@pytest.mark.parametrize("extra", [{}, HEIGHT, BREAKDOWN, dict(USE_WORK_CALENDAR=True, LOAD_HANDLING_TIME=("const", 0.2, "h")),
                                   dict(USE_WORK_CALENDAR=True, PRINTER_UNATTENDED=False, **HEIGHT)])
def test_phases_cover_whole_run_without_gaps(run_logic, extra):
    res = run_logic(**extra)
    ph = _phases(res)
    assert set(ph) == {u.name for u in res.units}
    for unit, iv in ph.items():
        assert iv[0][0] == pytest.approx(0.0) and iv[-1][1] == pytest.approx(res.end_time), unit
        for (s1, e1, _), (s2, _, _) in zip(iv, iv[1:]):
            assert s2 == pytest.approx(e1, abs=1e-9), (unit, e1, s2)                 # 빈틈·겹침 없음
        assert all(st in MACHINE_STATES and e > s for s, e, st in iv)


def test_printer_setup_and_running_split(run_logic):
    """height 모드: Setup = 0.5h x 배치 수, Setup + Running = 출력시간 합 (무인운전, 캘린더 없음)."""
    res = run_logic(**HEIGHT)
    for u in (u for u in res.units if u.name.startswith("P")):
        mine = [b for b in res.batches if b.print_start is not None and
                any(e.event == "VPP_BUILD_START" and e.entity_id == b.batch_id and e.resource == u.name
                    for e in res.log.events)]
        assert _total(res, u.name, "Setup") == pytest.approx(0.5 * len(mine))
        assert _total(res, u.name, "Setup") + _total(res, u.name, "Running") == \
            pytest.approx(sum(b.build_time for b in mine))


def test_printer_setup_running_events(run_logic):
    res = run_logic(**HEIGHT)
    for b in res.batches:
        ev = [e for e in res.log.events if e.entity_type == "MACHINE" and e.detail == b.batch_id]
        setup = next(e for e in ev if e.event == "SETUP_START")
        run = next(e for e in ev if e.event == "RUNNING_START")
        assert setup.sim_time == pytest.approx(b.print_start)
        assert run.sim_time == pytest.approx(b.print_start + b.setup_time)
        assert (setup.state, run.state) == ("Setup", "Running")


def test_down_and_maintenance_match_unit_log(run_logic):
    """고장 수리 = Down, PM + 세척액 교체 = Maintenance (EquipmentUnit.log 의 fail / pm / clean)."""
    res = run_logic(**BREAKDOWN)
    assert any(k == "fail" for u in res.units for k, _, _ in u.log)
    for u in res.units:
        dur = lambda kinds: sum(e - s for k, s, e in u.log if k in kinds)
        assert _total(res, u.name, "Down") == pytest.approx(dur({"fail"}))
        assert _total(res, u.name, "Maintenance") == pytest.approx(dur({"pm", "clean"}))


def test_washing_running_is_processing_time_and_night_is_waiting(run_logic):
    """캘린더 사용: 세척 Running 합 = 세척시간(2h) x 로드 수, Running 은 근무시간에만. 야간 멈춤은 Waiting."""
    res = run_logic(USE_WORK_CALENDAR=True)
    wash = next(u for u in res.units if u.name.startswith("WASH"))
    assert _total(res, wash.name, "Running") == pytest.approx(2.0 * wash.loads)
    for n, st, s, e in res.machine_phases:
        if st == "Running" and not n.startswith("P"):
            assert res.calendar.is_open(s) and res.calendar.work_hours(s, e) == pytest.approx(e - s)


@pytest.mark.parametrize("calendar", [False, True])
def test_loading_unloading_is_setup(run_logic, calendar):
    """
    적재·인출(각 0.1h) = Setup. 캘린더 사용 시 점심·퇴근에 걸려 멈춘 시간은 Setup 이 아니라 Waiting
    -> 어느 경우든 Setup 합 = 실제 적재·인출 시간 합.
    """
    res = run_logic(LOAD_HANDLING_TIME=("const", 0.2, "h"), USE_WORK_CALENDAR=calendar)
    for u in res.units:
        if not u.name.startswith("P"):
            assert _total(res, u.name, "Setup") == pytest.approx(0.2 * u.loads)
    for n, st, s, e in res.machine_phases:
        if st == "Setup" and not n.startswith("P"):
            assert res.calendar.work_hours(s, e) == pytest.approx(e - s)            # Setup 은 근무시간 안에만


def test_state_kpis_sum_to_measured_time(run_logic):
    res = run_logic(**BREAKDOWN)
    k, hours = kpis(res), machine_state_hours(res)
    for u in res.units:
        assert sum(hours[u.name].values()) == pytest.approx(res.end_time)
        per_week = sum(k[f"{u.name}_{s.lower()}_h_per_week"] for s in MACHINE_STATES)
        assert per_week == pytest.approx(res.end_time / (res.end_time / 40))          # 24h 시계: 1주 = 40h


def test_phases_recorded_without_event_log(logic_cfg):
    cfg = logic_cfg.replace(**BREAKDOWN)
    assert VPPSimulation(cfg, keep_events=False).run().machine_phases == VPPSimulation(cfg).run().machine_phases


def test_machine_events_match_phases(run_logic):
    """
    이벤트 로그의 설비 상태 전환 = 구간 기록 (캘린더 없음 -> 야간 분할 없음).
    같은 시각에 전환이 여러 번이면(예: 배정 Waiting 직후 Setup) 길이 0 구간은 기록되지 않으므로 마지막 이벤트로 비교.
    """
    res = run_logic(LOAD_HANDLING_TIME=("const", 0.2, "h"), **BREAKDOWN, **HEIGHT)
    ph = _phases(res)
    last = {}
    for e in res.log.events:
        if e.entity_type == "MACHINE" and e.sim_time < res.end_time:
            last[(e.entity_id, e.sim_time)] = e.state
    assert last
    for (unit, t), state in last.items():
        cover = [st for s, end, st in ph[unit] if s <= t + 1e-6 < end]        # 이벤트 시각은 6자리 반올림
        assert cover == [state], (unit, t, state, cover)
