# -*- coding: utf-8 -*-
"""
SimConfig: config/parameters.py 의 기본값을 담는 설정 객체.

왜 필요한가
  `from config.parameters import X` 는 import 순간 값이 고정되어,
  시나리오(Normal/High Demand)·반복실행·인원 비교처럼 값을 바꿔가며 돌릴 수 없다.
  모델은 항상 SimConfig 객체만 읽고, 실험 코드는 replace() 로 값을 바꾼다.

사용 예
  cfg = SimConfig.from_parameters()
  cfg2 = cfg.replace(SCENARIO="High Demand", POST_PROCESS_WORKER_COUNT=4, RANDOM_SEED=7)
"""
import copy
from dataclasses import dataclass, field

import config.parameters as P
from src.analysis.event_schema import PROCESSES
from src.scheduler.dispatch import RULES
from src.utils.random_utils import parse as parse_dist

_VALID_RULES = set(RULES)

# parameters.py 에 없는 새 설정의 기본값 (기존 동작 유지). parameters.py 에 같은 이름이 있으면 그 값이 우선.
#   URGENT_BATCH_MAX_WAIT_TIME : None = 긴급·일반 부품을 같은 배치에 섞음 (기존)
#                                분포   = 긴급 부품은 긴급 전용 배치로 모으고 이 대기 한도(T 조건)를 적용
DEFAULTS = {"URGENT_BATCH_MAX_WAIT_TIME": None}
_DIST_KINDS = {"const", "tri", "uniform", "exp"}
_EQUIP_KEYS = {"mtbf", "repair", "pm_every", "pm", "pm_offsets"}


def _check_dists(name, val):
    """값 안의 분포 튜플(중첩 dict 포함)을 모두 형식 검사."""
    if isinstance(val, tuple) and val and val[0] in _DIST_KINDS:
        try:
            parse_dist(val)
        except ValueError as e:
            raise ValueError(f"{name}: {e}") from None
    elif isinstance(val, dict):
        for k, v in val.items():
            _check_dists(f"{name}.{k}", v)


