# -*- coding: utf-8 -*-
"""
설정값 <-> 관측값 교차 검증 [회의 피드백: 입력값이 결과에 제대로 반영되는지].

  cross_checks(res) -> [{항목, 설정, 관측, n, 판정, 기준}]
      판정: "OK" (허용 범위 안) / "확인 필요" (범위 밖 — 로직 또는 표본 크기 점검) / "참고" (판정 기준 없음)

허용 범위
  - 비율(불량률·긴급 비율): 기대 p ± 3·sqrt(p(1-p)/n). p = 0 이면 관측도 정확히 0 이어야 함
  - 건수(도착·고장): 기대 μ ± 3·sqrt(μ) (Poisson), 최소 ±3
  - 평균 작업시간: 분포 평균 ± 3·σ/sqrt(n) (σ = 분포 표준편차). 고정값이면 오차 1e-6
  - 규칙(배치 확정 조건, 재출력 수, 고장 OFF 등): 정확히 일치
  - 이동시간(MOVE_TIME_MODE = "distance"): 이동 1건마다 기록된 시간 = 거리 ÷ 속도 (+ 선반·적재 시간) 를
    이 파일에서 따로 계산한 값과 비교 (오차 0.01초 이내). 작업자는 근무시간, AMR 은 달력시간 기준
이벤트 로그 필요 (keep_events=True). 표본이 작으면(짧은 실행) '확인 필요'가 우연히 나올 수 있음 — 기간을 늘려 재확인.
"""
import math
import re
from collections import Counter

from src.analysis.event_schema import route_parts
from src.analysis.kpi import kpis, measure_start
from src.resources.resources import role_of
from src.utils.random_utils import mean, std
from src.validation.invariants import check
from src.validation.locations import DOOR, amr_tasks, order_jumps, room_of, worker_jumps, worker_tasks

EPS = 1e-6
# 사람 작업(이벤트 이름) -> 작업시간 설정 키. 작업 1건 = START~END 근무시간
TASK_TIMES = (("JOB_ASSIGNMENT", "JOB_ASSIGNMENT_TIME", "작업 배정 시간 (주문당)"),
              ("SUPPORT_REMOVAL", "SUPPORT_REMOVAL_TIME", "서포트 제거 시간 (부품당)"),
              ("SURFACE_TREATMENT", "SURFACE_TREATMENT_TIME", "표면처리 시간 (부품당)"),
              ("INSPECTION", "INSPECTION_TIME", "검사 시간 (부품당)"),
              ("PACKAGING", "PACKAGING_TIME", "포장 시간 (부품당)"))


def _reprint_id(part_id):
    """불량 부품 번호 -> 재출력 부품 번호 (O001-1 -> O001-1-R1, O001-1-R1 -> O001-1-R2)."""
    m = re.fullmatch(r"(.*)-R(\d+)", part_id)
    return f"{m.group(1)}-R{int(m.group(2)) + 1}" if m else f"{part_id}-R1"


def _row(item, setting, observed, n, verdict, rule):
    return {"항목": item, "설정": setting, "관측": observed, "n": n, "판정": verdict, "기준": rule}


def _rate(item, p, hits, n):
    if n == 0:
        return _row(item, f"{p:.2%}", "-", 0, "참고", "표본 없음")
    obs = hits / n
    if p == 0:
        return _row(item, "0%", f"{obs:.2%}", n, "OK" if hits == 0 else "확인 필요", "설정 0 -> 발생 0")
    tol = 3 * math.sqrt(p * (1 - p) / n)
    return _row(item, f"{p:.2%}", f"{obs:.2%}", n, "OK" if abs(obs - p) <= tol else "확인 필요",
                f"±3σ = ±{tol:.2%}")


def _count(item, mu, obs, unit=""):
    tol = max(3.0, 3 * math.sqrt(mu))
    return _row(item, f"{mu:.1f}{unit}", f"{obs}{unit}", obs, "OK" if abs(obs - mu) <= tol else "확인 필요",
                f"Poisson ±{tol:.1f}")


def _mean_time(item, spec, values, unit="분"):
    m, s, n = mean(spec), std(spec), len(values)
    k = 60 if unit == "분" else 1                                  # hour -> 표시 단위
    if n == 0:
        return _row(item, f"{m * k:.2f}{unit}", "-", 0, "참고", "표본 없음")
    obs = sum(values) / n
    tol = max(EPS, 3 * s / math.sqrt(n))
    return _row(item, f"{m * k:.2f}{unit}", f"{obs * k:.2f}{unit}", n, "OK" if abs(obs - m) <= tol else "확인 필요",
                f"±3σ/√n = ±{tol * k:.2f}{unit}")


