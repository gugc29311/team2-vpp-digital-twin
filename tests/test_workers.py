# -*- coding: utf-8 -*-
"""
작업자 개인 ID (JA1.., PP1.., QI1..) 테스트 [명세서 1·10절 '어떤 작업자가 어느 작업', 14절 L3].
고정 설정 LOGIC_CFG: JA 1명, 후공정 2명, 검사원 1명.
"""
import pytest

from src.analysis.kpi import kpis, utilization, worker_utilization
from src.entities.order import Order
from src.model.simulation import VPPSimulation
from src.resources.resources import role_of
from src.validation.invariants import check, check_process_order, check_workers


def test_worker_ids(run_logic):
    res = run_logic(PACKER_COUNT=1)
    assert res.workers == {"job_assignment_workers": ["JA1"], "post_process_workers": ["PP1", "PP2"],
                           "quality_inspectors": ["QI1"], "packers": ["PK1"]}
    assert role_of("PP2") == "post_process_workers" and role_of("P1") is None and role_of("WASH1") is None


@pytest.mark.parametrize("packers", [0, 1])
def test_human_events_name_the_person(run_logic, packers):
    res = run_logic(PACKER_COUNT=packers)
    expected_role = {"JOB_ASSIGNMENT": "job_assignment_workers", "PART_REMOVAL": "post_process_workers",
                     "SUPPORT_REMOVAL": "post_process_workers", "SURFACE_TREATMENT": "post_process_workers",
                     "INSPECTION": "quality_inspectors",
                     "PACKAGING": "packers" if packers else "quality_inspectors"}
    for e in res.log.events:
        task = e.event.rsplit("_", 1)[0]
        if e.event.endswith(("_START", "_END")) and task in expected_role:
            assert e.resource in res.workers[expected_role[task]], e
    used = {e.resource for e in res.log.events if role_of(e.resource) == "post_process_workers"}
    assert used == {"PP1", "PP2"}                                     # 두 명 모두 실제로 일함


def test_lowest_numbered_free_worker_is_assigned(logic_cfg):
    """한 번에 한 작업만 있으면 항상 PP1 이 맡음 (PP2 는 쉼)."""
    res = VPPSimulation(logic_cfg, orders=[Order("O1", "P1", 0.0, 1, "Resin_A", 999.0, "NORMAL", 900.0, 40.0)]).run()
    wu = worker_utilization(res)
    assert wu["PP1"] > 0 and wu["PP2"] == 0


@pytest.mark.parametrize("extra", [{}, dict(USE_WORK_CALENDAR=True, PACKER_COUNT=1)])
def test_role_utilization_is_mean_of_personal(run_logic, extra):
    res = run_logic(**extra)
    u, wu = utilization(res), worker_utilization(res)
    for role, ids in res.workers.items():
        assert u[role]["utilization"] == pytest.approx(sum(wu[w] for w in ids) / len(ids))
    k = kpis(res)
    assert k["util_worker_PP1"] == pytest.approx(wu["PP1"])


def test_personal_busy_recorded_without_event_log(logic_cfg):
    a = VPPSimulation(logic_cfg).run()
    b = VPPSimulation(logic_cfg, keep_events=False).run()
    assert b.log.events == [] and b.log.person_busy == a.log.person_busy


def test_no_worker_does_two_things_at_once(run_logic):
    res = run_logic(USE_WORK_CALENDAR=True, INSPECTION_FAILURE_RATE=0.3, REWORK_ENABLED=True, RANDOM_SEED=3)
    assert check_workers(res) == [] and check_process_order(res) == [] and check(res) == []


# ---------------------------------------------------------------- 검사가 실제로 잡아내는지
def test_detects_worker_overlap(run_logic):
    res = run_logic()
    w, role, s, e, h = next(x for x in res.log.person_busy if x[0] == "PP1")
    res.log.person_busy.append((w, role, s + (e - s) / 2, e + 1.0, h))  # 같은 사람이 겹치는 두 번째 작업
    assert any("작업 겹침" in m for m in check_workers(res))


def test_detects_postprocess_before_previous_step_ends(run_logic):
    res = run_logic()
    i, ev = next((i, e) for i, e in enumerate(res.log.events) if e.event == "SUPPORT_REMOVAL_START")
    res.log.events[i] = ev._replace(sim_time=0.0)                      # UV 끝나기 전에 서포트 제거 시작
    assert any("서포트 제거" in m for m in check_process_order(res))


def test_detects_washing_before_removal_ends(run_logic):
    res = run_logic()
    i, ev = next((i, e) for i, e in enumerate(res.log.events) if e.event == "WASHING_START")
    res.log.events[i] = ev._replace(sim_time=0.0)
    assert any("세척 시작" in m for m in check_process_order(res))
