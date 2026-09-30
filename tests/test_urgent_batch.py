# -*- coding: utf-8 -*-
"""
긴급 전용 배치 (URGENT_BATCH_MAX_WAIT_TIME) 테스트 — 고정 설정 LOGIC_CFG.
  None(기본) = 긴급·일반 혼합 (기존 동작) / 분포 = 긴급 부품끼리 별도 배치 + 짧은 대기 한도
"""
import pytest

from src.model.config import SimConfig
from src.validation.invariants import check

URGENT_1H = dict(URGENT_BATCH_MAX_WAIT_TIME=("const", 1, "h"))


def _is_urgent(p):
    return p.order.is_urgent


def test_default_is_off_and_keeps_mixing(run_logic):
    assert SimConfig.from_parameters().URGENT_BATCH_MAX_WAIT_TIME is None
    res = run_logic()
    assert not any(b.urgent for b in res.batches)
    assert any(len({_is_urgent(p) for p in b.parts}) == 2 for b in res.batches)   # 샘플에서 혼합 배치 발생


def test_urgent_parts_are_never_mixed_with_normal(run_logic):
    res = run_logic(**URGENT_1H)
    assert any(b.urgent for b in res.batches)
    for b in res.batches:
        assert all(_is_urgent(p) == b.urgent for p in b.parts), b.batch_id
    assert all(o.status == "COMPLETED" for o in res.orders)
    assert check(res) == []


def test_urgent_batch_uses_its_own_wait_limit(run_logic):
    """긴급 배치는 1h 안에 확정 (가득 차면 더 일찍), 일반 배치는 기존 24h 한도."""
    res = run_logic(**URGENT_1H)
    for b in res.batches:
        limit = 1.0 if b.urgent else 24.0
        assert b.closed_time - b.created_time <= limit + 1e-9, (b.batch_id, b.trigger)
    assert any(b.urgent and b.trigger == "TIME" and b.closed_time - b.created_time == pytest.approx(1.0)
               for b in res.batches)


def test_urgent_orders_finish_sooner(run_logic):
    lead = lambda r: {o.order_id: o.completed_time - o.arrival_time for o in r.orders if o.is_urgent}
    off, on = lead(run_logic()), lead(run_logic(**URGENT_1H))
    assert sum(on.values()) < sum(off.values())


def test_rework_of_urgent_part_stays_urgent(run_logic):
    res = run_logic(INSPECTION_FAILURE_RATE=0.4, REWORK_ENABLED=True, RANDOM_SEED=5, **URGENT_1H)
    reprints = [(p, b) for b in res.batches for p in b.parts if p.gen > 0]
    assert reprints
    assert all(b.urgent == _is_urgent(p) for p, b in reprints)
    assert check(res) == []
