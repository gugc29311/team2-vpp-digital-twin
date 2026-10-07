# -*- coding: utf-8 -*-
"""
이벤트 로그: 시뮬레이션에서 일어난 모든 일을 한 줄씩 기록 -> KPI 계산 · CSV 저장 · 화면 추적.

  events : Event(sim_time, order_id, entity_type, entity_id, event, process, location, state, resource, detail)
           [명세서 10절 권장 형식 + resource, detail] — 이름으로 접근 (e.sim_time, e.event ...)
  busy   : (resource, start, end, work_hours)  자원 점유 구간 — 가동률 계산용 (역할 단위)
  person_busy : (worker_id, role, start, end, work_hours)  작업자 개인 점유 구간 — 개인 가동률·겹침 검사용

  order_id : 관련 주문 (배치·로드는 여러 주문을 ';' 로 연결, 설비 이벤트는 빈칸)
  process  : 명세서 8.1절 공정명 / location : parameters.LOCATIONS 의 방 이름 (가정값)
  state    : 이벤트 직후 대상 상태 (src/analysis/event_schema.py)

화면 출력(verbose)은 '시작/종료'를 명시해서 어느 시점 기록인지 헷갈리지 않게 한다.
"""
import csv
import os
from collections import namedtuple

COLUMNS = ("sim_time", "order_id", "entity_type", "entity_id", "event", "process", "location", "state",
           "resource", "detail")
Event = namedtuple("Event", COLUMNS)


class EventLog:
    COLUMNS = COLUMNS

    def __init__(self, verbose=False, keep_events=True):
        self.events = []
        self.busy = []
        self.person_busy = []
        self.verbose = verbose
        self.keep_events = keep_events     # False: 이벤트는 버리고 점유 구간만 보관 (장기·반복 실험 메모리 절약)

    @property
    def active(self):
        """이벤트를 보관하거나 화면에 출력하는지 (False 면 호출 쪽에서 상세 값 계산을 건너뛰어도 됨)."""
        return self.keep_events or self.verbose

    def add(self, time, entity_type, entity_id, event, resource="", detail="",
            order_id="", process="", location="", state=""):
        if self.keep_events:
            self.events.append(Event(round(time, 6), order_id, entity_type, entity_id, event, process, location,
                                     state, resource, detail))
        if self.verbose:
            extra = f" [{resource}]" if resource else ""
            extra += f" {detail}" if detail else ""
            print(f"{time:8.2f}h | {entity_type:<5} {entity_id:<14} | {event}{extra}")

    def add_busy(self, resource, start, end, work_hours):
        """
        work_hours = 그 점유가 실제로 소비한 근무시간(작업시간).
        KPI 는 (start, end) 로 계산하므로 읽지 않지만, '사람 작업은 근무시간만 소비한다'는 캘린더 규칙을
        검증하는 데 쓰는 값 (tests/test_smoke.py: work_hours(start, end) == work_hours) — 지우지 말 것.
        """
        self.busy.append((resource, start, end, work_hours))

    def add_person_busy(self, worker_id, role, start, end, work_hours):
        """작업자 개인 점유 구간 (keep_events 와 무관하게 기록 — 개인 가동률 KPI)."""
        self.person_busy.append((worker_id, role, start, end, work_hours))

    def to_csv(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:     # 엑셀에서 한글 깨짐 방지
            w = csv.writer(f)
            w.writerow(self.COLUMNS)
            w.writerows(self.events)
