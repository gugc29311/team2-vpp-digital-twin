# -*- coding: utf-8 -*-
"""근무 캘린더 단위 테스트 (t=0 = 월 09:00, 근무 09~12·13~18, 월~금)."""
import pytest
import simpy

from src.utils.calendar import WorkCalendar

CAL = WorkCalendar(5, [(9, 12), (13, 18)])
T = lambda day, hour: day * 24 + hour - 9          # 요일(월=0)·시각 -> t


@pytest.mark.parametrize("t, is_open, wait", [
    (T(0, 9), True, 0),            # 월 09:00
    (T(0, 17), True, 0),           # 월 17:00
    (T(0, 18), False, 15),         # 월 18:00 -> 화 09:00
    (T(0, 12), False, 1),          # 점심
    (T(4, 18), False, 63),         # 금 18:00 -> 월 09:00
    (T(5, 10), False, 47),         # 토 10:00
    (T(7, 9), True, 0),            # 다음 주 월 09:00
])
def test_open_and_wait(t, is_open, wait):
    assert CAL.is_open(t) is is_open
    assert CAL.until_open(t) == pytest.approx(wait)


def test_work_hours():
    assert CAL.work_hours(0, 168) == pytest.approx(40)
    assert CAL.work_hours(0, T(1, 10)) == pytest.approx(9)
    assert CAL.hours_per_week == pytest.approx(40)


@pytest.mark.parametrize("start, h, end", [
    (T(0, 9), 8, T(1, 9)),          # 월 09:00 + 8h -> 화 09:00 (월 18:00 과 같은 근무시각, 다음 근무 시작으로 표기)
    (T(0, 17), 3, T(1, 11)),
    (T(4, 17), 2, T(7, 10)),
    (T(0, 11), 2, T(0, 14)),
    (T(5, 10), 1, T(7, 10)),        # 토요일 도착 -> 월 10:00
])
def test_add_work_hours(start, h, end):
    got = CAL.add_work_hours(start, h)
    assert CAL.work_hours(start, got) == pytest.approx(h)
    if h and got != pytest.approx(end):
        # 근무 구간 끝(18:00)과 다음 근무 시작(09:00)은 근무시간상 같은 시각 — 둘 중 하나면 정답
        assert CAL.work_hours(got, end) == pytest.approx(0) or CAL.work_hours(end, got) == pytest.approx(0)


@pytest.mark.parametrize("start, dur, end", [
    (T(0, 17), 3, T(1, 11)),       # 퇴근 넘김
    (T(4, 17), 2, T(7, 10)),       # 주말 넘김
    (T(0, 11), 2, T(0, 14)),       # 점심 건너뜀
])
def test_delay_consumes_only_work_hours(start, dur, end):
    env = simpy.Environment()
    done = {}

    def p():
        yield env.timeout(start)
        yield from CAL.delay(env, dur)
        done["t"] = env.now

    env.process(p())
    env.run()
    assert done["t"] == pytest.approx(end)
