# -*- coding: utf-8 -*-
"""
v0.1.0 에서 추가한 KPI · 이벤트 로그 · main.py 옵션 검사 (고정 설정 LOGIC_CFG 사용 — parameters.py 값과 무관).
실행: 프로젝트 최상위 폴더에서  python -m pytest
"""
import csv
import math
import numbers
import subprocess
import sys

import pytest

from src.analysis.kpi import _level_series, _step_stats, bottleneck, daily_table, kpis, save_outputs, utilization
from src.entities.order import Order
from src.model.simulation import VPPSimulation
from src.resources.resources import role_of

HUMANS = ("job_assignment_workers", "post_process_workers", "quality_inspectors", "packers")


def one_order(due, arrival=0.0):
    return [Order("O1", "P1", arrival, 1, "Resin_A", due, "NORMAL", 900.0, 40.0)]


# ---------------------------------------------------------------- 계단함수 도구
def test_step_stats_hand_calculation():
    s = _level_series([(0, 4), (2, None)])                 # 0~2: 1개, 2~4: 2개, 4~: 1개(진행 중)
    assert s == [(0, 1), (2, 2), (4, 1)]
    assert _step_stats(s, 0, 6) == pytest.approx((8 / 6, 2))   # (1x2 + 2x2 + 1x2) / 6
    assert _step_stats(s, 1, 3) == pytest.approx((1.5, 2))     # (1x1 + 2x1) / 2


# ---------------------------------------------------------------- kpis() 형식
def test_kpis_are_all_numeric(run_logic):
    """문자열 KPI 가 섞이면 replications.summarize 가 깨짐 — bottleneck 은 kpis() 밖에 둔다."""
    k = kpis(run_logic())
    bad = {key: v for key, v in k.items() if not isinstance(v, numbers.Real) or isinstance(v, bool)}
    assert not bad
    for key in ("lead_work_h_mean", "lead_work_h_p95", "lead_calendar_h_mean", "lead_calendar_h_p95",
                "throughput_per_week", "tardiness_work_h_mean", "wip_mean", "wip_little",
                "printer_queue_mean", "printer_wait_h_mean"):
        assert key in k, key


# ---------------------------------------------------------------- 처리량 · WIP · Little
def test_throughput_and_little_law_when_all_orders_finish(run_logic):
    """csv 샘플은 전부 완료 후 종료 -> WIP 면적 = 리드타임 합 이므로 wip_mean == wip_little (정확히)."""
    res = run_logic()
    k = kpis(res)
    assert k["completion_ratio"] == 1.0
    assert k["throughput_per_week"] == pytest.approx(k["orders_per_week"])
    assert k["wip_mean"] == pytest.approx(k["wip_little"])
    assert k["wip_mean"] == pytest.approx(sum(o.completed_time - o.arrival_time for o in res.orders) / res.end_time)
    assert 1 <= k["wip_max"] <= len(res.orders)


def test_little_law_in_random_mode(logic_cfg):
    cfg = logic_cfg.replace(ORDER_SOURCE="random", SIMULATION_TIME=2000, WARMUP_TIME=200,
                            ORDER_ARRIVAL_RATE_PER_WEEK=10, RANDOM_SEED=3)   # JA 부하 25% (40이면 100%, 정상상태 없음)
    k = kpis(VPPSimulation(cfg, keep_events=False).run())
    assert k["wip_mean"] == pytest.approx(k["wip_little"], rel=0.05)
    assert k["throughput_per_week"] == pytest.approx(k["orders_per_week"] * k["completion_ratio"], rel=0.05)


# ---------------------------------------------------------------- 납기 · Tardiness
def test_unfinished_overdue_order_counts_as_late(logic_cfg):
    """종료 시 미완료 + 납기 경과 -> 지연 (예전에는 분모에서 빠져 NaN 이었음)."""
    res = VPPSimulation(logic_cfg.replace(SIMULATION_TIME=5), orders=one_order(due=1.0)).run()
    assert res.orders[0].completed_time is None
    assert kpis(res)["on_time_all"] == 0.0


def test_unfinished_order_not_yet_due_is_excluded(logic_cfg):
    res = VPPSimulation(logic_cfg.replace(SIMULATION_TIME=5), orders=one_order(due=999.0)).run()
    assert math.isnan(kpis(res)["on_time_all"])


def test_tardiness_hand_calculation(logic_cfg):
    res = VPPSimulation(logic_cfg, orders=one_order(due=1.0)).run()     # 24h 시계, 한참 늦게 완료
    o = res.orders[0]
    k = kpis(res)
    assert k["on_time_all"] == 0.0
    assert k["tardiness_work_h_total"] == pytest.approx(o.completed_time - 1.0)
    assert k["tardiness_work_h_mean"] == pytest.approx(o.completed_time - 1.0)


