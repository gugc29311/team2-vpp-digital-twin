# -*- coding: utf-8 -*-
"""
위치 연속성 검사: 이동 이벤트 없이 위치가 바뀌는 '순간이동' 찾기 [회의 피드백: 이동 주체·시간·위치 추적].

  move_endpoints(cfg, event, detail) -> 이동 이벤트의 (출발 위치, 도착 위치), 이동이 아니면 None
  order_jumps(res)  -> 주문(실물 부품)이 이동 없이 다른 위치에 나타난 경우. 정상 모델이면 빈 목록
  worker_tasks(res) -> 작업자별 작업 구간 (출발·도착 위치 포함)
  worker_jumps(res) -> 작업자가 이동(WALK·운반) 없이 다른 위치의 작업을 시작한 경우. 이동 모델(distance)이면 빈 목록
  amr_tasks(res)    -> AMR 별 이동 구간 (빈 차 이동 · 운반)

위치 표기
  "방"      = 그 방의 작업 위치 (parameters.LOCATIONS 값)
  "방:door" = 그 방 문 앞 복도 (AMR 선반)
  "Virtual" = 실물 없음 (접수·배치 구성) — 방으로 보지 않음
이동 이벤트 (event_schema)
  WALK / AMR_MOVE : detail 의 "출발->도착"
  HANDOFF_<구간>  : 출발 방 -> 출발 방:door        RECEIVE_<구간> : 도착 방:door -> 도착 방
  TRANSPORT_<구간>: AMR 모드 = 출발 방:door -> 도착 방:door, 사람 모드 = 출발 방 -> 도착 방
부품은 출력 시작(VPP_BUILD_START)부터 실물. 불량·폐기(*_FAILED, SCRAPPED) 되면 사라지고 재출력은 다시 출력 시작부터.
주문 단위로 추적하므로 부품이 여러 개인 주문(quantity > 1)은 건너뜀. 이벤트 로그 필요 (keep_events=True).
"""
from src.analysis.event_schema import TRANSPORT_ROUTES, VIRTUAL_LOCATION, base_name, route_parts

DOOR = ":door"


def room_of(loc):
    return loc.split(":")[0]


def move_endpoints(cfg, event, detail=""):
    base = base_name(event)
    if base in ("WALK", "AMR_MOVE"):
        a, b = detail.split(" wait=")[0].split("->")
        return a, b
    rp = route_parts(event)
    if not rp:
        return None
    prefix, seg = rp
    o, d = (cfg.LOCATIONS[p] for p in TRANSPORT_ROUTES[seg])
    if prefix == "HANDOFF_":
        return o, o + DOOR
    if prefix == "RECEIVE_":
        return d + DOOR, d
    if cfg.TRANSPORT_MODE == "amr":
        return o + DOOR, d + DOOR
    return o, d


def order_jumps(res):
    if not res.log.keep_events:
        return []
    cfg = res.cfg
    single = {o.order_id for o in res.orders if o.quantity == 1}
    cur, start_of, out = {}, {}, []                             # 주문 -> 현재 위치 (None = 실물 없음)
    for e in res.log.events:
        if e.entity_type in ("MACHINE", "WORKER", "AMR") or not e.order_id:
            continue
        for oid in e.order_id.split(";"):
            if oid not in single:
                continue
            here = cur.get(oid)
            if e.event.endswith("_FAILED") or e.event == "SCRAPPED":
                cur[oid] = None
                continue
            if e.event == "VPP_BUILD_START":
                cur[oid] = e.location
                continue
            if here is None:                                    # 아직 출력 전 (정보 단계)
                continue
            ends = move_endpoints(cfg, e.event, e.detail)
            if ends:
                if e.event.endswith("_START"):
                    if here != ends[0]:
                        out.append({"order_id": oid, "t": e.sim_time, "from": here, "to": ends[0], "event": e.event})
                    cur[oid] = "moving"
                else:
                    cur[oid] = ends[1]
                continue
            if e.location in (VIRTUAL_LOCATION, here):
                continue
            out.append({"order_id": oid, "t": e.sim_time, "from": here, "to": e.location, "event": e.event})
            cur[oid] = e.location
    return out


def worker_tasks(res):
    """
    작업자별 작업 구간 [(작업자, 시작, 끝, 작업 이름, 대상 ID, 출발 위치, 도착 위치, 관련 주문 수)] (시작 시각 순).
    이동이 아니면 출발 = 도착 = 작업 장소. 끝나지 않은 작업은 끝 = None.
    """
    if not res.log.keep_events:
        return []
    cfg = res.cfg
    ids = {w for ws in res.workers.values() for w in ws}
    open_, out = {}, []
    for e in res.log.events:
        if e.resource not in ids:
            continue
        task = base_name(e.event)
        if e.event.endswith("_START"):
            a, b = move_endpoints(cfg, e.event, e.detail) or (e.location, e.location)
            n = len(e.order_id.split(";")) if e.order_id else 0
            rec = [e.resource, e.sim_time, None, task, e.entity_id, a, b, n]
            open_[(e.resource, task, e.entity_id)] = rec
            out.append(rec)
        elif e.event.endswith("_END"):
            rec = open_.pop((e.resource, task, e.entity_id), None)
            if rec is not None:
                rec[2] = e.sim_time
    return [tuple(r) for r in out]


def worker_jumps(res, tasks=None):
    """작업자가 이동 이벤트 없이 다른 위치에서 다음 작업을 시작한 경우. 첫 위치는 시작 위치(worker_home)."""
    tasks = worker_tasks(res) if tasks is None else tasks
    cur, out = dict(getattr(res, "worker_home", {}) or {}), []
    for w, s, e, task, ent, a, b, _ in tasks:
        here = cur.get(w)
        if here is not None and here != a:
            out.append({"worker": w, "t": s, "from": here, "to": a, "task": task, "entity": ent})
        cur[w] = b
    return out


def amr_tasks(res):
    """AMR 별 이동 구간 [(AMR, 시작, 끝, 작업 이름, 대상 ID, 출발 위치, 도착 위치, 부품 수, 적재·하역 시간 h)]."""
    if not res.log.keep_events:
        return []
    cfg = res.cfg
    open_, out = {}, []
    for e in res.log.events:
        if not (e.resource or "").startswith("AMR"):
            continue
        task = base_name(e.event)
        if e.event.endswith("_START"):
            a, b = move_endpoints(cfg, e.event, e.detail)
            xfer = float(e.detail.split("transfer=")[1].rstrip("h")) if "transfer=" in e.detail else 0.0
            n = len(e.order_id.split(";")) if e.order_id else 0
            rec = [e.resource, e.sim_time, None, task, e.entity_id, a, b, n, xfer]
            open_[(e.resource, task, e.entity_id)] = rec
            out.append(rec)
        elif e.event.endswith("_END"):
            rec = open_.pop((e.resource, task, e.entity_id), None)
            if rec is not None:
                rec[2] = e.sim_time
    return [tuple(r) for r in out]
