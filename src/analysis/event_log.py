# -*- coding: utf-8 -*-
"""
이벤트 로그: 시뮬레이션에서 일어난 모든 일을 한 줄씩 기록 -> KPI 계산 · CSV 저장 · 화면 추적.

  events : (time, entity_type, entity_id, event, resource, detail)
  busy   : (resource, start, end, work_hours)  자원 점유 구간 — 가동률 계산용

화면 출력(verbose)은 '시작/종료'를 명시해서 어느 시점 기록인지 헷갈리지 않게 한다.
"""
import csv
import os


class EventLog:
    COLUMNS = ("time", "entity_type", "entity_id", "event", "resource", "detail")

    def __init__(self, verbose=False, keep_events=True):
        self.events = []
        self.busy = []
        self.verbose = verbose
        self.keep_events = keep_events     # False: 이벤트는 버리고 점유 구간만 보관 (장기·반복 실험 메모리 절약)

    def add(self, time, entity_type, entity_id, event, resource="", detail=""):
        if self.keep_events:
            self.events.append((round(time, 6), entity_type, entity_id, event, resource, detail))
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

    def to_csv(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:     # 엑셀에서 한글 깨짐 방지
            w = csv.writer(f)
            w.writerow(self.COLUMNS)
            w.writerows(self.events)
