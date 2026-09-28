# -*- coding: utf-8 -*-
"""
배치(Batch): 빌드플레이트 1장에 함께 출력되는 부품 묶음.

VPP 는 한 층을 플레이트 전체에 동시에 경화하므로
  - 여러 주문의 부품을 한 배치로 모아 한 번에 출력하고
  - 출력시간은 부품 수가 아니라 배치 내 '최대 높이'로 정해진다 (height 모드).
"""
from dataclasses import dataclass, field
from typing import List, Optional

from src.entities.order import Part


@dataclass
class Batch:
    batch_id: str
    material: str
    created_time: float
    parts: List[Part] = field(default_factory=list)
    trigger: Optional[str] = None        # "PARTS" | "AREA" | "OVERFLOW" | "TIME"
    closed_time: Optional[float] = None
    build_time: Optional[float] = None
    print_start: Optional[float] = None
    print_end: Optional[float] = None

    @property
    def closed(self):
        return self.closed_time is not None

    @property
    def n_parts(self):
        return len(self.parts)

    @property
    def total_area(self):
        return sum(p.area_mm2 or 0.0 for p in self.parts)

    @property
    def max_height(self):
        hs = [p.height_mm for p in self.parts if p.height_mm is not None]
        return max(hs) if hs else None

    @property
    def earliest_due(self):
        return min(p.order.due_date for p in self.parts)