@dataclass(frozen=True)
class SimConfig:
    values: dict = field(default_factory=dict)

    @classmethod
    def from_parameters(cls):
        """parameters.py 의 대문자 변수 전체를 읽어 설정 객체 생성."""
        vals = copy.deepcopy(DEFAULTS)
        vals.update({k: copy.deepcopy(getattr(P, k)) for k in dir(P) if k.isupper()})
        cfg = cls(vals)
        cfg.validate()
        return cfg

    def replace(self, **overrides):
        """일부 값을 바꾼 새 설정 객체 (원본 불변). 없는 이름을 넣으면 오타로 보고 오류."""
        unknown = set(overrides) - set(self.values)
        if unknown:
            raise KeyError(f"알 수 없는 파라미터: {sorted(unknown)}")
        vals = dict(self.values)
        vals.update(overrides)
        cfg = SimConfig(vals)
        cfg.validate()
        return cfg

    def __getattr__(self, name):
        try:
            return self.values[name]
        except KeyError:
            raise AttributeError(name) from None

    # ------------------------------------------------------------ 파생값
    @property
    def arrival_rate_per_week(self):
        """λ [건/주]: ORDER_ARRIVAL_RATE_PER_WEEK 가 있으면 그 값, 없으면 SCENARIO 값."""
        if self.ORDER_ARRIVAL_RATE_PER_WEEK is not None:
            return float(self.ORDER_ARRIVAL_RATE_PER_WEEK)
        return float(self.SCENARIO_ARRIVAL_RATES[self.SCENARIO])

    # ------------------------------------------------------------ 검증
    def validate(self):
        """실행 전 설정 일관성 검사 (잘못된 조합은 조용히 넘어가지 않고 바로 알림)."""
        v = self.values
        for k, val in v.items():
            _check_dists(k, val)
        if int(v["VPP_PRINTER_COUNT"]) < 0:                # 프린터 0대 허용 (Level 5 극한 조건: 생산량 0)
            raise ValueError(f"VPP_PRINTER_COUNT 는 0 이상이어야 함 (현재 {v['VPP_PRINTER_COUNT']})")
        for k in ("WASHING_MACHINE_COUNT", "UV_CURING_MACHINE_COUNT",
                  "JOB_ASSIGNMENT_WORKER_COUNT", "POST_PROCESS_WORKER_COUNT", "QUALITY_INSPECTOR_COUNT",
                  "WASHING_LOAD_CAPACITY", "UV_CURING_LOAD_CAPACITY"):
            if int(v[k]) < 1:
                raise ValueError(f"{k} 는 1 이상이어야 함 (현재 {v[k]})")
        for k in ("PACKER_COUNT", "PRINTER_OPERATOR_COUNT"):
            if int(v[k]) < 0:
                raise ValueError(f"{k} 는 0 이상")
        if v["ORDER_SOURCE"] not in ("csv", "random"):
            raise ValueError("ORDER_SOURCE 는 'csv' 또는 'random'")
        # random 모드는 주문마다 면적·높이·납기 여유·수량을 항상 추출 -> 분포가 반드시 있어야 함
        # (재출력 형상을 새로 추출하는 경우도 면적·높이 분포 사용)
        need = []
        if v["ORDER_SOURCE"] == "random":
            need += ["ORDER_AREA_MM2", "ORDER_HEIGHT_MM", "ORDER_QUANTITY",
                     "DUE_DATE_SLACK_NORMAL", "DUE_DATE_SLACK_URGENT"]
        if v["REWORK_ENABLED"] and not v["REWORK_SAME_GEOMETRY"]:
            need += ["ORDER_AREA_MM2", "ORDER_HEIGHT_MM"]
        for k in dict.fromkeys(need):
            if not (isinstance(v[k], tuple) and v[k] and v[k][0] in _DIST_KINDS):
                raise ValueError(f"{k}: 분포 정의 필요 (현재 {v[k]!r}) — random 모드/재출력 새 추출에서 사용")
        if v["ORDER_ARRIVAL_RATE_PER_WEEK"] is None and v["SCENARIO"] not in v["SCENARIO_ARRIVAL_RATES"]:
            raise ValueError(f"SCENARIO '{v['SCENARIO']}' 가 SCENARIO_ARRIVAL_RATES 에 없음")
        missing = [p for p in PROCESSES if p not in v["LOCATIONS"]]
        if missing:
            raise ValueError(f"LOCATIONS 에 공정 위치가 없음: {missing}")
        if v["DEFAULT_SCHEDULING_RULE"] not in _VALID_RULES:
            raise ValueError(f"DEFAULT_SCHEDULING_RULE 는 {sorted(_VALID_RULES)} 중 하나")
        if v["VPP_BUILD_TIME_MODE"] not in ("fixed", "height"):
            raise ValueError("VPP_BUILD_TIME_MODE 는 'fixed' 또는 'height'")
        if v["BUILD_PLATE_MAX_PARTS"] is None and v["BUILD_PLATE_AREA_MM2"] is None \
                and v["BATCH_MAX_WAIT_TIME"] is None:
            raise ValueError("배치 확정 조건이 하나도 없음 (부품 수·면적·대기시간 중 하나는 필요)")
        if v["BATCH_FILL_RATIO"] is not None and v["BUILD_PLATE_AREA_MM2"] is None:
            raise ValueError("BATCH_FILL_RATIO 를 쓰려면 BUILD_PLATE_AREA_MM2 가 필요")
        for k in ("PRINT_FAILURE_RATE", "INSPECTION_FAILURE_RATE"):
            if not 0.0 <= v[k] < 1.0:
                raise ValueError(f"{k} 는 0 이상 1 미만")
        if v["BREAKDOWN_ENABLED"]:
            for eq in ("printer", "washing", "uv_curing"):
                p = v["EQUIPMENT_FAILURE"].get(eq)
                if p is None or set(p) != _EQUIP_KEYS:
                    raise ValueError(f"EQUIPMENT_FAILURE['{eq}'] 는 {sorted(_EQUIP_KEYS)} 키가 모두 필요")
                if p["mtbf"] <= 0 or p["pm_every"] <= 0:
                    raise ValueError(f"EQUIPMENT_FAILURE['{eq}']: mtbf, pm_every 는 양수")
        if v["CLEANING_LIQUID_CHANGE_EVERY_LOADS"] is not None and v["CLEANING_LIQUID_CHANGE_EVERY_LOADS"] < 1:
            raise ValueError("CLEANING_LIQUID_CHANGE_EVERY_LOADS 는 1 이상 또는 None")
        if v["USE_WORK_CALENDAR"]:
            for s, e in v["WORK_WINDOWS"]:
                if not 0 <= s < e <= 24:
                    raise ValueError(f"WORK_WINDOWS 구간 오류: {(s, e)}")