def _distance(cfg, a, b):
    """이동 거리 [m] — 시뮬레이션과 별도로 다시 계산 (같은 공식이면 결과가 같아야 함)."""
    if a == b:
        return 0.0
    if room_of(a) == room_of(b):
        return cfg.ROOM_DEPTH_M
    X = cfg.ROOM_DOOR_X_M
    da = 0.0 if a.endswith(DOOR) else cfg.ROOM_DEPTH_M
    db = 0.0 if b.endswith(DOOR) else cfg.ROOM_DEPTH_M
    return da + abs(X[room_of(a)] - X[room_of(b)]) + db


def _move_rows(res, t0):
    """이동 1건마다 기록 시간 = 거리 ÷ 속도 (+ 고정 시간) 인지. 선반·적재 시간이 분포면 평균으로 비교 (3σ)."""
    cfg, cal = res.cfg, res.calendar
    tol_s = 0.01 / 3600                                           # 0.01초
    shelf_m, shelf_sd = mean(cfg.SHELF_HANDLING_TIME), std(cfg.SHELF_HANDLING_TIME)
    bad = n = 0
    for w, s, e, task, ent, a, b, _ in worker_tasks(res):
        if e is None or s < t0 or a == b:
            continue
        d = _distance(cfg, a, b)
        if task == "WALK":
            exp, tol = d / cfg.WALK_SPEED_M_S / 3600, tol_s
        elif task.startswith(("HANDOFF_", "RECEIVE_")):
            exp, tol = d / cfg.CARRY_SPEED_M_S / 3600 + shelf_m, tol_s + 3 * shelf_sd
        else:                                                    # 사람이 직접 운반 (worker 모드)
            exp, tol = d / cfg.CARRY_SPEED_M_S / 3600, tol_s
        n += 1
        bad += abs(cal.work_hours(s, e) - exp) > tol
    rows = [_row("작업자 이동시간 = 거리 ÷ 속도", f"걷기 {cfg.WALK_SPEED_M_S} m/s · 짐 {cfg.CARRY_SPEED_M_S} m/s",
                 f"불일치 {bad}건 / {n}건", n, "OK" if bad == 0 else "확인 필요",
                 "이동마다 (방 깊이 + 문 사이 거리) ÷ 속도, 0.01초 이내")]
    if cfg.TRANSPORT_ENABLED and cfg.TRANSPORT_MODE == "amr":
        bad = n = 0
        for amr, s, e, task, ent, a, b, _, xfer in amr_tasks(res):
            if e is None or s < t0:
                continue
            exp = _distance(cfg, a, b) / cfg.AMR_SPEED_M_S / 3600 + 2 * xfer
            n += 1
            bad += abs((e - s) - exp) > tol_s
        rows.append(_row("AMR 이동시간 = 거리 ÷ 속도 + 적재·하역", f"{cfg.AMR_SPEED_M_S} m/s · {cfg.AMR_COUNT}대",
                         f"불일치 {bad}건 / {n}건", n, "OK" if bad == 0 else "확인 필요", "0.01초 이내 (달력시간)"))
        # 같은 물건(대상 ID + 구간)끼리 짝지음: 측정 구간에 선반에 놓인 것은 AMR 운반 -> 선반 꺼내기까지 이어져야 함
        legs = {}
        for e in res.log.events:
            rp = route_parts(e.event) if e.event.endswith("_START") else None
            if rp:
                legs.setdefault((e.entity_id, rp[1]), {})[rp[0]] = e.sim_time
        started = [v for v in legs.values() if v.get("HANDOFF_", -1) >= t0]
        broken = sum(1 for v in started if "TRANSPORT_" not in v or "RECEIVE_" not in v)
        orphan = sum(1 for v in legs.values() if "HANDOFF_" not in v)      # 놓지 않았는데 운반·꺼내기
        rows.append(_row("선반 놓기 -> AMR 운반 -> 선반 꺼내기 (짝)", "모두 이어짐",
                         f"미완 {broken}건 · 짝 없음 {orphan}건 / {len(started)}건", len(started),
                         "OK" if orphan == 0 and broken <= cfg.AMR_COUNT + 2 else "확인 필요",
                         "운반마다 3단계 (종료 시 진행 중인 몇 건 허용)"))
    return rows


