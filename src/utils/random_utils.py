# -*- coding: utf-8 -*-
"""
분포 정의 튜플 -> 난수 추출.

  ("const", 1, "h")            -> 1.0 h
  ("tri", 2, 5, 12, "min")     -> Triangular(2,5,12) 분을 hour 로 변환
  ("uniform", 400, 1600)       -> 단위 없음: 값 그대로 (면적 등)
  ("exp", 3, "h")              -> 평균 3h 지수분포

난수 스트림
  SeedSequence(RANDOM_SEED).spawn() 으로 용도별 독립 스트림을 만든다.
  -> 한 곳(예: 검사 불량률)을 바꿔도 다른 곳(예: 주문 도착)의 난수 순서가 바뀌지 않아
     시나리오 비교가 공정해진다 (공통 난수, CRN).
"""
import numpy as np

_TO_HOUR = {"h": 1.0, "min": 1 / 60, "s": 1 / 3600}
_N_PARAMS = {"const": 1, "tri": 3, "uniform": 2, "exp": 1}
# 용도별 스트림. 새 용도는 반드시 '뒤에' 추가 (앞 스트림들의 난수열이 바뀌지 않도록 — spawn 순번 고정)
STREAMS = ("orders", "process", "quality", "transport", "failure", "rework", "resin")


def make_streams(seed):
    """용도별 독립 난수 생성기 dict."""
    children = np.random.SeedSequence(seed).spawn(len(STREAMS))
    return {name: np.random.default_rng(ss) for name, ss in zip(STREAMS, children)}


def parse(spec):
    """분포 튜플 검증 -> (종류, 파라미터 tuple, hour 변환계수 또는 None)."""
    if not isinstance(spec, tuple) or not spec or spec[0] not in _N_PARAMS:
        raise ValueError(f"분포 정의 형식 오류: {spec!r}")
    kind = spec[0]
    rest = spec[1:]
    unit = None
    if rest and isinstance(rest[-1], str):
        unit, rest = rest[-1], rest[:-1]
        if unit not in _TO_HOUR:
            raise ValueError(f"알 수 없는 단위 {unit!r} (h/min/s): {spec!r}")
    if len(rest) != _N_PARAMS[kind]:
        raise ValueError(f"{kind} 는 파라미터 {_N_PARAMS[kind]}개 필요: {spec!r}")
    if kind == "tri" and not rest[0] <= rest[1] <= rest[2]:
        raise ValueError(f"tri 는 최소 <= 최빈 <= 최대: {spec!r}")
    return kind, tuple(float(x) for x in rest), (_TO_HOUR[unit] if unit else None)


def sample(spec, rng):
    """분포에서 1개 추출. 시간 단위가 있으면 hour 로 변환해 반환."""
    kind, p, scale = parse(spec)
    if kind == "const":
        x = p[0]
    elif kind == "tri":
        x = p[0] if p[0] == p[2] else rng.triangular(*p)
    elif kind == "uniform":
        x = rng.uniform(*p)
    else:
        x = rng.exponential(p[0])
    return x * (scale if scale is not None else 1.0)


def mean(spec):
    """분포의 이론 평균 (hour 변환 포함) — 이론값 검증용."""
    kind, p, scale = parse(spec)
    m = {"const": p[0], "tri": sum(p) / 3, "uniform": sum(p) / 2, "exp": p[0]}[kind]
    return m * (scale if scale is not None else 1.0)


def std(spec):
    """분포의 이론 표준편차 (hour 변환 포함) — 관측 평균의 허용 오차(3σ/√n) 계산용."""
    kind, p, scale = parse(spec)
    if kind == "const":
        s = 0.0
    elif kind == "tri":
        a, c, b = p                                     # 최소, 최빈, 최대
        s = ((a * a + b * b + c * c - a * b - a * c - b * c) / 18) ** 0.5
    elif kind == "uniform":
        s = (p[1] - p[0]) / 12 ** 0.5
    else:
        s = p[0]
    return s * (scale if scale is not None else 1.0)