def test_tardiness_is_zero_when_on_time(run_logic):
    k = kpis(run_logic(SIMULATION_TIME=10_000))
    assert k["tardiness_work_h_total"] >= 0
    if k["on_time_all"] == 1.0:
        assert k["tardiness_work_h_total"] == 0.0


# ---------------------------------------------------------------- 프린터 대기열
def test_printer_wait_matches_batch_times(run_logic):
    res = run_logic()
    k = kpis(res)
    waits = [b.print_start - b.closed_time for b in res.batches]
    assert min(waits) >= 2 - 1e-9                                         # BUILD_PREPARATION 2h 포함
    assert k["printer_wait_h_mean"] == pytest.approx(sum(waits) / len(waits))
    assert k["printer_queue_mean"] >= 0 and k["printer_queue_max"] >= 1


# ---------------------------------------------------------------- 세척·UV 로드 대기
def _two_loads(logic_cfg, keep_events=True):
    """
    주문 1건 x 4부품 -> 배치 1개 -> 로드 2개(용량 2). 세척기·UV 각 1대, 작업자 2명이라 두 로드의 이동이 동시.
      세척: 두 로드가 같은 시각 대기열 진입 -> 대기 0h, 2h(세척 2h)
      UV  : 세척이 모두 끝난 뒤 두 로드가 같은 시각 진입 -> 대기 0h, 3h(UV 3h)
    """
    orders = [Order("O1", "P1", 0.0, 4, "Resin_A", 999.0, "NORMAL", 900.0, 40.0)]
    return VPPSimulation(logic_cfg, orders=orders, keep_events=keep_events).run()


def test_load_wait_hand_calculation(logic_cfg):
    res = _two_loads(logic_cfg)
    k = kpis(res)
    assert k["washing_wait_h_mean"] == pytest.approx(1.0)          # (0 + 2) / 2
    assert k["washing_wait_h_p95"] == pytest.approx(0.05 * 0 + 0.95 * 2)
    assert k["uv_wait_h_mean"] == pytest.approx(1.5)               # (0 + 3) / 2
    assert k["washing_queue_mean"] == pytest.approx(2.0 / res.end_time)   # 1로드 x 2h
    assert k["uv_queue_mean"] == pytest.approx(3.0 / res.end_time)        # 1로드 x 3h
    assert k["washing_queue_max"] == k["uv_queue_max"] == 1


def test_load_wait_matches_event_log(logic_cfg):
    res = _two_loads(logic_cfg)
    enter, start = {}, {}
    for e in res.log.events:
        t, eid, ev = e.sim_time, e.entity_id, e.event
        if ev.endswith("_QUEUE_ENTER") and ev != "PRINTER_QUEUE_ENTER":
            enter[(eid, ev.replace("_QUEUE_ENTER", ""))] = t
        elif ev in ("WASHING_START", "UV_CURING_START"):
            start[(eid, ev.replace("_START", ""))] = t
    from_events = sorted(round(start[key] - enter[key], 6) for key in enter)
    from_record = sorted(round(s - e, 6) for _, e, s in res.load_queue)
    assert from_events == from_record == [0.0, 0.0, 2.0, 3.0]


def test_load_wait_kpis_without_event_log(logic_cfg):
    """반복 실험 경로(keep_events=False)에서도 같은 값."""
    with_ev, without = kpis(_two_loads(logic_cfg)), kpis(_two_loads(logic_cfg, keep_events=False))
    for key in ("washing_wait_h_mean", "uv_wait_h_mean", "washing_queue_mean", "uv_queue_max"):
        assert without[key] == pytest.approx(with_ev[key])


def test_daily_table_has_load_queues(run_logic):
    res = run_logic()
    rows = daily_table(res)
    assert all("washing_queue_mean" in r and "uv_queue_mean" in r for r in rows)
    total = sum(r["washing_queue_mean"] * 24 for r in rows[:-1]) \
        + rows[-1]["washing_queue_mean"] * (res.end_time - 24 * (len(rows) - 1))
    assert total == pytest.approx(kpis(res)["washing_queue_mean"] * res.end_time, abs=0.01)


# ---------------------------------------------------------------- 병목
def test_bottleneck_is_highest_load(run_logic):
    res = run_logic()
    name, value = bottleneck(res)
    k, u = kpis(res), utilization(res)
    loads = {n: v["utilization"] for n, v in u.items() if n != "vpp_printers"}
    loads["vpp_printers"] = k["printer_rho"]
    assert name in res.capacities
    assert value == pytest.approx(max(loads.values()))


