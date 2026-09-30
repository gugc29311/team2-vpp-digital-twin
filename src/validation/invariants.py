# -*- coding: utf-8 -*-
"""
불변식 검사: 어떤 설정·시드로 돌려도 항상 참이어야 하는 규칙.

  check(res) -> 위반 메시지 목록 (빈 목록 = 정상)

KPI 값이 '그럴듯한지'가 아니라 모델이 '물리적으로 말이 되는지'를 본다.
  1. 자원: 동시 점유 수 ≤ 용량, 사람 작업은 근무시간만 소비 (EventLog.busy 4번째 값)
  2. 배치: 부품 수·면적 한도, 같은 재료, 시각 순서 (생성 ≤ 확정 ≤ 출력 시작 ≤ 종료), 출력시간 = build_time
  3. 주문: 합격 + 폐기 ≤ 수량, 완료 주문은 합격 + 폐기 = 수량, 완료 시각 ≥ 도착 시각
  4. 부품: 한 부품은 한 번만 출력
  5. 설비: 고장·PM·세척액 교체 중에는 그 설비로 작업을 시작하지 않음
  6. 이벤트(keep_events 일 때): 시각이 줄지 않음, 사람 작업 *_START 는 근무시간

사용
  from src.validation.invariants import check
  assert not check(VPPSimulation(cfg).run())
"""
from collections import Counter

EPS = 1e-6
HUMANS = ("job_assignment_workers", "post_process_workers", "quality_inspectors", "packers")
MACHINE_STARTS = ("VPP_BUILD_START", "WASHING_START", "UV_CURING_START")


def _peak(intervals):
    pts = sorted([(s, 1) for s, e in intervals] + [(e, -1) for s, e in intervals])   # 같은 시각이면 반납 먼저
    level = peak = 0
    for _, d in pts:
        level += d
        peak = max(peak, level)
    return peak


def check_resources(res):
    out, cal = [], res.calendar
    by = {}
    for r, s, e, hours in res.log.busy:
        by.setdefault(r, []).append((s, e))
        if e < s - EPS:
            out.append(f"{r}: 점유 종료 < 시작 ({s:.4f} > {e:.4f})")
        if r in HUMANS and abs(cal.work_hours(s, e) - hours) > EPS:
            out.append(f"{r}: 근무시간 불일치 [{s:.4f}, {e:.4f}] 기록 {hours:.4f}h")
    for r, iv in by.items():
        cap = res.capacities.get(r, 0)
        if _peak(iv) > cap:
            out.append(f"{r}: 동시 점유 {_peak(iv)} > 용량 {cap}")
    return out


def check_batches(res):
    out, c = [], res.cfg
    unattended = not c.USE_WORK_CALENDAR or c.PRINTER_UNATTENDED
    for b in res.batches:
        tag = b.batch_id
        if c.BUILD_PLATE_MAX_PARTS is not None and b.n_parts > c.BUILD_PLATE_MAX_PARTS:
            out.append(f"{tag}: 부품 {b.n_parts} > 한도 {c.BUILD_PLATE_MAX_PARTS}")
        if c.BUILD_PLATE_AREA_MM2 is not None and b.total_area > c.BUILD_PLATE_AREA_MM2 + EPS:
            out.append(f"{tag}: 면적 {b.total_area:.0f} > 플레이트 {c.BUILD_PLATE_AREA_MM2}")
        if c.BATCH_SAME_MATERIAL_ONLY and len({p.material for p in b.parts}) > 1:
            out.append(f"{tag}: 재료 혼합 {sorted({p.material for p in b.parts})}")
        times = [t for t in (b.created_time, b.closed_time, b.print_start, b.print_end) if t is not None]
        if any(a > b_ + EPS for a, b_ in zip(times, times[1:])):
            out.append(f"{tag}: 시각 순서 오류 {times}")
        if b.print_end is not None and unattended and abs(b.print_end - b.print_start - b.build_time) > EPS:
            out.append(f"{tag}: 출력시간 {b.print_end - b.print_start:.4f} != build_time {b.build_time:.4f}")
    return out


def check_orders(res):
    out = []
    for o in res.orders:
        if o.parts_good + o.parts_scrapped > o.quantity:
            out.append(f"{o.order_id}: 합격 {o.parts_good} + 폐기 {o.parts_scrapped} > 수량 {o.quantity}")
        if o.completed_time is not None:
            if not o.is_done:
                out.append(f"{o.order_id}: 완료 시각이 있는데 부품이 남음")
            if o.completed_time < o.arrival_time - EPS:
                out.append(f"{o.order_id}: 완료 {o.completed_time:.4f} < 도착 {o.arrival_time:.4f}")
        elif o.is_done:
            out.append(f"{o.order_id}: 부품은 끝났는데 완료 시각 없음")
    printed = Counter(p.part_id for b in res.batches for p in b.parts)
    out += [f"{pid}: {n}번 배치에 들어감" for pid, n in printed.items() if n > 1]
    return out


def check_downtime(res):
    """설비가 고장·PM·세척액 교체 중일 때 그 설비로 작업이 시작되지 않음 (이벤트 로그 필요)."""
    if not res.log.keep_events:
        return []
    starts = [(e[0], e[4]) for e in res.log.events if e[3] in MACHINE_STARTS]
    out = []
    for u in res.units:
        for kind, s, e in u.log:
            for t, name in starts:
                if name == u.name and s + EPS < t < e - EPS:
                    out.append(f"{u.name}: {kind} [{s:.4f}, {e:.4f}] 중 {t:.4f} 에 작업 시작")
    return out


def check_events(res):
    if not res.log.keep_events:
        return []
    out, cal = [], res.calendar
    times = [e[0] for e in res.log.events]
    if any(a > b + EPS for a, b in zip(times, times[1:])):
        out.append("이벤트 시각이 줄어드는 구간 있음")
    for t, _, eid, ev, r, _ in res.log.events:
        if ev.endswith("_START") and r in HUMANS and not cal.is_open(t):
            out.append(f"{eid}: {ev} 가 근무시간 밖 ({t:.4f})")
    return out


CHECKS = (check_resources, check_batches, check_orders, check_downtime, check_events)


def check(res):
    """모든 불변식 검사 -> 위반 메시지 목록."""
    return [msg for fn in CHECKS for msg in fn(res)]
