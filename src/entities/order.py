# -*- coding: utf-8 -*-
"""
주문(Order)과 부품(Part).

  Order : 고객 주문 1건 (data/sample_orders.csv 한 줄). quantity 개의 Part 로 나뉜다.
  Part  : 실제로 빌드플레이트에 올라가고 공정을 도는 단위. 재출력되면 새 Part(gen+1)가 생긴다.
  주문은 '양품 부품 수 + 폐기 부품 수 == quantity' 가 되면 종료된다.

시간 규약 (CSV)
  arrival_time, due_date 는 시뮬레이션 시각(t=0 기준, hour) 절대값.
  USE_WORK_CALENDAR = True 이면 t 는 달력시간 (t=0 = 월요일 첫 근무 시작).
"""
from dataclasses import dataclass, field
from typing import List, Optional

PRIORITIES = ("NORMAL", "URGENT")


@dataclass
class Order:
    order_id: str
    product_id: str
    arrival_time: float
    quantity: int
    material: str
    due_date: float
    priority: str = "NORMAL"
    area_mm2: Optional[float] = None       # 부품 1개 바닥 면적 (면적 기준 배치에 필요)
    height_mm: Optional[float] = None      # 부품 1개 높이 (height 출력시간 모드에 필요)

    # 시뮬레이션 중 채워지는 상태
    parts_good: int = field(default=0, init=False)
    parts_scrapped: int = field(default=0, init=False)
    reworks: int = field(default=0, init=False)        # 이 주문에서 발생한 재출력 횟수
    received_time: Optional[float] = field(default=None, init=False)
    completed_time: Optional[float] = field(default=None, init=False)

    def __post_init__(self):
        self.priority = str(self.priority).strip().upper()
        if self.priority not in PRIORITIES:
            raise ValueError(f"{self.order_id}: priority 는 {PRIORITIES} 중 하나 (입력 {self.priority!r})")
        if int(self.quantity) < 1:
            raise ValueError(f"{self.order_id}: quantity 는 1 이상")
        self.quantity = int(self.quantity)
        if self.arrival_time < 0:
            raise ValueError(f"{self.order_id}: arrival_time 은 0 이상")
        if self.due_date < self.arrival_time:
            raise ValueError(f"{self.order_id}: due_date({self.due_date}) < arrival_time({self.arrival_time})")
        for name in ("area_mm2", "height_mm"):
            v = getattr(self, name)
            if v is not None and v <= 0:
                raise ValueError(f"{self.order_id}: {name} 는 양수")

    @property
    def is_urgent(self):
        return self.priority == "URGENT"

    @property
    def is_done(self):
        return self.parts_good + self.parts_scrapped == self.quantity

    @property
    def status(self):
        if not self.is_done:
            return "IN_PROGRESS"
        return "COMPLETED" if self.parts_scrapped == 0 else "SHORT"   # SHORT = 일부 폐기


@dataclass
class Part:
    part_id: str                  # 예: O001-1, 재출력이면 O001-1-R1
    order: Order
    area_mm2: Optional[float]
    height_mm: Optional[float]
    gen: int = 0                  # 재출력 횟수
    resin_mm3: Optional[float] = None   # 출력 시 소모한 레진 (RESIN_TRACKING)
    base_id: Optional[str] = None       # 최초 부품 번호 (재출력돼도 유지) — 문자열 파싱 없이 재출력 번호 생성

    def __post_init__(self):
        if self.base_id is None:
            self.base_id = self.part_id

    @property
    def material(self):
        return self.order.material

    def reprint(self, area_mm2, height_mm):
        """재출력 부품: 번호 = 최초 번호 + -R{재출력 횟수}. 주문 번호에 어떤 문자가 있어도 안전."""
        gen = self.gen + 1
        return Part(f"{self.base_id}-R{gen}", self.order, area_mm2, height_mm, gen, base_id=self.base_id)


def make_parts(order: Order) -> List[Part]:
    return [Part(f"{order.order_id}-{i + 1}", order, order.area_mm2, order.height_mm)
            for i in range(order.quantity)]
