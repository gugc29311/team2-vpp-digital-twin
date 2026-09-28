# -*- coding: utf-8 -*-
"""
근무 캘린더.

  AlwaysOpen   : 24시간 연속 (USE_WORK_CALENDAR = False). 모든 대기가 env.timeout 과 동일.
  WorkCalendar : 근무일·근무구간이 있는 캘린더. t=0 = 월요일 첫 근무 시작 시각(예: 09:00).
                 사람 작업은 delay() 로 '근무시간'만 소비 -> 퇴근하면 멈췄다가 다음 근무시간에 이어서 진행.

공통 인터페이스 (SimPy 프로세스 안에서 `yield from` 으로 사용)
  delay(env, hours)   : 근무시간 hours 만큼 소비
  wait_open(env)      : 근무시간이 될 때까지 대기
  work_hours(t0, t1)  : [t0, t1] 사이 근무시간 합 (가동률 분모·리드타임 환산용)
  add_work_hours(t, h): t 로부터 근무시간 h 가 지난 시각 (근무일 기준 납기 계산용)
  hours_per_week      : 주당 근무시간 (λ[건/주] -> 도착간격 변환용)
"""


class AlwaysOpen:
    hours_per_week = 40.0      # 24시간 연속 시계에서도 λ[건/주]는 '주 40 근무시간' 기준 (1번 정의)

    def delay(self, env, hours):
        if hours > 0:
            yield env.timeout(hours)

    def wait_open(self, env):
        return
        yield  # 제너레이터로 만들기 위한 문법 (도달하지 않음)

    def is_open(self, t):
        return True

    def work_hours(self, t0, t1):
        return max(0.0, t1 - t0)

    def add_work_hours(self, t, hours):
        return t + hours


class WorkCalendar:
    WEEK_H = 168.0
    EPS = 1e-9

    def __init__(self, work_days, windows):
        self.work_days = int(work_days)
        self.windows = sorted((float(s), float(e)) for s, e in windows)
        self.day_h = sum(e - s for s, e in self.windows)
        self.hours_per_week = self.work_days * self.day_h
        self.offset = self.windows[0][0]            # t=0 = 월요일 첫 근무 시작 -> 월 00:00 기준 보정

    def _pos(self, t):
        """시뮬 시각 -> (요일 0=월, 그날의 시각)."""
        h = round((t + self.offset) % self.WEEK_H, 9) % self.WEEK_H
        day = int(h // 24)
        return day, h - day * 24

    def is_open(self, t):
        day, hod = self._pos(t)
        return day < self.work_days and any(s <= hod < e for s, e in self.windows)

    def until_open(self, t):
        """근무시간이면 0, 아니면 다음 근무 시작까지 남은 시간."""
        if self.is_open(t):
            return 0.0
        day, hod = self._pos(t)
        if day < self.work_days:
            for s, _ in self.windows:
                if hod < s:
                    return s - hod
            if day < self.work_days - 1:
                return 24 - hod + self.windows[0][0]
        return (7 - day) * 24 - hod + self.windows[0][0]

    def left_in_window(self, t):
        _, hod = self._pos(t)
        for s, e in self.windows:
            if s <= hod < e:
                return e - hod
        return 0.0

    def delay(self, env, hours):
        while hours > self.EPS:
            wait = self.until_open(env.now)
            if wait > self.EPS:
                yield env.timeout(wait)
                continue
            step = min(hours, self.left_in_window(env.now))
            if step <= self.EPS:
                yield env.timeout(self.EPS)
                continue
            yield env.timeout(step)
            hours -= step

    def wait_open(self, env):
        while True:
            wait = self.until_open(env.now)
            if wait <= self.EPS:
                return
            yield env.timeout(wait)

    def _cum(self, t):
        """월 00:00 부터 t(월 00:00 기준 시각)까지 누적 근무시간."""
        weeks, r = divmod(t, self.WEEK_H)
        day, hod = int(r // 24), r % 24
        total = weeks * self.work_days * self.day_h + min(day, self.work_days) * self.day_h
        if day < self.work_days:
            total += sum(max(0.0, min(hod, e) - s) for s, e in self.windows)
        return total

    def work_hours(self, t0, t1):
        return max(0.0, self._cum(t1 + self.offset) - self._cum(t0 + self.offset))

    def add_work_hours(self, t, hours):
        """t 이후 근무시간 hours 를 소비한 시각 (delay 와 같은 규칙, 시뮬레이션 없이 계산)."""
        while hours > self.EPS:
            wait = self.until_open(t)
            if wait > self.EPS:
                t += wait
                continue
            step = min(hours, self.left_in_window(t))
            if step <= self.EPS:
                t += self.EPS
                continue
            t += step
            hours -= step
        return t


def make_calendar(cfg):
    if cfg.USE_WORK_CALENDAR:
        return WorkCalendar(cfg.WORK_DAYS, cfg.WORK_WINDOWS)
    return AlwaysOpen()
