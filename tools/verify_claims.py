# -*- coding: utf-8 -*-
"""
분석 주장 검증 실험 — "원인을 바꾸면 결과가 예측대로 바뀌는가" / "다른 시드에서도 성립하는가".

실행 (프로젝트 최상위 폴더):  python -m tools.verify_claims
결과는 화면 출력 + outputs/verify_claims.md (검증 문서에 옮겨 쓸 표). 시드·설정이 고정이라 누구나 같은 숫자가 나온다.

주장 (검증 문서 7절)
  C1 세척 대기열이 UV 보다 긴 것은 세척 로드가 작아서(10개 vs 15개)
       -> 세척 로드를 15개로 바꾸면 세척 대기열이 UV 수준으로 줄어야 함 (UV 로드를 10개로 바꾸면 UV 대기열이 늘어야 함)
  C2 일별 세척 대기열이 튀는 날은 퇴근 직전 도착 로드가 밤을 넘기기 때문
       -> 근무시간 밖 대기를 빼면 튀는 날이 사라져야 함
  C3 일별 처리량 흔들림의 주원인은 주문 도착 수
       -> 여러 시드에서 같은 날 도착 수와 완료 수의 상관이 높아야 함
  C4 긴급 주문 납기 준수율이 낮은 것은 FCFS(긴급 우선 없음) 때문
       -> EDD·URGENT_FIRST 로 바꾸면 긴급 납기 준수율이 올라야 함
  C5 월요일 프린터 가동률이 튀는 것은 금요일 저녁 배치가 주말 동안 대기하기 때문
       -> 주말 대기 배치가 있었던 월요일의 가동률이 다른 평일보다 높아야 함
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from src.analysis.kpi import daily_table, kpis           # noqa: E402
from src.model.config import SimConfig                    # noqa: E402
from src.model.simulation import VPPSimulation            # noqa: E402

SEEDS = [42, 7, 101, 2024, 31337]
WEEKS = 4


def cfg(seed, warmup_weeks=0, weeks=WEEKS, **over):
    c = SimConfig.from_parameters().replace(ORDER_SOURCE="random", SCENARIO="Normal", RANDOM_SEED=seed,
                                            WARMUP_TIME=warmup_weeks * 168,
                                            SIMULATION_TIME=(warmup_weeks + weeks) * 168)
    return c.replace(**over) if over else c


def run(c, events=False):
    return VPPSimulation(c, keep_events=events).run()


def mean_ci(xs):
    xs = np.asarray(xs, float)
    half = 2.776 * xs.std(ddof=1) / np.sqrt(len(xs)) if len(xs) > 1 else float("nan")   # t(0.975, 4)
    return f"{xs.mean():.3f} ± {half:.3f}"


def is_weekday(day):
    return (day - 1) % 7 < 5


out = []


def say(line=""):
    print(line)
    out.append(line)


# ---------------------------------------------------------------- C1
say("## C1 세척 로드 크기 -> 세척 대기열")
rows = {"기본 (세척 10 · UV 15)": {}, "세척 로드 15": {"WASHING_LOAD_CAPACITY": 15},
        "UV 로드 10": {"UV_CURING_LOAD_CAPACITY": 10}}
say("| 설정 | 세척 대기열 (로드) | UV 대기열 (로드) |")
say("|---|---|---|")
for name, over in rows.items():
    w, u = [], []
    for s in SEEDS:
        k = kpis(run(cfg(s, warmup_weeks=1, **over)))
        w.append(k["washing_queue_mean"])
        u.append(k["uv_queue_mean"])
    say(f"| {name} | {mean_ci(w)} | {mean_ci(u)} |")
say()

# ---------------------------------------------------------------- C2
say("## C2 일별 세척 대기열이 튀는 날 = 밤을 넘긴 로드가 있는 날")
spike_total = spike_overnight = 0
max_without = []
for s in SEEDS:
    res = run(cfg(s))
    cal = res.calendar
    for day in range(1, WEEKS * 7 + 1):
        if not is_weekday(day):
            continue
        a, b = (day - 1) * 24, day * 24
        total = night = 0.0
        for m, e, st in res.load_queue:
            if m != "washing_machines":
                continue
            st = res.end_time if st is None else st
            lo, hi = max(e, a), min(st, b)
            if hi > lo:
                total += hi - lo
                night += (hi - lo) - cal.work_hours(lo, hi)
        if total / 24 > 0.5:                                   # 튀는 날 (일평균 대기열 0.5 초과)
            spike_total += 1
            spike_overnight += night > 0
        max_without.append((total - night) / 24)
say(f"- 시드 {len(SEEDS)}개 x 평일 {WEEKS * 5}일 = {len(SEEDS) * WEEKS * 5}일 중 튀는 날(> 0.5): {spike_total}일, "
    f"그중 밤을 넘긴 로드가 있는 날: {spike_overnight}일")
say(f"- 근무시간 밖 대기를 뺀 일평균 대기열: 최대 {max(max_without):.3f}, 평균 {np.mean(max_without):.3f}")
say()

# ---------------------------------------------------------------- C3
say("## C3 일별 처리량 흔들림 vs 주문 도착 수")
say("| 시드 | 완료 표준편차 | 도착 표준편차 | 같은 날 상관 |")
say("|---|---|---|---|")
corrs = []
for s in SEEDS:
    res = run(cfg(s))
    arr, done = {}, {}
    for o in res.orders:
        d = int(o.arrival_time // 24) + 1
        arr[d] = arr.get(d, 0) + 1
        if o.completed_time is not None:
            d = int(o.completed_time // 24) + 1
            done[d] = done.get(d, 0) + 1
    days = [d for d in range(2, WEEKS * 7 + 1) if is_weekday(d)]     # 첫날(빈 공장) 제외
    x = np.array([done.get(d, 0) for d in days])
    y = np.array([arr.get(d, 0) for d in days])
    r = np.corrcoef(x, y)[0, 1]
    corrs.append(r)
    say(f"| {s} | {x.std():.1f} | {y.std():.1f} | {r:.2f} |")
say(f"- 상관 평균 {np.mean(corrs):.2f} (최소 {min(corrs):.2f})")
say()

# ---------------------------------------------------------------- C4
say("## C4 프린터 순서 규칙 -> 긴급 납기 준수율")
say("| 규칙 | 긴급 납기 준수율 | 일반 납기 준수율 |")
say("|---|---|---|")
for rule in ("FCFS", "EDD", "URGENT_FIRST"):
    ur, nr = [], []
    for s in SEEDS:
        k = kpis(run(cfg(s, warmup_weeks=1, DEFAULT_SCHEDULING_RULE=rule)))
        ur.append(k["on_time_urgent"])
        nr.append(k["on_time_normal"])
    say(f"| {rule} | {mean_ci(ur)} | {mean_ci(nr)} |")
say()

# ---------------------------------------------------------------- C5
say("## C5 월요일 프린터 가동률 vs 주말 대기 배치")
carry, nocarry, other = [], [], []
for s in SEEDS:
    res = run(cfg(s))
    daily = {r["day"]: r for r in daily_table(res)}
    for day, r in daily.items():
        if not is_weekday(day) or day == 1:
            continue
        if (day - 1) % 7 == 0:                                  # 월요일
            sat = daily.get(day - 2)
            (carry if sat and sat["printer_queue_mean"] > 0 else nocarry).append(r["printer_util"])
        else:
            other.append(r["printer_util"])
fmt = lambda xs: f"{np.mean(xs):.3f} (n={len(xs)})" if xs else "- (n=0)"
say(f"- 주말 대기 배치가 있던 월요일: {fmt(carry)}")
say(f"- 주말 대기 배치가 없던 월요일: {fmt(nocarry)}")
say(f"- 화~금: {fmt(other)}")

os.makedirs("outputs", exist_ok=True)
with open("outputs/verify_claims.md", "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")
print("\n저장: outputs/verify_claims.md")
