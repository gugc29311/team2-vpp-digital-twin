# -*- coding: utf-8 -*-
"""
로직 테스트 (고정 설정 LOGIC_CFG 사용 — parameters.py 값과 무관).
실행: 프로젝트 최상위 폴더에서  python -m pytest
"""
import pytest

from src.analysis.kpi import kpis, order_table, utilization
from src.model.config import SimConfig
from src.model.order_source import load_orders
from src.model.simulation import VPPSimulation

CSV = "data/sample_orders.csv"


def events_of(res, entity_id):
    return [e.event for e in res.log.events if e.entity_id == entity_id]


# ---------------------------------------------------------------- 기본 통과
def test_all_sample_orders_complete(run_logic):
    res = run_logic()
    orders = load_orders(CSV)
    assert len(res.orders) == len(orders) == 10
    assert all(o.status == "COMPLETED" for o in res.orders)
    assert sum(o.parts_good for o in res.orders) == sum(o.quantity for o in orders)


def test_each_part_goes_through_every_step_in_order(run_logic):
    res = run_logic()
    steps = ["ADDED_TO_BATCH", "SUPPORT_REMOVAL_START", "SUPPORT_REMOVAL_END", "SURFACE_TREATMENT_START",
             "SURFACE_TREATMENT_END", "INSPECTION_START", "INSPECTION_END", "PACKAGING_START",
             "PACKAGING_END", "PART_COMPLETED"]
    for o in res.orders:
        for i in range(o.quantity):
            ev = events_of(res, f"{o.order_id}-{i + 1}")
            assert [e for e in ev if e in steps] == steps, (o.order_id, ev)


# ---------------------------------------------------------------- 배치 규칙
def test_batches_respect_plate_limit_and_material(run_logic):
    res = run_logic()
    for b in res.batches:
        assert 1 <= b.n_parts <= 4
        assert len({p.material for p in b.parts}) == 1
    assert {b.trigger for b in res.batches} == {"PARTS", "TIME"}


def test_area_based_batching(run_logic):
    res = run_logic(BUILD_PLATE_MAX_PARTS=None, BUILD_PLATE_AREA_MM2=4000, BATCH_FILL_RATIO=0.7)
    for b in res.batches:
        assert b.total_area <= 4000
        if b.trigger == "AREA":
            assert b.total_area >= 0.7 * 4000
    assert all(o.status == "COMPLETED" for o in res.orders)


def test_height_mode_build_time_uses_max_height(run_logic):
    res = run_logic(VPP_BUILD_TIME_MODE="height", BUILD_SETUP_TIME=("const", 0.5, "h"),
                    LAYER_THICKNESS_MM=0.05, TIME_PER_LAYER=("const", 8, "s"))
    for b in res.batches:
        assert b.build_time == pytest.approx(0.5 + round(b.max_height / 0.05) * 8 / 3600)


# ---------------------------------------------------------------- 자원
def test_resources_never_exceed_capacity(run_logic):
    res = run_logic()
    for name, cap in res.capacities.items():
        pts = sorted([(s, 1) for r, s, e, _ in res.log.busy if r == name] +
                     [(e, -1) for r, s, e, _ in res.log.busy if r == name], key=lambda x: (x[0], x[1]))
        level = peak = 0
        for _, d in pts:
            level += d
            peak = max(peak, level)
        assert peak <= max(cap, 0), (name, peak, cap)


