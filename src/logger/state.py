# -*- coding: utf-8 -*-
"""
OME Time Query: 이벤트 로그로 시각 t 의 공장 상태를 재구성 [명세서 1절 질문 목록 · 18절 OME Time Query].

  state_at(res, t)  -> dict
      orders        : 주문별 현재 위치 {order_id: {process, state, event, entity_id}} (도착한 주문만)
      wip / completed : 시스템 안 주문 수 / t 까지 완료된 주문 수
      late_orders   : t 기준 납기가 지났는데 미완료인 주문
      machines      : 설비별 장비 상태 6종 (Idle/Setup/Running/Waiting/Down/Maintenance)
      workers       : 작업자별 수행 중인 작업 {JA1: "JOB_ASSIGNMENT O003" | None}
      queues        : 대기 수 {printer, washing, uv, job_assignment_workers, post_process_workers, ...}
      resin_L       : t 까지 레진 누적 소모량 [L]
  order_trace(res, "O001") -> 주문 1건의 전체 이벤트 (Level 2 Event Trace)
  parse_time("Day 3 14:25") -> t,  format_time(t) -> "Day 3 14:25"
      Day 1 = 월요일, t=0 = Day 1 09:00 (근무 시작). 시각은 달력 기준.

재구성 규칙
  - 주문 위치 = order_id 열에 그 주문이 들어간 t 이전 마지막 이벤트 (부품이 여러 개면 가장 최근에 움직인 부품·배치)
  - 설비 상태 = t 이전 마지막 MACHINE 이벤트의 state. 단 근무시간 외에 멈추는 처리(세척·UV, 무인운전이 아닌 프린터)가
    Running·Setup 인데 t 가 근무시간 밖이면 Waiting (이벤트 없이 멈추는 구간 — GUIDE '장비 상태 6종')
  - 작업자 = 개인 ID 가 resource 인 *_START 이후 *_END 전
  - 설비 대기 = *_QUEUE_ENTER 이후 시작 전. 인력 대기 = 요청 시각(시작 - detail 의 wait) 이후 시작 전
    (wait 에는 작업자 대기와 근무시간 대기가 함께 들어 있음 — 밤에 요청된 작업도 '대기' 로 셈)
  - 레진은 이벤트가 아닌 res.resin_log (출력 시작 시각별 소모량) 사용
이벤트 로그가 필요: csv 모드 또는 keep_events=True (main.py --at 은 이를 검사).
"""
import re
from collections import defaultdict

from src.resources.resources import role_of

START_HOUR = 9.0            # t=0 = Day 1 09:00
DAY_NAMES = ("월", "화", "수", "목", "금", "토", "일")

_TIME_RE = re.compile(r"\s*day\s*(\d+)\s+(\d{1,2}):(\d{2})\s*", re.IGNORECASE)
_WAIT_RE = re.compile(r"wait=([\d.]+)h")
_DUE_RE = re.compile(r"due=([\d.]+)")
_MACHINE_QUEUE = {"PRINTER_QUEUE_ENTER": ("printer", "VPP_BUILD_START"),
                  "WASHING_QUEUE_ENTER": ("washing", "WASHING_START"),
                  "UV_CURING_QUEUE_ENTER": ("uv", "UV_CURING_START")}


def parse_time(text):
    """'Day 3 14:25' -> 달력 시각 t [h]. 숫자 문자열이면 그대로 t. Day 1 09:00 이전은 오류."""
    try:
        return float(text)
    except (TypeError, ValueError):
        pass
    m = _TIME_RE.fullmatch(str(text))
    if not m:
        raise ValueError(f"시각 형식 오류: {text!r} — 예: 'Day 3 14:25' (Day 1 = 월요일)")
    day, hh, mm = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if day < 1 or hh > 23 or mm > 59:
        raise ValueError(f"시각 범위 오류: {text!r}")
    t = (day - 1) * 24 + hh + mm / 60 - START_HOUR
    if t < 0:
        raise ValueError(f"{text!r} 는 시뮬레이션 시작(Day 1 09:00) 이전")
    return t