# ---------------------------------------------------------------- 일별 표
def test_daily_table_sums_to_completed_orders(run_logic, tmp_path):
    res = run_logic()
    rows = daily_table(res)
    assert len(rows) == math.ceil(res.end_time / 24)
    assert sum(r["throughput"] for r in rows) == sum(o.completed_time is not None for o in res.orders)
    assert all(0 <= r["printer_util"] <= 1 for r in rows)
    save_outputs(res, str(tmp_path), daily=True)
    with open(tmp_path / "daily_summary.csv", encoding="utf-8-sig") as f:
        assert len(list(csv.DictReader(f))) == len(rows)


def test_daily_summary_not_written_by_default(run_logic, tmp_path):
    save_outputs(run_logic(), str(tmp_path))
    assert not (tmp_path / "daily_summary.csv").exists()


# ---------------------------------------------------------------- 이벤트 로그
def _events(res):
    return [(e.sim_time, e.entity_id, e.event) for e in res.log.events]


def test_every_queue_enter_is_followed_by_start(run_logic):
    res = run_logic(CLEANING_LIQUID_CHANGE_EVERY_LOADS=2, CLEANING_LIQUID_CHANGE_TIME=("const", 0.25, "h"))
    ev = _events(res)
    queued = [(t, eid, e) for t, eid, e in ev if e.endswith("_QUEUE_ENTER")]
    assert {e for _, _, e in queued} == {"PRINTER_QUEUE_ENTER", "WASHING_QUEUE_ENTER", "UV_CURING_QUEUE_ENTER"}
    start_of = {"PRINTER_QUEUE_ENTER": "VPP_BUILD_START"}
    for t, eid, e in queued:
        want = start_of.get(e, e.replace("_QUEUE_ENTER", "_START"))
        assert any(x == eid and s == want and u >= t for u, x, s in ev), (eid, e)


@pytest.mark.parametrize("kind", ["DOWN", "MAINTENANCE", "CLEANING"])     # 고장 수리 / PM / 세척액 교체
def test_machine_start_end_events_are_paired(logic_cfg, kind):
    fail = {"printer": {"mtbf": 3.0, "repair": ("const", 2, "h"), "pm_every": 20.0, "pm": ("const", 1, "h"),
                        "pm_offsets": [0.0, 10.0]},
            "washing": {"mtbf": 2.0, "repair": ("const", 1, "h"), "pm_every": 15.0, "pm": ("const", 0.5, "h"),
                        "pm_offsets": [0.0]},
            "uv_curing": {"mtbf": 2.0, "repair": ("const", 1, "h"), "pm_every": 15.0, "pm": ("const", 0.5, "h"),
                          "pm_offsets": [0.0]}}
    res = VPPSimulation(logic_cfg.replace(BREAKDOWN_ENABLED=True, EQUIPMENT_FAILURE=fail,
                                          CLEANING_LIQUID_CHANGE_EVERY_LOADS=2,
                                          CLEANING_LIQUID_CHANGE_TIME=("const", 0.25, "h"))).run()
    open_ = {}
    n = 0
    for t, eid, e in _events(res):
        if e == f"{kind}_START":
            assert not open_.get(eid), (eid, t)                          # 끝나기 전에 다시 시작하지 않음
            open_[eid] = True
            n += 1
        elif e == f"{kind}_END":
            assert open_.get(eid), (eid, t)                              # START 없이 END 없음
            open_[eid] = False
    assert n > 0
    assert not any(open_.values())


@pytest.mark.parametrize("packers", [0, 1])
def test_human_starts_are_in_work_hours(run_logic, packers):
    res = run_logic(USE_WORK_CALENDAR=True, PRINTER_UNATTENDED=True, PACKER_COUNT=packers)
    starts = [(e.sim_time, e.event) for e in res.log.events
              if e.event.endswith("_START") and role_of(e.resource) in HUMANS]
    assert starts
    off = [(t, ev) for t, ev in starts if not res.calendar.is_open(t)]
    assert not off, off[:5]


# ---------------------------------------------------------------- 이벤트 로그 컬럼 [명세서 10절]
STATES = {"Waiting", "Processing", "Moving", "Failed", "Scrapped", "Done",                 # 주문·부품·배치·로드
          "Idle", "Setup", "Running", "Down", "Maintenance"}                                # 설비 (Waiting 공통)
ALL_EVENTS = dict(INSPECTION_FAILURE_RATE=0.3, PRINT_FAILURE_RATE=0.1, REWORK_ENABLED=True, RANDOM_SEED=3,
                  CLEANING_LIQUID_CHANGE_EVERY_LOADS=2, CLEANING_LIQUID_CHANGE_TIME=("const", 0.25, "h"),
                  LOAD_HANDLING_TIME=("const", 0.2, "h"), BREAKDOWN_ENABLED=True,
                  EQUIPMENT_FAILURE={k: {"mtbf": 3.0, "repair": ("const", 1, "h"), "pm_every": 20.0,
                                         "pm": ("const", 0.5, "h"), "pm_offsets": [0.0]}
                                     for k in ("printer", "washing", "uv_curing")})


