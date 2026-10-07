# -*- coding: utf-8 -*-
"""
VPP Digital Twin 대시보드 초안 [명세서 11절 Dashboard · 13절 Factory Replay].

실행 (프로젝트 최상위 폴더에서):
  python -m streamlit run dashboard/app.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import math

import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from dashboard import data
from dashboard.replay import STATE_COLORS, STATE_LABELS, replay_figure
from dashboard.replay3d import replay3d_html
from src.analysis.kpi import MACHINE_STATES
from src.logger.state import DAY_NAMES, format_time
from src.model.config import SimConfig
from src.scheduler.dispatch import RULES

ASSUMPTION_NOTE = "가정값 기반 결과 (인터뷰 확인 전)"

QUEUE_SERIES = (("printer_queue_mean", "프린터", "#1e40af"), ("washing_queue_mean", "세척기", "#0d9488"),
                ("uv_queue_mean", "UV기", "#a16207"))


@st.cache_resource(show_spinner="시뮬레이션 실행 중...", max_entries=8)
def get_result(scenario, weeks, seed, rule):
    """같은 설정이면 다시 돌리지 않고 캐시된 SimulationResult 를 쓴다 (Replay·주문 추적용 이벤트 로그 포함)."""
    return data.run(scenario, weeks, seed, rule)


@st.cache_data(show_spinner="KPI 계산 중...")
def load(scenario, weeks, seed, rule):
    return data.summarize(get_result(scenario, weeks, seed, rule))


@st.cache_data(show_spinner="Replay 프레임 계산 중...", max_entries=32)
def load_replay(params, step_min):
    res = get_result(*params)
    return data.replay_states(res, data.replay_times_all(step_min, res.end_time))


def pct(x):
    return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.1%}"


def num(x, unit="", digits=1):
    return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{digits}f}{unit}"


def time_ticks(t_end):
    """x축 눈금: 하루 00:00 (기간이 길면 간격을 넓힘), 라벨은 format_time."""
    days = t_end / 24
    step = 12 if days <= 8 else (24 if days <= 15 else 48)
    vals, t = [0.0], 24 - 9.0                         # t=0 = Day 1 09:00 -> 첫 자정 = t 15
    while t <= t_end + 1e-9:
        if (t + 9) % step == 0:
            vals.append(t)
        t += 12
    return vals, [format_time(v) for v in vals]


def line_chart(df, cols, title, yfmt=None, height=260):
    fig = go.Figure()
    for col, name, color in cols:
        fig.add_trace(go.Scatter(x=df["day"], y=df[col], name=name, mode="lines+markers",
                                 line=dict(width=2, color=color), marker=dict(size=8)))
    fig.update_layout(title=title, height=height, margin=dict(l=10, r=10, t=40, b=10),
                      hovermode="x unified", showlegend=len(cols) > 1,
                      legend=dict(orientation="h", y=1.02, x=1, xanchor="right", yanchor="bottom"))
    fig.update_xaxes(title="일차 (Day)", dtick=1, showgrid=False)
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.2)", rangemode="tozero", tickformat=yfmt)
    return fig


def gantt(phases, units, t_end):
    fig = go.Figure()
    for state in MACHINE_STATES:
        d = phases[phases["state"] == state]
        if d.empty:
            continue
        fig.add_trace(go.Bar(
            y=d["unit"], x=d["duration"], base=d["start"], orientation="h",
            name=f"{state} ({STATE_LABELS[state]})", marker=dict(color=STATE_COLORS[state], line=dict(width=0)),
            customdata=d[["start_label", "end_label", "duration"]].to_numpy(),
            hovertemplate=f"<b>%{{y}}</b> · {state} ({STATE_LABELS[state]})<br>"
                          "%{customdata[0]} → %{customdata[1]}<br>%{customdata[2]:.2f}h<extra></extra>",
        ))
    vals, labels = time_ticks(t_end)
    fig.update_layout(barmode="overlay", height=120 + 60 * len(units), bargap=0.35,
                      margin=dict(l=10, r=10, t=30, b=10),
                      legend=dict(orientation="h", y=1.02, x=0, yanchor="bottom", traceorder="normal"))
    fig.update_xaxes(tickvals=vals, ticktext=labels, tickangle=-45, range=[0, t_end],
                     gridcolor="rgba(128,128,128,0.2)")
    fig.update_yaxes(categoryorder="array", categoryarray=list(reversed(units)))
    return fig


# ------------------------------------------------------------------ 화면
st.set_page_config(page_title="VPP 디지털 트윈 대시보드", layout="wide")
st.warning(f"⚠️ {ASSUMPTION_NOTE}")
st.title("VPP 디지털 트윈 대시보드")

default_rule = SimConfig.from_parameters().DEFAULT_SCHEDULING_RULE
rules = list(RULES)
with st.sidebar:
    st.header("실행 설정")
    scenario = st.selectbox("시나리오", data.SCENARIO_OPTIONS,
                            help="Rush Order 는 프리셋 (Normal 도착률 + 긴급 주문 비율 30%, 가정값)")
    weeks = st.slider("기간(주)", 1, 4, 1, help="워밍업 없이 N주 실행 (KPI 에 초기 빈 공장 상태 포함)")
    seed = st.number_input("시드", min_value=0, value=42, step=1)
    rule = st.selectbox("프린터 순서 규칙", rules, index=rules.index(default_rule))
    if st.button("실행", type="primary", width="stretch"):
        st.session_state["params"] = (scenario, int(weeks), int(seed), rule)

params = st.session_state.get("params")
if params is None:
    st.info("왼쪽 사이드바에서 설정을 고른 뒤 [실행] 을 누르세요.")
    st.stop()

s = load(*params)
k = s["kpis"]
p_scenario, p_weeks, p_seed, p_rule = params
st.caption(f"시나리오 **{p_scenario}** · 기간 **{p_weeks}주** · 시드 **{p_seed}** · 프린터 순서 규칙 **{p_rule}** · "
           f"도착률 λ {s['arrival_rate']:.1f}건/주 · 주문 {s['n_orders']}건 · 배치 {s['n_batches']}개 · "
           f"종료 {format_time(s['end_time'])}")
if params != (scenario, int(weeks), int(seed), rule):
    st.caption("사이드바 설정이 바뀌었습니다. [실행] 을 눌러야 결과에 반영됩니다.")

tab_kpi, tab_trend, tab_machine, tab_replay, tab_replay3d = st.tabs(["KPI 요약", "시간 추이", "장비 상태 타임라인",
                                                                          "2D 공장 Replay", "3D 공장 Replay"])

# ------------------------------------------------------------------ 1. KPI 요약
with tab_kpi:
    c = st.columns(4)
    c[0].metric("처리량", num(k["throughput_per_week"], "건/주"))
    c[1].metric("프린터 부하율 ρ", pct(k["printer_rho"]),
                help="주당 출력시간 / 유효 처리용량 (정의 B)")
    c[1].caption(f"유효 용량({s['printer_capacity_h']:.1f}h/주) 대비")
    c[2].metric("평균 WIP", num(k["wip_mean"], "건"))
    c[3].metric("재출력 비율", pct(k["rework_share_printed"]), help="출력된 부품 중 재출력 부품 비중")

    c = st.columns(4)
    c[0].metric("리드타임 (근무시간)", num(k["lead_work_h_mean"], "h", 2), help="도착→완료, 근무시간 기준 평균")
    c[1].metric("리드타임 (달력)", num(k["lead_calendar_h_mean"], "h", 2), help="도착→완료, 달력시간 기준 평균")
    c[2].metric("납기 준수율 (일반)", pct(k["on_time_normal"]))
    c[3].metric("납기 준수율 (긴급)", pct(k["on_time_urgent"]))

    st.subheader("자원별 가동률 · 평균 대기")
    bn_name, bn_val = s["bottleneck"]
    if bn_name:
        bn_label = data.RESOURCE_LABELS.get(bn_name, bn_name)
        basis = "부하율 ρ" if bn_name == "vpp_printers" else "가동률"
        st.error(f"🔺 병목 자원: **{bn_label}** — {basis} {bn_val:.1%}")

    res_df = s["resources"]

    def _highlight(row):
        on = row["resource"] == bn_name
        return ["background-color: rgba(220,38,38,0.15); font-weight: bold" if on else ""] * len(row)

    st.dataframe(
        res_df.style.apply(_highlight, axis=1).format(
            {"가동률": "{:.1%}", "병목 비교값": "{:.1%}", "평균 대기(h)": "{:.2f}", "평균 대기열": "{:.2f}"},
            na_rep="-"),
        hide_index=True, width="stretch", column_order=[c for c in res_df.columns if c != "resource"])
    st.caption("병목 비교값: 프린터는 부하율 ρ (유효 용량 대비), 나머지는 가동률. "
               "판정 기준 <50% 여유 · 50~70% 주의 · ≥70% 위험. "
               "프린터 가동률은 무인운전 시 달력시간 기준, 그 외 자원은 근무시간 기준.")
    with st.expander("작업자 개인별 가동률"):
        st.dataframe(s["workers"].style.format({"가동률": "{:.1%}"}), hide_index=True, width="stretch")

# ------------------------------------------------------------------ 2. 시간 추이
with tab_trend:
    daily = s["daily"]
    if daily.empty:
        st.info("일별 데이터가 없습니다.")
    else:
        a, b = st.columns(2)
        a.plotly_chart(line_chart(daily, [("throughput", "처리량", "#1e40af")], "일별 처리량 (완료 주문 수)"),
                       width="stretch")
        b.plotly_chart(line_chart(daily, [("wip_mean", "WIP", "#1e40af")], "일별 평균 WIP (시스템 안 주문 수)"),
                       width="stretch")
        a, b = st.columns(2)
        a.plotly_chart(line_chart(daily, QUEUE_SERIES, "일별 평균 대기열 길이 (배치·로드 수)"),
                       width="stretch")
        b.plotly_chart(line_chart(daily, [("printer_util", "프린터 가동률", "#1e40af")],
                                  "일별 프린터 가동률 (달력시간 기준)", yfmt=".0%"),
                       width="stretch")
        with st.expander("일별 표"):
            st.dataframe(daily, hide_index=True, width="stretch")

# ------------------------------------------------------------------ 3. 장비 상태 타임라인
with tab_machine:
    st.plotly_chart(gantt(s["phases"], s["units"], s["end_time"]), width="stretch")
    st.caption("Waiting 에는 근무시간 밖에서 멈춘 세척·UV 처리(무인운전이 아닌 프린터 포함)도 들어갑니다. "
               "차트 위에서 드래그하면 확대, 더블클릭하면 원래 범위.")
    st.subheader("설비별 상태 누적 시간 (h)")
    st.dataframe(s["state_hours"].rename(columns={k: f"{k} ({v})" for k, v in STATE_LABELS.items()}).style.format("{:.1f}"),
                 width="stretch")

# ------------------------------------------------------------------ 4. 2D 공장 Replay [명세서 13절]
with tab_replay:
    st.caption("⚠️ 방 배치(위치·크기)는 화면용 임의 배치입니다 — 모델에는 좌표가 없고 공정 → 방 이름만 있습니다. "
               "실제 공장 배치는 인터뷰 확인 필요.")
    # 날짜를 따로 고르지 않고 시뮬레이션 전체 기간(Day 1 09:00 ~ 종료)을 한 번에 연속 재생
    b, c = st.columns([2, 1])
    step_min = b.select_slider("시간 간격", options=[15, 30, 60, 120], value=30 if p_weeks == 1 else 60,
                               format_func=lambda m: f"{m}분",
                               help="간격이 짧을수록 부드럽지만 프레임이 많아져 느려집니다 (기간이 길면 60분 이상 권장)")
    speed = c.select_slider("재생 속도", options=["느리게", "보통", "빠르게", "매우 빠르게"], value="보통")
    n_frames = int(s["end_time"] * 60 / step_min) + 1
    st.caption(f"전체 기간 {format_time(0)} ~ {format_time(s['end_time'])} 연속 재생 · 프레임 {n_frames}개")
    if n_frames > 1000:
        st.warning("프레임이 많아 화면이 느려질 수 있습니다. 시간 간격을 늘려 보세요.")
    states = load_replay(params, step_min)
    if not states:
        st.info("재생할 시뮬레이션 구간이 없습니다.")
    else:
        frame_ms = {"느리게": 800, "보통": 400, "빠르게": 150, "매우 빠르게": 60}[speed]
        st.plotly_chart(replay_figure(states, frame_ms), width="stretch", config={"displayModeBar": False})
        st.caption("▶ 재생 또는 아래 시간 슬라이더로 이동 (슬라이더 아래 D1·D2… = 그 날짜 시작). 방 안 글자에 마우스를 올리면 공정별 주문 수와 주문 ID, "
                   "설비·작업자에 올리면 상태·작업 내용이 보입니다. "
                   "근무시간 09:00~12:00 · 13:00~18:00 (세척·UV 는 근무시간 밖에서 Waiting).")

    with st.expander("주문 추적 (주문 1건의 전체 이벤트)"):
        oid = st.text_input("주문 ID", placeholder="예: R000021").strip()
        if oid:
            try:
                st.dataframe(data.order_trace_table(get_result(*params), oid), hide_index=True, width="stretch")
            except ValueError as e:
                st.warning(str(e))

with tab_replay3d:
    st.caption("⚠️ 방 배치(위치·크기)는 2D Replay 와 같은 화면용 임의 배치입니다 — 실제 공장 배치는 인터뷰 확인 필요.")
    # 2D 와 같은 데이터(Event Log -> 시각별 상태)를 3D 로 그림. 전체 기간 연속 재생
    step3d = st.select_slider("시간 간격", options=[15, 30, 60, 120], value=30 if p_weeks == 1 else 60,
                              format_func=lambda m: f"{m}분", key="step3d",
                              help="간격이 짧을수록 부드럽지만 프레임이 많아져 느려집니다 (기간이 길면 60분 이상 권장)")
    states3d = load_replay(params, step3d)
    if not states3d:
        st.info("재생할 시뮬레이션 구간이 없습니다.")
    else:
        components.html(replay3d_html(states3d, frame_ms=400, height=760), height=775)
        st.caption("▶ 재생 · 속도 ×1/×2/×5/×10 · 슬라이더로 이동 (D1·D2… = 그 날짜 시작). "
                   "마우스 드래그 = 회전, 휠 = 확대/축소, 오른쪽 드래그(Shift+드래그) = 이동, "
                   "설비·작업자·방에 마우스를 올리면 상태가 보입니다. 작업자가 다른 방으로 갈 때는 복도를 거쳐 이동합니다. "
                   "주문 상자: 남색 = 작업 중, 주황 = 대기.")
