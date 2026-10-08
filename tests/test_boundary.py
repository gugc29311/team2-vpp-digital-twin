# -*- coding: utf-8 -*-
"""
경계 조건 · 민감도 테스트 [회의 피드백 4·5: 입력값이 결과에 제대로 반영되는지].

설비 대수를 0 으로 두는 극단값 대신, 설비는 그대로 두고 '그 입력이 꺼지거나 극단일 때' 결과가 예측대로 바뀌는지 본다.

  불량률 0     : 출력 실패·검사 불량 이벤트 0, 재출력 0
  고장 OFF     : DOWN·MAINTENANCE 이벤트 0, 설비 Down·PM 시간 0
  고장 잦음    : 모든 프린터에 고장 발생, 처리량 감소
  인력 넉넉    : 인력 대기 감소 / 인력 최소(1명씩): 후공정 가동률·리드타임 증가
  처리시간 극단 : 세척 4h -> 세척기가 병목, 세척 대기 증가 / 세척 0 -> 세척 대기 감소
  이동 OFF·1h  : 단일 주문 리드타임 = 손계산 47h -/+ 이동 6구간
  위치 연속성   : 기본 설정 부품 순간이동 0, 이동을 끄면 검출됨 (검출기 대조군)
  교차 검증     : 기본 설정 4주 실행에서 '확인 필요' 0
  대별·단계 KPI : 설비 대별 처리 합 = 배치 수, 단계 시간 합 = 리드타임
기본값(parameters.py) 기반 실행은 2~4주, 시드 고정.
"""
import dataclasses

import pytest

from src.analysis.kpi import bottleneck, kpis, machine_state_hours, order_stage_table, unit_table, utilization
from src.entities.order import Order
from src.model.config import SimConfig
from src.model.simulation import VPPSimulation
from src.validation.crosscheck import cross_checks
from src.validation.invariants import check
from src.validation.locations import order_jumps, worker_jumps

ZERO = ("const", 0, "h")


def _base(weeks=2, **overrides):
    """기본 가정값 + random 모드 N주 (워밍업 없음)."""
    cfg = SimConfig.from_parameters().replace(ORDER_SOURCE="random", WARMUP_TIME=0, SIMULATION_TIME=weeks * 168,
                                              RANDOM_SEED=7)
    return cfg.replace(**overrides) if overrides else cfg


def _run(cfg, events=True):
    return VPPSimulation(cfg, keep_events=events).run()


def _events(res, *names):
    return [e for e in res.log.events if e.event in names]


@pytest.fixture(scope="module")
def base_res():
    return _run(_base())


# ---------------------------------------------------------------- 불량 · 고장
def test_zero_defect_rates_mean_no_failures():
    res = _run(_base(INSPECTION_FAILURE_RATE=0.0, PRINT_FAILURE_RATE=0.0))
    assert _events(res, "INSPECTION_FAILED", "PRINT_FAILED", "JOB_ASSIGNMENT_REWORK_START") == []
    assert sum(o.reworks for o in res.orders) == 0
    assert kpis(res)["rework_share_printed"] == 0
    assert check(res) == []


def test_defect_rate_is_reflected(base_res):
    """기본 불량률 4% -> 불량·재출력이 실제로 발생 (0% 설정과 대조)."""
    assert len(_events(base_res, "INSPECTION_FAILED")) > 0
    assert sum(o.reworks for o in base_res.orders) == len(_events(base_res, "INSPECTION_FAILED"))


def test_breakdown_off_means_no_down_or_pm():
    res = _run(_base(BREAKDOWN_ENABLED=False, CLEANING_LIQUID_CHANGE_EVERY_LOADS=None))
    assert _events(res, "DOWN_START", "MAINTENANCE_START", "CLEANING_START") == []
    for hours in machine_state_hours(res).values():
        assert hours["Down"] == 0 and hours["Maintenance"] == 0
    assert all(r["failures"] == 0 for r in unit_table(res))


