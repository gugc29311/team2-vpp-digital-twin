# -*- coding: utf-8 -*-
"""
Replay 화면 데이터 검사: 3D 화면에 넘기는 설비 상태가 시각 조회(state_at = 터미널 --at)와 같은지.
(실시간 모드에서 상태 번호표가 달라 Running <-> Idle 이 뒤바뀌었던 버그의 회귀 방지)
+ 세척기·UV기 안의 부품이 Replay 방별 숫자에서 '작업 중'으로 세어지는지, requirements.txt 가 한국어 Windows 에서 읽히는지
"""
import pytest

from dashboard import data
from dashboard.replay3d import build_payload
from src.logger.state import state_at
from src.model.config import SimConfig
from src.model.simulation import VPPSimulation


@pytest.fixture(scope="module")
def res():
    cfg = SimConfig.from_parameters().replace(ORDER_SOURCE="random", WARMUP_TIME=0, SIMULATION_TIME=2 * 168,
                                              RANDOM_SEED=42)
    return VPPSimulation(cfg).run()


def _state_in(segs, t):
    hit = [g for g in segs if g[0] <= t < g[1]]
    return hit[-1][2] if hit else None


@pytest.mark.parametrize("t0", [25.0, 26.5, 50.0])
def test_realtime_machine_states_match_state_at(res, t0):
    states = data.replay_states(res, data.replay_times_window(t0, 1, 1, res.end_time))
    tracks = data.replay_tracks(res, t0, t0 + 1)
    p = build_payload(states, tracks=tracks)
    names = [s["key"] for s in p["states"]]
    for k in range(0, 60, 3):
        t = t0 + k / 60 + 1e-4
        truth = state_at(res, t)["machines"]
        for i, m in enumerate(p["machines"]):
            idx = _state_in(p["rt"]["machines"][i], t)
            if idx is not None:
                assert names[idx] == truth[m["id"]], (m["id"], t)


def test_frame_machine_states_match_state_at(res):
    states = data.replay_states(res, data.replay_times_all(60, 48))
    p = build_payload(states)
    names = [s["key"] for s in p["states"]]
    for fr, s in zip(p["frames"], states):
        truth = state_at(res, s["t"])["machines"]
        assert [names[i] for i in fr["m"]] == [truth[m["id"]] for m in p["machines"]]


def test_parts_inside_running_machine_are_processing(res):
    """세척기·UV기 적재 완료(LOADING_END) ~ 인출 전 부품 = Processing (예전에는 Waiting 으로 보여 대기를 부풀렸음)."""
    checked = 0
    for t in [24 + k * 0.25 for k in range(40)]:
        st = state_at(res, t)
        for o in st["orders"].values():
            if o["event"] == "LOADING_END":
                assert o["state"] == "Processing"
                checked += 1
    assert checked > 0



def test_room_counts_show_parts_inside_machine_as_processing(res):
    """화면 숫자까지: Day 2 10:28 UV1 이 로드 B00003-U2 (14개) 경화 중 -> UV실 '작업 14' (예전 화면: '작업 0 · 대기 59')."""
    uv = data.replay_states(res, [25.0 + 28 / 60])[0]["rooms"]["UV Room"]
    assert uv["processing"] == 14


def test_requirements_readable_on_korean_windows():
    """pip 은 한국어 Windows 에서 requirements.txt 를 cp949 로 읽음 -> UTF-8 한글 주석이 있으면 설치 실패."""
    with open("requirements.txt", "rb") as f:
        f.read().decode("ascii")
