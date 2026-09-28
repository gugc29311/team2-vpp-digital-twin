# -*- coding: utf-8 -*-
"""
주문 입력.

  load_orders(path)   : CSV -> Order 리스트 (검증 모드). 엑셀 저장 CSV(BOM) 도 읽도록 utf-8-sig 사용.
  random_order(...)   : 확률적 주문 1건 생성 (random 모드). 분포는 parameters.py 의 ORDER_* 사용.

CSV 필수 열: order_id, product_id, arrival_time, quantity, material, due_date, priority
CSV 선택 열: area_mm2, height_mm  (면적 기준 배치 / height 출력시간 모드에서는 필수)
"""
import csv

from src.entities.order import Order
from src.utils.random_utils import sample

REQUIRED = ("order_id", "product_id", "arrival_time", "quantity", "material", "due_date", "priority")


def _opt_float(row, key):
    v = (row.get(key) or "").strip()
    return float(v) if v else None


def load_orders(file_path):
    orders, seen = [], set()
    with open(file_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        header = [h.strip() for h in (reader.fieldnames or [])]
        missing = [c for c in REQUIRED if c not in header]
        if missing:
            raise ValueError(f"{file_path}: 필수 열 누락 {missing} (현재 열 {header})")
        for line_no, raw in enumerate(reader, start=2):
            row = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}
            try:
                order = Order(
                    order_id=row["order_id"],
                    product_id=row["product_id"],
                    arrival_time=float(row["arrival_time"]),
                    quantity=int(row["quantity"]),
                    material=row["material"],
                    due_date=float(row["due_date"]),
                    priority=row["priority"] or "NORMAL",
                    area_mm2=_opt_float(row, "area_mm2"),
                    height_mm=_opt_float(row, "height_mm"),
                )
            except (ValueError, TypeError) as e:
                raise ValueError(f"{file_path} {line_no}행: {e}") from None
            if order.order_id in seen:
                raise ValueError(f"{file_path} {line_no}행: order_id 중복 {order.order_id}")
            seen.add(order.order_id)
            orders.append(order)
    return sorted(orders, key=lambda o: o.arrival_time)


def check_orders_for_config(orders, cfg):
    """설정이 요구하는 주문 속성이 CSV 에 있는지 확인 (실행 도중이 아니라 시작 전에 실패하도록)."""
    need_area = cfg.BUILD_PLATE_AREA_MM2 is not None
    need_height = cfg.VPP_BUILD_TIME_MODE == "height"
    for o in orders:
        if need_area and o.area_mm2 is None:
            raise ValueError(f"{o.order_id}: 면적 기준 배치를 쓰려면 area_mm2 필요")
        if need_height and o.height_mm is None:
            raise ValueError(f"{o.order_id}: height 출력시간 모드에는 height_mm 필요")
        if need_area and o.area_mm2 > cfg.BUILD_PLATE_AREA_MM2:
            raise ValueError(f"{o.order_id}: 부품 면적 {o.area_mm2} > 플레이트 면적 {cfg.BUILD_PLATE_AREA_MM2}")


def random_order(idx, now, cfg, rng, cal):
    """
    random 모드 주문 1건.
    난수 소비 순서: 면적 -> 높이 -> 재료 -> 긴급 여부 -> 납기 여유 -> 수량 (순서를 바꾸면 결과 재현이 깨짐)
    납기 = 도착 후 '근무시간' slack 이 지난 시각 [공통7: 근무일 기준]
    """
    area = sample(cfg.ORDER_AREA_MM2, rng)
    height = sample(cfg.ORDER_HEIGHT_MM, rng)
    mats = list(cfg.ORDER_MATERIALS)
    if len(mats) == 1:
        material = mats[0]
    else:
        probs = [cfg.ORDER_MATERIALS[m] for m in mats]
        material = mats[int(rng.choice(len(mats), p=[p / sum(probs) for p in probs]))]
    urgent = cfg.URGENT_PROBABILITY > 0 and rng.random() < cfg.URGENT_PROBABILITY
    slack = sample(cfg.DUE_DATE_SLACK_URGENT if urgent else cfg.DUE_DATE_SLACK_NORMAL, rng)
    qty = max(1, int(round(sample(cfg.ORDER_QUANTITY, rng))))
    return Order(
        order_id=f"R{idx:06d}", product_id="RANDOM", arrival_time=now, quantity=qty, material=material,
        due_date=cal.add_work_hours(now, slack), priority="URGENT" if urgent else "NORMAL",
        area_mm2=area, height_mm=height,
    )