def test_frequent_breakdowns_hit_every_printer_and_cut_output(base_res):
    fail = dict(SimConfig.from_parameters().EQUIPMENT_FAILURE)
    fail["printer"] = dict(fail["printer"], mtbf=4.0)
    res = _run(_base(EQUIPMENT_FAILURE=fail))
    printers = [r for r in unit_table(res) if r["kind"] == "프린터"]
    assert printers and all(r["failures"] > 0 and r["down_h"] > 0 for r in printers)
    assert kpis(res)["throughput_per_week"] < kpis(base_res)["throughput_per_week"]
    assert check(res) == []


# ---------------------------------------------------------------- 인력
def test_ample_workers_reduce_worker_waiting(base_res):
    res = _run(_base(JOB_ASSIGNMENT_WORKER_COUNT=20, POST_PROCESS_WORKER_COUNT=20, QUALITY_INSPECTOR_COUNT=20))
    k, k0 = kpis(res), kpis(base_res)
    for role in ("post_process_workers", "quality_inspectors"):
        assert k[f"{role}_wait_h_mean"] < k0[f"{role}_wait_h_mean"]


def test_minimum_workers_raise_utilization_and_lead_time(base_res):
    res = _run(_base(JOB_ASSIGNMENT_WORKER_COUNT=1, POST_PROCESS_WORKER_COUNT=1, QUALITY_INSPECTOR_COUNT=1))
    u, u0 = utilization(res), utilization(base_res)
    assert u["post_process_workers"]["utilization"] > u0["post_process_workers"]["utilization"]
    assert kpis(res)["lead_calendar_h_mean"] > kpis(base_res)["lead_calendar_h_mean"]
    assert check(res) == []


# ---------------------------------------------------------------- 처리시간 극단 (설비 대수는 유지)
def test_very_long_washing_makes_washer_the_bottleneck(base_res):
    res = _run(_base(WASHING_TIME=("const", 4, "h")), events=False)
    assert bottleneck(res)[0] == "washing_machines"
    assert kpis(res)["washing_wait_h_mean"] > 5 * kpis(base_res)["washing_wait_h_mean"]


def test_near_zero_washing_reduces_washing_wait(base_res):
    res = _run(_base(WASHING_TIME=ZERO, LOAD_HANDLING_TIME=ZERO), events=False)
    assert kpis(res)["washing_wait_h_mean"] < kpis(base_res)["washing_wait_h_mean"]
    assert utilization(res)["washing_machines"]["utilization"] < 0.01


# ---------------------------------------------------------------- 이동 (손계산)
def _single(logic_cfg, **overrides):
    order = Order("O1", "P1", 0.0, 1, "Resin_A", 999.0, "NORMAL", 900.0, 40.0)
    return VPPSimulation(logic_cfg.replace(**overrides), orders=[order]).run()


@pytest.mark.parametrize("overrides, expected", [
    ({}, 47.0),                                                          # 이동 6구간 x 0.5h 포함
    ({"TRANSPORT_ENABLED": False}, 47.0 - 6 * 0.5),
    ({"DEFAULT_TRANSPORT_TIME": ("const", 1, "h")}, 47.0 + 6 * 0.5),
])
def test_transport_time_changes_lead_time_by_hand_calculation(logic_cfg, overrides, expected):
    res = _single(logic_cfg, **overrides)
    assert res.orders[0].completed_time == pytest.approx(expected)


def test_packing_transport_done_by_packer_when_present(run_logic):
    res = run_logic(PACKER_COUNT=1)
    movers = {e.resource for e in res.log.events if e.event == "TRANSPORT_6_TO_PACKING_START"}
    assert movers == {"PK1"}
    assert check(res) == []


# ---------------------------------------------------------------- 위치 연속성
def test_no_part_teleports_in_default_model(base_res):
    assert order_jumps(base_res) == []


