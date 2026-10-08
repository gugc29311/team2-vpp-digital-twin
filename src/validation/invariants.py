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
  7. 작업자: 한 사람의 작업 구간이 겹치지 않음 (한 명이 동시에 두 작업 X) [명세서 14절 L3]
  8. 공정 순서(keep_events 일 때): 출력 종료 ≤ 탈거 시작, 탈거 종료 ≤ 세척 시작, 세척 종료 ≤ UV 시작,
     UV 종료 ≤ 서포트 제거 시작 ≤ ... ≤ 검사 ≤ 포장 (작업 완료 전에 후공정 시작 X) [명세서 14절 L3]
  9. 이동(keep_events 일 때): 앞 공정 종료 ≤ 이동 출발, 이동 도착 ≤ 다음 공정·대기열 진입
     (이동하기 전에 다음 위치 도착 X) [명세서 14절 L3]

사용
  from src.validation.invariants import check
  assert not check(VPPSimulation(cfg).run())
"""
from collections import Counter, defaultdict

from src.resources.resources import role_of

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
    starts = [(e.sim_time, e.resource) for e in res.log.events if e.event in MACHINE_STARTS]
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
    times = [e.sim_time for e in res.log.events]
    if any(a > b + EPS for a, b in zip(times, times[1:])):
        out.append("이벤트 시각이 줄어드는 구간 있음")
    for e in res.log.events:
        if e.event.endswith("_START") and role_of(e.resource) in HUMANS and not cal.is_open(e.sim_time):
            out.append(f"{e.entity_id}: {e.event} 가 근무시간 밖 ({e.sim_time:.4f})")
    return out


def check_workers(res):
    """작업자 개인 구간: 겹침 없음, ID 가 그 역할의 명단에 있음 (person_busy — 이벤트 로그 없이도 동작)."""
    by = defaultdict(list)
    for w, role, s, e, _ in res.log.person_busy:
        by[w].append((s, e, role))
    out = []
    for w, iv in by.items():
        roles = {r for _, _, r in iv}
        if roles != {role_of(w)} or w not in res.workers.get(role_of(w), []):
            out.append(f"{w}: 역할·명단 불일치 {sorted(roles)}")
        iv.sort()
        for (s1, e1, _), (s2, e2, _) in zip(iv, iv[1:]):
            if s2 < e1 - EPS:
                out.append(f"{w}: 작업 겹침 [{s1:.4f}, {e1:.4f}] / [{s2:.4f}, {e2:.4f}]")
    return out


_PART_CHAIN = ("SUPPORT_REMOVAL", "SURFACE_TREATMENT", "INSPECTION", "PACKAGING")


def check_process_order(res):
    """배치·로드·부품의 공정 순서 (이벤트 로그 필요). 아직 일어나지 않은 단계는 건너뜀."""
    if not res.log.keep_events:
        return []
    part_batch = {p.part_id: b.batch_id for b in res.batches for p in b.parts}
    batch_ev = defaultdict(lambda: defaultdict(list))            # 배치 -> 이벤트 -> [시각] (로드 포함)
    part_ev = defaultdict(dict)                                  # 부품 -> 이벤트 -> 첫 시각
    for e in res.log.events:
        if e.entity_type == "BATCH":
            batch_ev[e.entity_id][e.event].append(e.sim_time)
        elif e.entity_type == "LOAD":
            batch_ev[e.entity_id.split("-")[0]][e.event].append(e.sim_time)
        elif e.entity_type == "PART":
            part_ev[e.entity_id].setdefault(e.event, e.sim_time)
    out = []

    def before(label, a, b):
        if a is not None and b is not None and a > b + EPS:
            out.append(f"{label}: {a:.4f} > {b:.4f}")

    last = lambda ev, k: max(ev[k]) if ev.get(k) else None
    first = lambda ev, k: min(ev[k]) if ev.get(k) else None
    for bid, ev in batch_ev.items():
        before(f"{bid} 출력 종료 -> 탈거 시작", last(ev, "VPP_BUILD_END"), first(ev, "PART_REMOVAL_START"))
        before(f"{bid} 탈거 종료 -> 세척 시작", last(ev, "PART_REMOVAL_END"), first(ev, "WASHING_START"))
        before(f"{bid} 세척 종료 -> UV 시작", last(ev, "WASHING_END"), first(ev, "UV_CURING_START"))
    for pid, ev in part_ev.items():
        bid = part_batch.get(pid)
        uv_end = last(batch_ev[bid], "UV_CURING_END") if bid in batch_ev else None
        before(f"{pid} UV 종료 -> 서포트 제거 시작", uv_end, ev.get("SUPPORT_REMOVAL_START"))
        for a, b in zip(_PART_CHAIN, _PART_CHAIN[1:]):
            before(f"{pid} {a} 종료 -> {b} 시작", ev.get(a + "_END"), ev.get(b + "_START"))
    return out


def check_transport(res):
    """
    이동 순서 (이벤트 로그 필요) [명세서 14절 L3 '이동하기 전에 다음 위치 도착'].
      - 앞 공정이 끝나기 전에 이동을 출발하지 않음
      - 이동이 끝나기(도착) 전에 다음 공정·대기열에 들어가지 않음
    ① 출력 -> 탈거(배치) ② 탈거 -> 세척(세척 로드) ③ 세척 -> UV(UV 로드) ④ UV -> 서포트(UV 로드) ⑤ 표면처리 -> 검사(배치)
    ⑥ 검사 -> 포장(배치의 합격품)
    """
    if not res.log.keep_events:
        return []
    ent = defaultdict(lambda: defaultdict(list))                 # 대상 ID -> 이벤트 -> [시각]
    loads = defaultdict(set)                                      # 배치 -> 로드 ID
    for e in res.log.events:
        ent[e.entity_id][e.event].append(e.sim_time)
        if e.entity_type == "LOAD":
            loads[e.entity_id.split("-")[0]].add(e.entity_id)
    out = []

    def before(label, a, b):
        if a is not None and b is not None and a > b + EPS:
            out.append(f"{label}: {a:.4f} > {b:.4f}")

    def times(ids, event, pick):
        vals = [t for i in ids for t in ent[i].get(event, [])]
        return pick(vals) if vals else None

    for b in res.batches:
        bid = b.batch_id
        wash = sorted(i for i in loads[bid] if "-W" in i)
        uv = sorted(i for i in loads[bid] if "-U" in i)
        parts = [p.part_id for p in b.parts]
        before(f"{bid} 출력 종료 -> 이동① 출발", times([bid], "VPP_BUILD_END", max),
               times([bid], "TRANSPORT_1_PRINT_TO_REMOVAL_START", min))
        before(f"{bid} 이동① 도착 -> 탈거 시작", times([bid], "TRANSPORT_1_PRINT_TO_REMOVAL_END", max),
               times([bid], "PART_REMOVAL_START", min))
        for w in wash:
            before(f"{w} 탈거 종료 -> 이동② 출발", times([bid], "PART_REMOVAL_END", max),
                   times([w], "TRANSPORT_2_TO_WASHING_START", min))
            before(f"{w} 이동② 도착 -> 세척 대기열", times([w], "TRANSPORT_2_TO_WASHING_END", max),
                   times([w], "WASHING_QUEUE_ENTER", min))
        for u in uv:
            before(f"{u} 세척 종료 -> 이동③ 출발", times(wash, "WASHING_END", max),
                   times([u], "TRANSPORT_3_TO_UV_START", min))
            before(f"{u} 이동③ 도착 -> UV 대기열", times([u], "TRANSPORT_3_TO_UV_END", max),
                   times([u], "UV_CURING_QUEUE_ENTER", min))
            before(f"{u} UV 종료 -> 이동④ 출발", times(uv, "UV_CURING_END", max),
                   times([u], "TRANSPORT_4_TO_SUPPORT_START", min))
        before(f"{bid} 이동④ 도착 -> 서포트 제거 시작", times(uv, "TRANSPORT_4_TO_SUPPORT_END", max),
               times(parts, "SUPPORT_REMOVAL_START", min))
        before(f"{bid} 표면처리 종료 -> 이동⑤ 출발", times(parts, "SURFACE_TREATMENT_END", max),
               times([bid], "TRANSPORT_5_TO_INSPECTION_START", min))
        before(f"{bid} 이동⑤ 도착 -> 검사 시작", times([bid], "TRANSPORT_5_TO_INSPECTION_END", max),
               times(parts, "INSPECTION_START", min))
        before(f"{bid} 검사 종료 -> 이동⑥ 출발", times(parts, "INSPECTION_END", max),
               times([bid], "TRANSPORT_6_TO_PACKING_START", min))
        before(f"{bid} 이동⑥ 도착 -> 포장 시작", times([bid], "TRANSPORT_6_TO_PACKING_END", max),
               times(parts, "PACKAGING_START", min))
    return out


CHECKS = (check_resources, check_batches, check_orders, check_downtime, check_events, check_workers,
          check_process_order, check_transport)


def check(res):
    """모든 불변식 검사 -> 위반 메시지 목록."""
    return [msg for fn in CHECKS for msg in fn(res)]
