# -*- coding: utf-8 -*-
"""
독립 반복실행 (Independent Replications) [공통5 · 명세서 Level 8].

  - 반복별 시드: SeedSequence(REPLICATION_ROOT_SEED).generate_state(n) -> 서로 다른 엔트로피 n 개
    (모델 내부에서 다시 용도별 스트림으로 spawn -> 스트림끼리 겹치지 않음)
  - KPI 별 평균, 표준편차, 95% CI (t, df=n-1), 상대정밀도(CI 반폭/평균), 95% 예측구간(단일 run 범위)
  - jobs > 1 이면 프로세스 병렬 실행 (Windows 는 스크립트로 실행해야 함: python main.py --reps 30)

사용
  from src.experiments.replications import run_replications, summarize
  rows = run_replications(cfg, n=30, jobs=8)
  table = summarize(rows)
"""
import math
import os
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from src.analysis.kpi import kpis
from src.model.config import SimConfig
from src.model.simulation import VPPSimulation

try:
    from scipy import stats as _st

    def _t975(df):
        return float(_st.t.ppf(0.975, df))
except ImportError:                                     # scipy 없을 때 근사
    def _t975(df):
        table = {9: 2.262, 19: 2.093, 29: 2.045, 49: 2.010, 99: 1.984}
        return table.get(df, 1.96 + 2.4 / df)


def replication_seeds(n, root):
    seeds = np.random.SeedSequence(root).generate_state(n).tolist()
    assert len(set(seeds)) == n, "반복 시드 중복"
    return [int(s) for s in seeds]


def _one(args):
    """반복 1회 (프로세스 풀에서 호출되므로 최상위 함수)."""
    values, seed = args
    cfg = SimConfig(dict(values)).replace(RANDOM_SEED=seed)
    res = VPPSimulation(cfg, keep_events=False).run()
    out = kpis(res)
    out["seed"] = seed
    return out


def run_replications(cfg, n=None, root=None, jobs=None):
    """n 회 독립 반복 -> 반복별 KPI dict 리스트 (시드 순서대로)."""
    n = n or cfg.REPLICATIONS
    root = cfg.REPLICATION_ROOT_SEED if root is None else root
    args = [(cfg.values, s) for s in replication_seeds(n, root)]
    jobs = jobs if jobs is not None else max(1, (os.cpu_count() or 2) - 1)
    if jobs <= 1:
        return [_one(a) for a in args]
    with ProcessPoolExecutor(max_workers=min(jobs, n)) as ex:
        return list(ex.map(_one, args))


def summarize(rows, keys=None):
    """KPI 별 {n, mean, sd, ci_lo, ci_hi, rel_precision, pi_lo, pi_hi, min, max}."""
    keys = keys or [k for k in rows[0] if k != "seed"]
    out = {}
    for k in keys:
        x = np.array([r[k] for r in rows], dtype=float)
        x = x[~np.isnan(x)]
        n = len(x)
        if n < 2:
            continue
        m, sd = float(x.mean()), float(x.std(ddof=1))
        t = _t975(n - 1)
        h = t * sd / math.sqrt(n)
        ph = t * sd * math.sqrt(1 + 1 / n)
        out[k] = {"n": n, "mean": m, "sd": sd, "ci_lo": m - h, "ci_hi": m + h,
                  "rel_precision": h / abs(m) if m else float("inf"),
                  "pi_lo": m - ph, "pi_hi": m + ph, "min": float(x.min()), "max": float(x.max())}
    return out


def print_summary_table(summary, keys=None, title=""):
    keys = keys or list(summary)
    if title:
        print(title)
    print(f"{'KPI':<34} {'평균':>10} {'SD':>9} {'95% CI':>23} {'상대정밀도':>9} {'최소~최대':>21}")
    for k in keys:
        if k not in summary:
            continue
        s = summary[k]
        pct = k.startswith(("util_", "printer_rho", "on_time", "rework_share", "completion", "batch_trigger"))
        f = (lambda v: f"{v:.2%}") if pct else (lambda v: f"{v:.3f}")
        print(f"{k:<34} {f(s['mean']):>10} {f(s['sd']):>9} [{f(s['ci_lo']):>9}, {f(s['ci_hi']):>9}] "
              f"{s['rel_precision']:>9.2%} {f(s['min']):>10}~{f(s['max']):<10}")
