# -*- coding: utf-8 -*-
"""
주문 모델 [명세서 4절]: Required Process · Estimated Build Time · Current State.

손계산 기준: tests/conftest.py 고정 설정 (서포트 제거 2h, 표면처리 2h, 후공정 2명).
"""
import csv

import pytest

from src.analysis.kpi import kpis, order_table
from src.entities.order import (MANDATORY_POST_PROCESSES, POST_PROCESSES, Order, parse_required_process)
from src.logger.state import state_at
from src.model.order_source import estimate_build_time, load_orders
from src.model.simulation import VPPSimulation
from src.validation.invariants import check

NO_FINISHING = "Part Removal;Washing;UV Curing;Inspection;Packaging"     # 서포트 제거·표면처리 생략


def _order(**kw):
    base = dict(order_id="X1", product_id="P", arrival_time=0, quantity=1, material="Resin_A", due_date=10)
    base.update(kw)
    return Order(**base)


def _csv_with_required(tmp_path, required):
    """sample_orders.csv 에 required_process 열 추가 ({order_id: 값})."""
    with open("data/sample_orders.csv", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["required_process"] = required.get(r["order_id"], "")
    path = tmp_path / "orders.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return str(path)


def _post_busy(res):
    return sum(h for r, _, _, h in res.log.busy if r == "post_process_workers")


# ---------------------------------------------------------------- Required Process
def test_required_process_default_is_full_route():
    assert _order().required_process == POST_PROCESSES
    assert _order(required_process="ALL").required_process == POST_PROCESSES


def test_required_process_parsing_keeps_route_order():
    o = _order(required_process="Packaging; Inspection;UV Curing;Washing;Part Removal")
    assert o.required_process == MANDATORY_POST_PROCESSES
    assert not o.needs("Surface Treatment") and o.needs("Washing")


@pytest.mark.parametrize("bad", ["Part Removal;Washing;UV Curing;Inspection",           # 포장 누락
                                 NO_FINISHING + ";Polishing"])                          # 없는 공정
def test_required_process_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        parse_required_process(bad, "X1")


def test_skipped_post_processes_are_not_run(logic_cfg, tmp_path):
    """O001 만 서포트 제거·표면처리 생략 -> 그 부품 이벤트 없음, 후공정 점유 = 기준 - (2h + 2h)."""
    base = VPPSimulation(logic_cfg).run()
    orders = load_orders(_csv_with_required(tmp_path, {"O001": NO_FINISHING}))
    res = VPPSimulation(logic_cfg, orders=orders).run()

    evs = {e.event for e in res.log.events if e.entity_id == "O001-1"}
    assert "SUPPORT_REMOVAL_START" not in evs and "SURFACE_TREATMENT_START" not in evs
    assert {"INSPECTION_START", "PACKAGING_START", "PART_COMPLETED"} <= evs
    other = {e.event for e in res.log.events if e.entity_id == "O002-1"}
    assert {"SUPPORT_REMOVAL_START", "SURFACE_TREATMENT_START"} <= other
    assert _post_busy(base) - _post_busy(res) == pytest.approx(4.0)
    assert all(o.completed_time is not None for o in res.orders)
    assert not check(res)


def test_required_process_in_order_table(logic_cfg, tmp_path):
    orders = load_orders(_csv_with_required(tmp_path, {"O002": NO_FINISHING}))
    rows = {r["order_id"]: r for r in order_table(VPPSimulation(logic_cfg, orders=orders).run())}
    assert rows["O002"]["required_process"] == ";".join(MANDATORY_POST_PROCESSES)
    assert rows["O001"]["required_process"] == ";".join(POST_PROCESSES)


# ---------------------------------------------------------------- Estimated Build Time
def test_estimated_build_time_height_mode(logic_cfg):
    """높이 40mm, 층 0.05mm -> 800층 x 8초 = 1.7778h + 셋업 0.5h = 2.2778h."""
    cfg = logic_cfg.replace(VPP_BUILD_TIME_MODE="height", BUILD_SETUP_TIME=("const", 0.5, "h"),
                            LAYER_THICKNESS_MM=0.05, TIME_PER_LAYER=("const", 8, "s"))
    assert estimate_build_time(40, cfg) == pytest.approx(0.5 + 800 * 8 / 3600)
    assert estimate_build_time(None, cfg) is None


def test_estimated_build_time_fixed_mode_and_set_on_receipt(logic_cfg):
    assert estimate_build_time(40, logic_cfg) == pytest.approx(5.0)          # fixed 5h
    res = VPPSimulation(logic_cfg).run()
    assert all(o.estimated_build_time == pytest.approx(5.0) for o in res.orders)


def test_estimated_build_time_not_above_actual_batch_time():
    """실제 출력시간 = 배치 최대 높이 기준 -> 부품 단독 예상 이상 (가정값, 2주)."""
    from src.model.config import SimConfig
    cfg = SimConfig.from_parameters().replace(ORDER_SOURCE="random", WARMUP_TIME=0, SIMULATION_TIME=2 * 168)
    res = VPPSimulation(cfg, keep_events=False).run()
    for b in res.batches:
        if b.build_time is not None:
            assert max(p.order.estimated_build_time for p in b.parts) == pytest.approx(b.build_time)


# ---------------------------------------------------------------- Current State
def test_current_state_matches_time_query(logic_cfg):
    """주문 객체의 current_process/state = 이벤트 로그로 재구성한 종료 시각의 주문 위치."""
    res = VPPSimulation(logic_cfg).run()
    st = state_at(res, res.end_time)
    for o in res.orders:
        assert (o.current_process, o.current_state) == (st["orders"][o.order_id]["process"],
                                                        st["orders"][o.order_id]["state"])
        assert (o.current_process, o.current_state) == ("Packaging", "Done")


def test_current_state_mid_run_and_without_event_log(logic_cfg):
    """중간 종료(t=12)에도 로그 재구성과 같고, 이벤트 로그를 보관하지 않아도 같은 상태."""
    cfg = logic_cfg.replace(SIMULATION_TIME=12)
    res = VPPSimulation(cfg).run()
    st = state_at(res, res.end_time)
    for o in res.orders:
        if o.order_id in st["orders"]:
            assert (o.current_process, o.current_state) == (st["orders"][o.order_id]["process"],
                                                            st["orders"][o.order_id]["state"])
    quiet = VPPSimulation(cfg, keep_events=False).run()
    assert [(o.order_id, o.current_process, o.current_state) for o in quiet.orders] == \
           [(o.order_id, o.current_process, o.current_state) for o in res.orders]


def test_default_route_keeps_kpis(logic_cfg, tmp_path):
    """required_process 를 전부 ALL 로 적은 CSV = 열이 없는 CSV (결과 동일)."""
    orders = load_orders(_csv_with_required(tmp_path, {}))
    assert kpis(VPPSimulation(logic_cfg, orders=orders).run()) == pytest.approx(
        kpis(VPPSimulation(logic_cfg).run()), nan_ok=True)
