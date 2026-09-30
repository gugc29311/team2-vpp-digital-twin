# -*- coding: utf-8 -*-
"""
실험 프리셋: parameters.py 를 고치지 않고 '설정 변경 묶음'으로 정의하는 시나리오.

  SCENARIO(Normal / High Demand / Stress) 는 도착률 λ 만 바꾸고,
  프리셋은 그 위에 다른 값(긴급 비율 등)을 함께 바꾼다.

  Rush Order : λ 는 Normal 그대로, 긴급 주문 비율 10% -> 30% ⚠️ 30% 는 가정값 (인터뷰 확인 필요)
               긴급 여부 추출은 비율과 무관하게 난수를 1개씩 쓰므로 도착 패턴·면적·높이는 Normal 과 같다
               -> 같은 시드면 '긴급 비율만 다른' 공정한 비교.

사용
  python main.py --preset "Rush Order"                 # 1회
  python main.py --preset "Rush Order" --reps 30       # 30회 반복

  from src.experiments.scenarios import apply_preset
  cfg = apply_preset(SimConfig.from_parameters(), "Rush Order")
"""

PRESETS = {
    "Rush Order": dict(SCENARIO="Normal", URGENT_PROBABILITY=0.30),
}


def apply_preset(cfg, name):
    """프리셋 적용한 SimConfig 복사본 (random 모드). 없는 이름은 KeyError."""
    if name not in PRESETS:
        raise KeyError(f"프리셋 '{name}' 없음 — {sorted(PRESETS)} 중 하나")
    return cfg.replace(ORDER_SOURCE="random", **PRESETS[name])
