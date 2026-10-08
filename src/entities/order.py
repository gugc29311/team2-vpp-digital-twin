# -*- coding: utf-8 -*-
"""
주문(Order)과 부품(Part) [명세서 4절].

단위 정의 (명세서 4절 'Customer Order -> Job -> Batch/Build -> Printing')
  Customer Order = Order : 고객 주문 1건 (data/sample_orders.csv 한 줄). quantity 개의 Part 로 나뉜다.
  Job            = Part  : 빌드플레이트에 올라가 공정을 도는 부품 1개. 재출력되면 새 Part(gen+1)가 생긴다.
  Batch/Build    = Batch : 빌드플레이트 1장 (src/entities/batch.py). 여러 주문의 Part 가 함께 올라간다.
  Printing               : 배치 단위 출력 (src/model/simulation.py _batch_flow).
  주문은 '양품 부품 수 + 폐기 부품 수 == quantity' 가 되면 종료된다.

명세서 4절 속성 -> 필드
  Order ID · Product ID · Arrival Time · Quantity · Material · Due Date · Priority
      -> order_id, product_id, arrival_time, quantity, material, due_date, priority
  Part Size / Volume   -> area_mm2, height_mm (부품 1개 기준. 레진 부피는 출력 시 Part.resin_mm3)
  Required Process     -> required_process : 이 주문에 필요한 후공정 (POST_PROCESSES 중). 생략 = 전체
  Estimated Build Time -> estimated_build_time [h] : 이 주문 부품 1개를 단독 출력한다고 볼 때의 예상 출력시간.
                          접수 시 설정값으로 계산 (order_source.estimate_build_time). 실제 출력시간은 같은 배치의
                          최대 높이로 정해지므로 이보다 길 수 있다.
  Current State        -> status (IN_PROGRESS / COMPLETED / SHORT) + current_process, current_state
                          (그 주문이 가장 최근에 움직인 공정과 상태 — 이벤트 로그 state_at 과 같은 규칙)

시간 규약 (CSV)
  arrival_time, due_date 는 시뮬레이션 시각(t=0 기준, hour) 절대값.
  USE_WORK_CALENDAR = True 이면 t 는 달력시간 (t=0 = 월요일 첫 근무 시작).
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

PRIORITIES = ("NORMAL", "URGENT")

# 후공정 (명세서 8.1절 Route 중 출력 이후)
POST_PROCESSES = ("Part Removal", "Washing", "UV Curing", "Support Removal", "Surface Treatment",
                  "Inspection", "Packaging")
# VPP 에서 생략할 수 없는 후공정: 탈거, 미경화 레진 제거(세척)·후경화(UV), 검사, 포장
MANDATORY_POST_PROCESSES = ("Part Removal", "Washing", "UV Curing", "Inspection", "Packaging")
# 주문에 따라 생략 가능한 후공정 (서포트 없이 출력한 부품, 표면처리 불필요한 부품) ⚠️ 인터뷰 확인
OPTIONAL_POST_PROCESSES = ("Support Removal", "Surface Treatment")


def parse_required_process(value, order_id="") -> Tuple[str, ...]:
    """
    required_process 입력 -> 후공정 tuple (POST_PROCESSES 순서).
      None / "" / "ALL" -> 전체 후공정
      "Part Removal;Washing;..." 또는 목록 -> 그 공정들. 필수 후공정이 빠지면 오류.
    """
    if value is None or (isinstance(value, str) and value.strip().upper() in ("", "ALL")):
        return POST_PROCESSES
    items = value.split(";") if isinstance(value, str) else list(value)
    items = [str(x).strip() for x in items if str(x).strip()]
    unknown = [x for x in items if x not in POST_PROCESSES]
    if unknown:
        raise ValueError(f"{order_id}: required_process 에 알 수 없는 공정 {unknown} (가능: {list(POST_PROCESSES)})")
    missing = [x for x in MANDATORY_POST_PROCESSES if x not in items]
    if missing:
        raise ValueError(f"{order_id}: required_process 에 필수 후공정 {missing} 누락 "
                         f"(생략 가능한 것은 {list(OPTIONAL_POST_PROCESSES)} 뿐)")
    return tuple(p for p in POST_PROCESSES if p in items)


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
    required_process: Optional[Tuple[str, ...]] = None   # 필요한 후공정. None = 전체 (POST_PROCESSES)

    # 시뮬레이션 중 채워지는 상태
    estimated_build_time: Optional[float] = field(default=None, init=False)   # [h] 접수 시 계산
    current_process: str = field(default="", init=False)    # 가장 최근에 움직인 공정 (명세서 8.1절 공정명)
    current_state: str = field(default="", init=False)      # 그 공정에서의 상태 (Waiting/Processing/Moving/Done …)
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
        self.required_process = parse_required_process(self.required_process, self.order_id)

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

    def needs(self, process):
        """이 주문에 해당 후공정이 필요한지."""
        return process in self.required_process


@dataclass
class Part:
    part_id: str                  # 예: O001-1, 재출력이면 O001-1-R1
    order: Order
    area_mm2: Optional[float]
    height_mm: Optional[float]
    gen: int = 0                  # 재출력 횟수
    resin_mm3: Optional[float] = None   # 출력 시 소모한 레진 (RESIN_TRACKING)
    base_id: Optional[str] = None       # 최초 부품 번호 (재출력돼도 유지) — 문자열 파싱 없이 재출력 번호 생성
    batch: Optional[object] = None      # 들어간 배치 (Batch) — 주문 단계별 시간 KPI 용 기록
    batched_time: Optional[float] = None   # 배치에 들어간 시각 (= 작업 배정 완료)

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