def test_teleport_detector_finds_jumps_when_transport_is_off(logic_cfg):
    res = _single(logic_cfg, TRANSPORT_ENABLED=False)
    jumps = order_jumps(res)
    # 출력 -> 후공정 -> 세척 -> UV -> 후공정 -> 검사 -> 포장 : 방이 바뀌는 6번 모두 순간이동
    assert len(jumps) == 6
    assert [(j["from"], j["to"]) for j in jumps][0] == ("Print Room", "Post-processing Room")


def test_no_worker_teleports_when_walking_is_modelled(base_res):
    """기본(distance) 모드: 작업자의 빈손 이동까지 모두 WALK 로 모델링 -> 순간이동 0."""
    assert worker_jumps(base_res) == []
    assert any(e.event == "WALK_START" for e in base_res.log.events)


def test_fixed_mode_has_worker_teleports(run_logic):
    """대조군: 기존 fixed 모드는 빈손 이동이 없어 작업자 순간이동이 검출됨."""
    assert worker_jumps(run_logic()) != []


# ---------------------------------------------------------------- 교차 검증 · 대별 · 단계 KPI
def test_cross_checks_pass_on_default_four_weeks():
    rows = cross_checks(_run(_base(weeks=4)))
    bad = [r for r in rows if r["판정"] == "확인 필요"]
    assert bad == []


def test_cross_check_flags_a_wrong_setting(base_res):
    """관측(불량 4%)과 다른 설정(20%)을 대조하면 '확인 필요' 가 나와야 함 — 검사기 대조군."""
    fake = dataclasses.replace(base_res, cfg=base_res.cfg.replace(INSPECTION_FAILURE_RATE=0.20))
    row = next(r for r in cross_checks(fake) if r["항목"] == "검사 불량률")
    assert row["판정"] == "확인 필요"


def test_unit_table_matches_batches_and_type_utilization(base_res):
    rows = {r["unit"]: r for r in unit_table(base_res)}
    printed = [b for b in base_res.batches if b.print_end is not None]
    assert sum(r["jobs"] for r in rows.values() if r["kind"] == "프린터") == len(printed)
    assert sum(r["parts"] for r in rows.values() if r["kind"] == "프린터") == sum(b.n_parts for b in printed)
    mean_util = sum(r["utilization"] for r in rows.values() if r["kind"] == "프린터") / base_res.cfg.VPP_PRINTER_COUNT
    assert mean_util == pytest.approx(utilization(base_res)["vpp_printers"]["utilization"], rel=1e-6)


def test_order_stages_sum_to_lead_time(base_res):
    rows = order_stage_table(base_res)
    assert rows
    for r in rows:
        assert all(r[k] >= -1e-9 for k in ("ja", "batch", "queue", "print", "post"))
        assert r["pre_print"] + r["after_start"] == pytest.approx(r["lead"])
    assert sum(r["lead"] for r in rows) / len(rows) == pytest.approx(kpis(base_res)["lead_calendar_h_mean"])


# ---------------------------------------------------------------- 이동 모델 (거리 ÷ 속도) · AMR
def _amr_single(logic_cfg, **overrides):
    """주문 1건, AMR + 거리 모드 (나머지는 LOGIC_CFG 고정 처리시간)."""
    return _single(logic_cfg, TRANSPORT_MODE="amr", MOVE_TIME_MODE="distance", **overrides)


def _durations(res, event_base):
    """이벤트 쌍의 소요 시간 [초]. 이벤트 시각은 1e-6 h(3.6 ms) 단위로 기록되므로 비교는 0.01 초 허용."""
    starts, out = {}, []
    for e in res.log.events:
        key = (e.resource, e.entity_id)
        if e.event == event_base + "_START":
            starts[key] = e.sim_time
        elif e.event == event_base + "_END" and key in starts:
            out.append((e.sim_time - starts.pop(key)) * 3600)          # 초
    return out