def test_busy_hours_match_hand_calculation(run_logic):
    """고정 시간 설정에서 자원별 점유시간 = 손계산."""
    res = run_logic()
    u = utilization(res)
    n_parts, n_batches = 11, len(res.batches)
    n_load = sum(-(-b.n_parts // 2) for b in res.batches)            # ceil(n/2), 세척·UV 동일
    assert u["vpp_printers"]["busy_h"] == pytest.approx(5 * n_batches)
    assert u["washing_machines"]["busy_h"] == pytest.approx(2 * n_load)
    assert u["uv_curing_machines"]["busy_h"] == pytest.approx(3 * n_load)
    assert u["job_assignment_workers"]["busy_h"] == pytest.approx(1 * 10 + 2 * n_batches)
    transport = 0.5 * (n_batches + 3 * n_load)                        # ① + ② + ③ + ④
    assert u["post_process_workers"]["busy_h"] == pytest.approx(transport + 1 * n_batches + 4 * n_parts)
    assert u["quality_inspectors"]["busy_h"] == pytest.approx(0.5 * n_batches + 2 * n_parts)


def test_worker_count_changes_result(run_logic):
    lt = lambda r: sum(x["lead_time_h"] for x in order_table(r))
    assert lt(run_logic(POST_PROCESS_WORKER_COUNT=4)) < lt(run_logic(POST_PROCESS_WORKER_COUNT=1))


def test_separate_packer(run_logic):
    res = run_logic(PACKER_COUNT=1)
    assert all(o.status == "COMPLETED" for o in res.orders)
    assert "packers" in utilization(res)


@pytest.mark.parametrize("rule", ["FCFS", "SPT", "EDD"])
def test_scheduling_rules_run(run_logic, rule):
    assert all(o.status == "COMPLETED" for o in run_logic(DEFAULT_SCHEDULING_RULE=rule).orders)


# ---------------------------------------------------------------- 불량 / 재출력
def test_rework_completes_all_orders(run_logic):
    res = run_logic(INSPECTION_FAILURE_RATE=0.3, PRINT_FAILURE_RATE=0.1, REWORK_ENABLED=True, RANDOM_SEED=3)
    assert all(o.status == "COMPLETED" for o in res.orders)
    assert any("-R" in p.part_id for b in res.batches for p in b.parts)


@pytest.mark.parametrize("same", [True, False])
def test_rework_geometry_option(run_logic, same):
    res = run_logic(INSPECTION_FAILURE_RATE=0.4, REWORK_ENABLED=True, REWORK_SAME_GEOMETRY=same, RANDOM_SEED=5)
    reprints = [p for b in res.batches for p in b.parts if p.gen > 0]
    assert reprints
    identical = [(p.area_mm2, p.height_mm) == (p.order.area_mm2, p.order.height_mm) for p in reprints]
    assert all(identical) if same else not all(identical)


def test_rework_ids_with_R_in_order_id(logic_cfg):
    """주문 번호에 '-R' 이 들어가도 재출력 부품 번호가 깨지지 않음 (반복 재출력 포함)."""
    from src.entities.order import Order
    orders = [Order(f"ORD-R{i:02d}", "P1", float(i), 1, "Resin_A", 999.0, "NORMAL", 900.0, 40.0) for i in range(6)]
    res = VPPSimulation(logic_cfg.replace(INSPECTION_FAILURE_RATE=0.6, REWORK_ENABLED=True, RANDOM_SEED=11),
                        orders=orders).run()
    assert all(o.status == "COMPLETED" for o in res.orders)
    reprints = [p for b in res.batches for p in b.parts if p.gen > 0]
    assert any(p.gen >= 2 for p in reprints)                               # 재출력의 재출력까지 발생
    for p in reprints:
        assert p.part_id == f"{p.order.order_id}-1-R{p.gen}"
    ids = [p.part_id for b in res.batches for p in b.parts]
    assert len(ids) == len(set(ids))                                        # 부품 번호 중복 없음


def test_no_rework_scraps_parts(run_logic):
    res = run_logic(INSPECTION_FAILURE_RATE=0.3, REWORK_ENABLED=False, RANDOM_SEED=3)
    assert all(o.is_done for o in res.orders)
    assert any(o.status == "SHORT" for o in res.orders)


# ---------------------------------------------------------------- 고장·PM / 세척액
def _breakdown_cfg(logic_cfg, **kw):
    fail = {"printer": {"mtbf": 3.0, "repair": ("const", 2, "h"), "pm_every": 20.0, "pm": ("const", 1, "h"),
                        "pm_offsets": [0.0, 10.0]},
            "washing": {"mtbf": 2.0, "repair": ("const", 1, "h"), "pm_every": 15.0, "pm": ("const", 0.5, "h"),
                        "pm_offsets": [0.0]},
            "uv_curing": {"mtbf": 2.0, "repair": ("const", 1, "h"), "pm_every": 15.0, "pm": ("const", 0.5, "h"),
                          "pm_offsets": [0.0]}}
    return logic_cfg.replace(BREAKDOWN_ENABLED=True, EQUIPMENT_FAILURE=fail, **kw)


def test_breakdown_units_not_used_while_down(logic_cfg):
    res = VPPSimulation(_breakdown_cfg(logic_cfg)).run()
    assert all(o.status == "COMPLETED" for o in res.orders)
    for prefix in ("P", "WASH", "UV"):                                      # 설비 종류마다 고장 실제 발생
        assert any(k == "fail" for u in res.units if u.name.startswith(prefix) for k, _, _ in u.log), prefix
    # 다운 구간 동안 그 설비로 시작된 작업이 없어야 함
    starts = [(e.sim_time, e.resource) for e in res.log.events
              if e.event in ("VPP_BUILD_START", "WASHING_START", "UV_CURING_START")]
    for u in res.units:
        for _, s, e in u.log:
            assert not any(name == u.name and s <= t < e for t, name in starts), u.name


def test_breakdown_is_non_preemptive(logic_cfg):
    """출력 도중 고장으로 중단되지 않음: 모든 배치의 출력시간 = build_time."""
    res = VPPSimulation(_breakdown_cfg(logic_cfg)).run()
    for b in res.batches:
        assert b.print_end - b.print_start == pytest.approx(b.build_time)


def test_cleaning_liquid_change(run_logic):
    res = run_logic(CLEANING_LIQUID_CHANGE_EVERY_LOADS=2, CLEANING_LIQUID_CHANGE_TIME=("const", 0.25, "h"))
    wash = [u for u in res.units if u.name.startswith("WASH")][0]
    changes = [x for x in wash.log if x[0] == "clean"]
    assert len(changes) == wash.loads // 2
    assert all(e - s == pytest.approx(0.25) for _, s, e in changes)


# ---------------------------------------------------------------- 캘린더 · 납기
def test_work_calendar_mode(run_logic):
    res = run_logic(USE_WORK_CALENDAR=True, PRINTER_UNATTENDED=True)
    assert all(o.status == "COMPLETED" for o in res.orders)
    cal = res.calendar
    humans = ("job_assignment_workers", "post_process_workers", "quality_inspectors")
    for r, s, e, hours in res.log.busy:
        if r in humans:
            assert cal.work_hours(s, e) == pytest.approx(hours, abs=1e-6), (r, s, e, hours)
    for b in res.batches:
        assert cal.is_open(b.print_start)                               # 적재는 근무시간
        assert b.print_end - b.print_start == pytest.approx(b.build_time)   # 출력은 연속(무인)
    assert any(cal.work_hours(b.print_start, b.print_end) < b.build_time - 1e-6 for b in res.batches)


def test_work_calendar_attended_printer(run_logic):
    res = run_logic(USE_WORK_CALENDAR=True, PRINTER_UNATTENDED=False)
    for b in res.batches:
        assert res.calendar.work_hours(b.print_start, b.print_end) == pytest.approx(b.build_time, abs=1e-6)


def test_random_due_dates_are_work_days(logic_cfg):
    cfg = logic_cfg.replace(ORDER_SOURCE="random", USE_WORK_CALENDAR=True, SIMULATION_TIME=400,
                            URGENT_PROBABILITY=0.5, DUE_DATE_SLACK_NORMAL=("const", 24, "h"),
                            DUE_DATE_SLACK_URGENT=("const", 8, "h"), ORDER_ARRIVAL_RATE_PER_WEEK=40)
    res = VPPSimulation(cfg).run()
    cal = res.calendar
    for o in res.orders:
        slack = 8 if o.is_urgent else 24
        assert cal.work_hours(o.arrival_time, o.due_date) == pytest.approx(slack, abs=1e-6)
    # 달력 기준으로는 밤·주말을 넘기므로 slack 보다 길어지는 주문이 있어야 함
    assert any(o.due_date - o.arrival_time > (8 if o.is_urgent else 24) + 1 for o in res.orders)


# ---------------------------------------------------------------- 재현성 / 모드
def test_same_seed_same_result(run_logic):
    kw = dict(JOB_ASSIGNMENT_TIME=("tri", 2, 5, 12, "min"), SUPPORT_REMOVAL_TIME=("tri", 30, 60, 150, "s"))
    assert run_logic(RANDOM_SEED=1, **kw).log.events == run_logic(RANDOM_SEED=1, **kw).log.events
    assert run_logic(RANDOM_SEED=1, **kw).log.events != run_logic(RANDOM_SEED=2, **kw).log.events


def test_random_mode_runs(logic_cfg):
    cfg = logic_cfg.replace(ORDER_SOURCE="random", SIMULATION_TIME=200, ORDER_ARRIVAL_RATE_PER_WEEK=60)
    res = VPPSimulation(cfg).run()
    assert len(res.orders) > 100
    assert res.end_time == pytest.approx(200)
    k = kpis(res)
    assert k["orders_per_week"] == pytest.approx(60, rel=0.25)


def test_resin_tracking(run_logic):
    res = run_logic(RESIN_TRACKING=True, RESIN_FILL_RATIO=("const", 0.3), RESIN_SUPPORT_RATIO=0.15)
    for t, v, _ in res.resin_log:
        assert v > 0
    expected = sum(o.area_mm2 * o.height_mm * 0.3 * 1.15 * o.quantity for o in res.orders)
    assert sum(v for _, v, _ in res.resin_log) == pytest.approx(expected)


# ---------------------------------------------------------------- 입력 검증
def test_excel_bom_csv_is_readable(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_text(open(CSV, encoding="utf-8").read(), encoding="utf-8-sig")
    assert len(load_orders(str(p))) == 10


def test_bad_csv_rows_are_reported(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("order_id,product_id,arrival_time,quantity,material,due_date,priority\n"
                 "O1,P1,0,1,Resin_A,10,Nomal\n", encoding="utf-8")
    with pytest.raises(ValueError, match="2행"):
        load_orders(str(p))


def test_config_typo_and_bad_values_are_rejected():
    cfg = SimConfig.from_parameters()
    with pytest.raises(KeyError):
        cfg.replace(POST_PROCES_WORKER_COUNT=3)
    with pytest.raises(ValueError, match="JOB_ASSIGNMENT_TIME"):
        cfg.replace(JOB_ASSIGNMENT_TIME=("tri", 5, 2, 12, "min"))
    with pytest.raises(ValueError, match="WASHING_TIME"):
        cfg.replace(WASHING_TIME=("const", 2, "hour"))
    with pytest.raises(ValueError, match="repair"):
        cfg.replace(EQUIPMENT_FAILURE={**cfg.EQUIPMENT_FAILURE,
                                       "printer": {**cfg.EQUIPMENT_FAILURE["printer"], "repair": ("tri", 8, 4, 2, "h")}})
    with pytest.raises(ValueError, match="SCENARIO"):
        cfg.replace(SCENARIO="Rush")


@pytest.mark.parametrize("key", ["ORDER_HEIGHT_MM", "ORDER_AREA_MM2", "DUE_DATE_SLACK_URGENT"])
def test_random_mode_requires_order_distributions(key):
    cfg = SimConfig.from_parameters()
    cfg.replace(**{key: None})                                   # csv 모드에서는 필요 없으므로 허용
    with pytest.raises(ValueError, match=key):
        cfg.replace(ORDER_SOURCE="random", **{key: None})        # random 모드에서는 실행 전에 이름과 함께 오류


def test_resample_rework_requires_geometry_distributions():
    cfg = SimConfig.from_parameters()
    cfg.replace(ORDER_HEIGHT_MM=None, REWORK_SAME_GEOMETRY=True)
    with pytest.raises(ValueError, match="ORDER_HEIGHT_MM"):
        cfg.replace(ORDER_HEIGHT_MM=None, REWORK_ENABLED=True, REWORK_SAME_GEOMETRY=False)