def cross_checks(res):
    if not res.log.keep_events:
        raise ValueError("이벤트 로그가 없음 — keep_events=True 로 실행해야 교차 검증 가능")
    cfg, cal = res.cfg, res.calendar
    t0, t1 = measure_start(res), res.end_time
    ev = [e for e in res.log.events if e.sim_time >= t0 - EPS]
    cnt = Counter(e.event for e in ev)
    rows = []

    # 1. 주문 도착 · 긴급 비율 · 납기 여유
    arrived = [o for o in res.orders if o.arrival_time >= t0]
    if cfg.ORDER_SOURCE == "random":
        weeks = cal.work_hours(t0, t1) / cal.hours_per_week
        rows.append(_count("주문 도착 수 (λ x 기간)", cfg.arrival_rate_per_week * weeks, len(arrived), "건"))
        rows.append(_rate("긴급 주문 비율", cfg.URGENT_PROBABILITY, sum(o.is_urgent for o in arrived), len(arrived)))
        for urgent, key, label in ((False, "DUE_DATE_SLACK_NORMAL", "일반"), (True, "DUE_DATE_SLACK_URGENT", "긴급")):
            slack = [cal.work_hours(o.arrival_time, o.due_date) for o in arrived if o.is_urgent == urgent]
            rows.append(_mean_time(f"납기 여유 평균 ({label}, 근무시간)", getattr(cfg, key), slack, unit="h"))

    # 2. 불량 · 재출력
    rows.append(_rate("검사 불량률", cfg.INSPECTION_FAILURE_RATE, cnt["INSPECTION_FAILED"], cnt["INSPECTION_END"]))
    printed = sum(1 for t, _ in res.printed_parts if t >= t0)
    rows.append(_rate("출력 실패율", cfg.PRINT_FAILURE_RATE, cnt["PRINT_FAILED"], printed))
    fails = cnt["INSPECTION_FAILED"] + cnt["PRINT_FAILED"]
    if cfg.REWORK_ENABLED:
        # 불량 부품마다 재출력 부품(번호 + -R{n})의 작업 배정이 있어야 함 — 같은 부품끼리 짝지음.
        # 측정 끝 무렵 불량은 아직 배정 전일 수 있어 그 차이만 허용
        failed = [e.entity_id for e in ev if e.event in ("INSPECTION_FAILED", "PRINT_FAILED")]
        rework_ids = {e.entity_id for e in res.log.events if e.event == "JOB_ASSIGNMENT_REWORK_START"}
        pending = sum(1 for p in failed if _reprint_id(p) not in rework_ids)
        rows.append(_row("불량마다 재출력", f"불량 {fails}건", f"재출력 {fails - pending}건 · 대기 {pending}건", fails,
                         "OK" if pending <= cfg.JOB_ASSIGNMENT_WORKER_COUNT + 2 else "확인 필요",
                         "불량 부품 -> 재출력 작업 배정 (종료 시 대기 중 몇 건 허용)"))
    else:
        rows.append(_row("재출력 OFF -> 재출력 0", "OFF", f"재출력 {cnt['JOB_ASSIGNMENT_REWORK_START']}건 · "
                         f"폐기 {cnt['SCRAPPED']}건", fails,
                         "OK" if cnt["JOB_ASSIGNMENT_REWORK_START"] == 0 and cnt["SCRAPPED"] == fails else "확인 필요",
                         "재출력 0, 폐기 = 불량"))

    # 3. 고장 · PM
    if not cfg.BREAKDOWN_ENABLED:
        n_down = cnt["DOWN_START"] + cnt["MAINTENANCE_START"]
        rows.append(_row("고장 OFF -> 고장·PM 0", "OFF", f"{n_down}건", n_down, "OK" if n_down == 0 else "확인 필요",
                         "DOWN·MAINTENANCE 이벤트 0 (세척액 교체는 별도)"))
    else:
        for kind, label in (("printer", "프린터"), ("washing", "세척기"), ("uv_curing", "UV기")):
            units = [u for u in res.units if u.kind == kind]
            if not units:
                continue
            mtbf = cfg.EQUIPMENT_FAILURE[kind]["mtbf"]
            expected = sum(u.cum_op for u in units) / mtbf          # 누적 가동시간 / MTBF (전체 기간)
            observed = sum(1 for u in units for k, s, e in u.log if k == "fail")
            rows.append(_count(f"{label} 고장 횟수 (가동시간/MTBF)", expected, observed, "회"))

    # 4. 사람 작업시간 평균 (근무시간)
    starts = {}
    dur = {name: [] for name, _, _ in TASK_TIMES}
    trans = []
    for e in ev:
        base = e.event.rsplit("_", 1)[0]
        if not role_of(e.resource) or not (base in dur or base.startswith("TRANSPORT_")):
            continue
        key = (e.resource, base, e.entity_id)
        if e.event.endswith("_START"):
            starts[key] = e.sim_time
        elif e.event.endswith("_END") and key in starts:
            d = cal.work_hours(starts.pop(key), e.sim_time)
            (trans if base.startswith("TRANSPORT_") else dur[base]).append(d)
    for name, key, label in TASK_TIMES:
        rows.append(_mean_time(label, getattr(cfg, key), dur[name]))
    if cfg.MOVE_TIME_MODE == "distance":
        rows += _move_rows(res, t0)
    elif cfg.TRANSPORT_ENABLED:
        rows.append(_mean_time("이동 시간 (①~⑥ 전체)", cfg.DEFAULT_TRANSPORT_TIME, trans))
    else:
        rows.append(_row("이동 OFF -> 이동 0", "OFF", f"{len(trans)}건", len(trans),
                         "OK" if not trans else "확인 필요", "TRANSPORT 이벤트 0"))

    # 5. 배치 확정 규칙
    bs = [b for b in res.batches if b.closed_time is not None and b.closed_time >= t0]
    if cfg.BATCH_FILL_RATIO is not None and cfg.BUILD_PLATE_AREA_MM2:
        need = cfg.BATCH_FILL_RATIO * cfg.BUILD_PLATE_AREA_MM2
        area = [b for b in bs if b.trigger == "AREA"]
        bad = sum(b.total_area < need - EPS for b in area)
        rows.append(_row("면적 조건 확정 배치 >= 채움률", f">= {need:,.0f} mm²",
                         f"최소 {min((b.total_area for b in area), default=float('nan')):,.0f} mm²", len(area),
                         "OK" if bad == 0 else "확인 필요", "모든 AREA 배치"))
    if cfg.BATCH_MAX_WAIT_TIME is not None and cfg.URGENT_BATCH_MAX_WAIT_TIME is None:
        T = mean(cfg.BATCH_MAX_WAIT_TIME)
        timed = [cal.work_hours(b.created_time, b.closed_time) for b in bs if b.trigger == "TIME"]
        early = [w for w in [cal.work_hours(b.created_time, b.closed_time) for b in bs] if w > T + 1e-3]
        rows.append(_row("시간 조건: 대기 <= T", f"T = {T:.1f}h (근무)",
                         f"TIME 확정 {len(timed)}개 · T 초과 {len(early)}개", len(bs),
                         "OK" if not early else "확인 필요", "어떤 배치도 T 보다 오래 기다리지 않음"))

    # 6. 구조 검사
    jumps = order_jumps(res)
    if cfg.TRANSPORT_ENABLED:
        rows.append(_row("부품 순간이동 (이동 없이 위치 변경)", "0건", f"{len(jumps)}건", len(res.orders),
                         "OK" if not jumps else "확인 필요", "locations.order_jumps"))
    else:
        rows.append(_row("부품 순간이동 (이동 OFF)", "이동 없음", f"{len(jumps)}건", len(res.orders), "참고",
                         "TRANSPORT_ENABLED=False 면 방 사이 이동 시간이 0 이라 순간이동이 정상"))
    if cfg.MOVE_TIME_MODE == "distance":
        wj = worker_jumps(res)
        rows.append(_row("작업자 순간이동 (걷지 않고 위치 변경)", "0건", f"{len(wj)}건", len(wj),
                         "OK" if not wj else "확인 필요", "locations.worker_jumps (빈손 이동 포함 모든 이동이 모델에 있음)"))
    viol = check(res)
    rows.append(_row("불변식 위반 (용량·순서·근무시간 등)", "0건", f"{len(viol)}건", len(viol),
                     "OK" if not viol else "확인 필요", "invariants.check"))
    k = kpis(res)
    if not math.isnan(k["wip_little"]) and k["wip_little"] > 0:
        rel = abs(k["wip_mean"] - k["wip_little"]) / k["wip_little"]
        short = cal.work_hours(t0, t1) < 4 * cal.hours_per_week
        rows.append(_row("Little 법칙 (WIP = λ·W)", f"{k['wip_little']:.2f}건", f"{k['wip_mean']:.2f}건", len(arrived),
                         "OK" if rel <= 0.10 else ("참고" if short else "확인 필요"),
                         f"차이 {rel:.1%} — 10% 이내 OK. 4주 미만 실행은 시작(빈 공장)·끝(미완료) 영향이 커서 참고"))
    return rows