def test_amr_leg_times_match_hand_calculation(logic_cfg):
    """
    손계산 (parameters 배치도): 프린터실 문 7.4 m, 후공정실 문 22.6 m, 방 깊이 2.5 m
      HANDOFF ① = 2.5 m ÷ 0.8 m/s + 선반 10 s = 13.125 s
      AMR ①     = 적재 15 s + |22.6 − 7.4| ÷ 1.0 m/s + 하역 15 s = 45.2 s  (AMR 은 프린터실 문에서 시작 -> 빈 차 이동 없음)
      RECEIVE ① = 선반 10 s + 2.5 m ÷ 0.8 m/s = 13.125 s
    """
    res = _amr_single(logic_cfg)
    assert _durations(res, "HANDOFF_1_PRINT_TO_REMOVAL") == pytest.approx([13.125], abs=0.01)
    assert _durations(res, "TRANSPORT_1_PRINT_TO_REMOVAL") == pytest.approx([45.2], abs=0.01)
    assert _durations(res, "RECEIVE_1_PRINT_TO_REMOVAL") == pytest.approx([13.125], abs=0.01)
    first_amr = next(e for e in res.log.events if e.event == "TRANSPORT_1_PRINT_TO_REMOVAL_START")
    assert first_amr.resource.startswith("AMR")
    assert check(res) == [] and order_jumps(res) == [] and worker_jumps(res) == []


def test_amr_roles_sender_and_receiver(logic_cfg):
    """보내는 사람: ①~⑤ 후공정(PP), ⑥ 검사원(QI) / 운반: AMR / 받는 사람: ⑤⑥ 검사원, 나머지 후공정."""
    res = _amr_single(logic_cfg)
    who = {}
    for e in res.log.events:
        if e.event.endswith("_START") and e.event.startswith(("HANDOFF_", "TRANSPORT_", "RECEIVE_")):
            who.setdefault(e.event.rsplit("_", 1)[0], set()).add(e.resource[:2])
    assert who["HANDOFF_6_TO_PACKING"] == {"QI"} and who["RECEIVE_6_TO_PACKING"] == {"QI"}
    assert who["HANDOFF_5_TO_INSPECTION"] == {"PP"} and who["RECEIVE_5_TO_INSPECTION"] == {"QI"}
    assert all(v == {"AM"} for k, v in who.items() if k.startswith("TRANSPORT_"))


def test_worker_walk_time_is_distance_over_speed(logic_cfg):
    """검사원 QI1 은 검사실 작업 위치에서 시작 -> ⑤ 를 받으러 검사실 문까지 2.5 m ÷ 1.0 m/s = 2.5 s."""
    res = _amr_single(logic_cfg)
    walk = next(e for e in res.log.events if e.event == "WALK_START" and e.resource == "QI1")
    assert walk.detail.startswith("Inspection Room->Inspection Room:door")
    end = next(e for e in res.log.events if e.event == "WALK_END" and e.resource == "QI1" and e.sim_time >= walk.sim_time)
    assert (end.sim_time - walk.sim_time) * 3600 == pytest.approx(2.5, abs=0.01)


def test_fewer_amrs_mean_longer_amr_wait():
    one = kpis(_run(_base(AMR_COUNT=1), events=False))
    two = kpis(_run(_base(AMR_COUNT=2), events=False))
    assert one["amr_wait_h_mean"] >= two["amr_wait_h_mean"]
    assert one["amr_trips_per_week"] > 0


def test_worker_carry_mode_has_no_amr_and_no_teleports():
    res = _run(_base(TRANSPORT_MODE="worker"))
    assert not any((e.resource or "").startswith("AMR") for e in res.log.events)
    assert not any(e.event.startswith(("HANDOFF_", "RECEIVE_")) for e in res.log.events)
    assert worker_jumps(res) == [] and order_jumps(res) == [] and check(res) == []


def test_amr_mode_requires_distance_mode(logic_cfg):
    with pytest.raises(ValueError, match="MOVE_TIME_MODE"):
        logic_cfg.replace(TRANSPORT_MODE="amr", MOVE_TIME_MODE="fixed")