def format_time(t):
    h = t + START_HOUR
    day, rem = int(h // 24) + 1, h % 24
    minutes = int(round(rem * 60))
    if minutes == 24 * 60:                                   # 반올림으로 24:00 이 되면 다음 날 00:00
        day, minutes = day + 1, 0
    return f"Day {day} {minutes // 60:02d}:{minutes % 60:02d} ({DAY_NAMES[(day - 1) % 7]})"


def _pauses_off_hours(res, unit):
    c = res.cfg
    return c.USE_WORK_CALENDAR and (not unit.startswith("P") or not c.PRINTER_UNATTENDED)


def state_at(res, t):
    if not res.log.keep_events:
        raise ValueError("이벤트 로그가 없음 — csv 모드 또는 keep_events=True 로 실행해야 시각 조회 가능")
    eps = 1e-9
    orders, arrived, completed, due = {}, set(), set(), {}
    machines = {u.name: "Idle" for u in res.units}
    workers = {w: None for ids in res.workers.values() for w in ids}
    queued = {}                                                  # (종류, 대상 ID) -> True
    for e in res.log.events:
        if e.sim_time > t + eps:
            break
        if e.entity_type == "MACHINE":
            machines[e.entity_id] = e.state
            continue
        for oid in filter(None, e.order_id.split(";")):
            orders[oid] = {"process": e.process, "state": e.state, "event": e.event, "entity_id": e.entity_id}
        if e.event == "ORDER_RECEIVED":
            arrived.add(e.entity_id)
            m = _DUE_RE.search(e.detail)
            if m:
                due[e.entity_id] = float(m.group(1))
        elif e.event == "ORDER_COMPLETED":
            completed.add(e.entity_id)
        if e.resource in workers:                                # 개인 ID -> 작업 시작/종료
            task = e.event.rsplit("_", 1)[0]
            if e.event.endswith("_START"):
                workers[e.resource] = f"{task} {e.entity_id}"
            elif e.event.endswith("_END"):
                workers[e.resource] = None
        if e.event in _MACHINE_QUEUE:
            queued[(_MACHINE_QUEUE[e.event][0], e.entity_id)] = True
        for kind, start_event in _MACHINE_QUEUE.values():
            if e.event == start_event:
                queued.pop((kind, e.entity_id), None)

    cal = res.calendar
    for unit, st in machines.items():
        if st in ("Running", "Setup") and _pauses_off_hours(res, unit) and not cal.is_open(t):
            machines[unit] = "Waiting"

    queues = {"printer": 0, "washing": 0, "uv": 0}
    for kind, _ in queued:
        queues[kind] += 1
    for role in res.workers:                                     # 인력 대기: 요청 <= t < 시작
        queues[role] = 0
    for e in res.log.events:
        role = role_of(e.resource)
        if role and e.event.endswith("_START") and e.sim_time > t + eps:
            m = _WAIT_RE.search(e.detail)
            if m and e.sim_time - float(m.group(1)) <= t + 5e-3:  # wait 는 소수 2자리 기록
                queues[role] += 1

    resin = sum(v for s, v, _ in res.resin_log if s <= t + eps) / 1e6
    in_system = arrived - completed
    return {
        "t": t, "label": format_time(t),
        "orders": {o: orders[o] for o in sorted(arrived) if o in orders},
        "wip": len(in_system), "completed": len(completed),
        "late_orders": sorted(o for o in in_system if o in due and due[o] < t - eps),
        "machines": machines, "workers": workers, "queues": queues, "resin_L": resin,
    }


def order_trace(res, order_id):
    """
    주문 1건을 처음부터 끝까지 추적한 이벤트 목록 [명세서 14절 Level 2 Event Trace Verification].
    그 주문·부품 이벤트와, 그 주문 부품이 들어간 배치·로드 이벤트를 시각 순서대로.
    """
    if not res.log.keep_events:
        raise ValueError("이벤트 로그가 없음 — csv 모드 또는 keep_events=True 로 실행해야 추적 가능")
    rows = [{"t": e.sim_time, "time": format_time(e.sim_time), "entity": f"{e.entity_type} {e.entity_id}",
             "event": e.event, "process": e.process, "location": e.location, "state": e.state,
             "resource": e.resource}
            for e in res.log.events if order_id in e.order_id.split(";")]
    if not rows:
        raise ValueError(f"주문 {order_id!r} 의 이벤트가 없음")
    return rows


def print_trace(rows):
    print("=" * 110)
    print(f"{'t(h)':>8}  {'시각':<18} {'대상':<16} {'이벤트':<32} {'공정':<18} {'상태':<11} 자원")
    for r in rows:
        print(f"{r['t']:8.2f}  {r['time']:<18} {r['entity']:<16} {r['event']:<32} {r['process']:<18} "
              f"{r['state']:<11} {r['resource']}")
    print("=" * 110)


QUEUE_LABELS = {"printer": "프린터", "washing": "세척", "uv": "UV", "job_assignment_workers": "JA",
                "post_process_workers": "후공정", "quality_inspectors": "검사", "packers": "포장",
                "printer_operators": "오퍼레이터"}


def print_state(st):
    print("=" * 78)
    print(f"[시각 조회] {st['label']}  (t = {st['t']:.2f}h)")
    print(f"WIP {st['wip']}건 | 완료 {st['completed']}건 | 납기 지연(미완료) {len(st['late_orders'])}건 "
          f"{st['late_orders'] if st['late_orders'] else ''}")
    by_proc = defaultdict(list)
    for oid, o in st["orders"].items():
        if o["state"] != "Done":
            by_proc[f"{o['process']} ({o['state']})"].append(oid)
    print("주문 위치:")
    for proc, ids in sorted(by_proc.items()):
        print(f"  {proc:<34} {' '.join(ids)}")
    print("설비: " + " | ".join(f"{u} {s}" for u, s in st["machines"].items()))
    print("작업자: " + " | ".join(f"{w} {task or '-'}" for w, task in st["workers"].items()))
    print("대기: " + " | ".join(f"{QUEUE_LABELS.get(k, k)} {v}" for k, v in st["queues"].items()))
    print(f"레진 누적 소모: {st['resin_L']:.4f} L")
    print("=" * 78)