@pytest.mark.parametrize("extra", [{}, ALL_EVENTS, dict(PACKER_COUNT=1, USE_WORK_CALENDAR=True),
                                   dict(REWORK_ENABLED=False, INSPECTION_FAILURE_RATE=0.3, RANDOM_SEED=3)])
def test_every_event_has_process_location_state(run_logic, extra):
    from src.analysis.event_schema import PROCESSES
    res = run_logic(**extra)
    locations = res.cfg.LOCATIONS
    for e in res.log.events:
        assert e.process in PROCESSES, e
        assert e.location == locations[e.process], e
        assert e.state in STATES, e
        assert (e.order_id == "") == (e.entity_type == "MACHINE"), e    # 설비 이벤트만 주문 없음


def test_all_event_kinds_covered(run_logic):
    """고장·세척액·재출력·적재/인출까지 나오는 설정에서 이벤트 종류가 빠짐없이 공정에 매핑됨."""
    kinds = {e.event for e in run_logic(**ALL_EVENTS).log.events}
    for must in ("DOWN_START", "MAINTENANCE_START", "CLEANING_START", "SETUP_START", "RUNNING_START",
                 "WAITING_START", "IDLE_START", "PRINT_FAILED", "INSPECTION_FAILED", "LOADING_START",
                 "UNLOADING_END", "JOB_ASSIGNMENT_REWORK_START", "TRANSPORT_4_TO_SUPPORT_START"):
        assert must in kinds, must


def test_order_id_column(run_logic):
    res = run_logic()
    batches = {b.batch_id: b for b in res.batches}
    for e in res.log.events:
        if e.entity_type == "PART":
            assert e.entity_id.startswith(e.order_id + "-")
        elif e.entity_type == "ORDER":
            assert e.order_id == e.entity_id
        elif e.entity_type == "BATCH" and e.event != "BATCH_OPENED":
            assert set(e.order_id.split(";")) == {p.order.order_id for p in batches[e.entity_id].parts}
        elif e.entity_type == "LOAD":
            assert set(e.order_id.split(";")) <= {p.order.order_id for p in batches[e.entity_id.split("-")[0]].parts}


def test_state_and_process_examples(run_logic):
    ev = {(e.entity_id, e.event): e for e in run_logic().log.events}
    assert (ev[("O001", "ORDER_RECEIVED")].process, ev[("O001", "ORDER_RECEIVED")].state) == ("Order Reception", "Waiting")
    assert ev[("O001", "JOB_ASSIGNMENT_START")].state == "Processing"
    assert ev[("O001", "ORDER_COMPLETED")].state == "Done"
    t = next(e for e in ev.values() if e.event.startswith("TRANSPORT_") and e.event.endswith("_START"))
    assert (t.process, t.location, t.state) == ("Transport", "Corridor", "Moving")
    w = next(e for e in ev.values() if e.event == "WASHING_QUEUE_ENTER")
    assert (w.process, w.state) == ("Washing", "Waiting")


def test_event_log_csv_columns(run_logic, tmp_path):
    save_outputs(run_logic(), str(tmp_path))
    with open(tmp_path / "event_log.csv", encoding="utf-8-sig") as f:
        header = next(csv.reader(f))
    assert header == ["sim_time", "order_id", "entity_type", "entity_id", "event", "process", "location", "state",
                      "resource", "detail"]


def test_locations_must_cover_all_processes(logic_cfg):
    locs = dict(logic_cfg.LOCATIONS)
    del locs["Washing"]
    with pytest.raises(ValueError, match="Washing"):
        logic_cfg.replace(LOCATIONS=locs)


# ---------------------------------------------------------------- main.py 옵션 조합
@pytest.mark.parametrize("argv", [["--orders", "a.csv", "--mode", "random"], ["--jobs", "4"],
                                  ["--mode", "csv", "--weeks", "2"],
                                  ["--mode", "random", "--at", "Day 2 10:00"],             # 이벤트 로그 없음
                                  ["--mode", "csv", "--at", "Day 2 10:00", "--reps", "2"],
                                  ["--mode", "csv", "--at", "Day 1 08:00"],                # 시작 전
                                  ["--mode", "csv", "--at", "tomorrow"],
                                  ["--mode", "random", "--trace", "O001"],                 # 이벤트 로그 없음
                                  ["--mode", "csv", "--trace", "O001", "--reps", "2"]])
def test_main_rejects_bad_option_combinations(argv):
    r = subprocess.run([sys.executable, "main.py", *argv], capture_output=True)
    assert r.returncode == 2, r.stderr.decode(errors="replace")
