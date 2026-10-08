# -*- coding: utf-8 -*-
"""
VPP Digital Twin 대시보드 초안 [명세서 11절 Dashboard · 13절 Factory Replay].

화면 2종 (사이드바에서 선택)
  고객용 (요약)  : 처리량·납기 준수율·리드타임·주문 단계별 시간 + 3D 공장 Replay
  개발자용 (검증): 자원·설비 대별·작업자 상세, 주문 대기시간 분해, 시간 추이, 장비 상태, 2D/3D Replay(실시간 포함),
                  검증 탭(설정값 <-> 관측값 교차 검증, 위치 연속성, 불변식)

실행 (프로젝트 최상위 폴더에서):
  python -m streamlit run dashboard/app.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import datetime as dt
import math

import plotly.graph_objects as go
import streamlit as st

from dashboard import data
from dashboard.replay import STATE_COLORS, STATE_LABELS, replay_figure
from dashboard.replay3d import REALTIME_SPEEDS, replay3d_html
from src.analysis.kpi import ORDER_STAGES
from src.analysis.kpi import MACHINE_STATES
from src.logger.state import DAY_NAMES, START_HOUR, format_time
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


@st.cache_data(show_spinner="실시간 Replay 프레임 계산 중...", max_entries=16)
def load_replay_window(params, start_h, dur_h):
    res = get_result(*params)
    states = data.replay_states(res, data.replay_times_window(start_h, dur_h, 1, res.end_time))
    return states, data.replay_tracks(res, start_h, min(start_h + dur_h, res.end_time))


def embed_html(html, height):
    """HTML 한 장을 iframe 으로 띄움. st.iframe 이 없는 이전 streamlit 이면 components.html (삭제 예고된 기존 방식)."""
    if hasattr(st, "iframe"):
        st.iframe(html, height=height)
    else:
        import streamlit.components.v1 as components
        components.html(html, height=height)


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


def stage_chart(stages):
    """주문 단계별 평균 시간 (달력 h) — 단일 계열 가로 막대, 값 직접 표시."""
    labels = [label for _, label in ORDER_STAGES]
    means = [float(stages[key].mean()) for key, _ in ORDER_STAGES]
    fig = go.Figure(go.Bar(
        y=labels, x=means, orientation="h", marker=dict(color="#1e40af", line=dict(width=0)),
        text=[f"{m:.1f}h" for m in means], textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: %{x:.2f}h<extra></extra>",
    ))
    fig.update_layout(height=260, margin=dict(l=10, r=40, t=10, b=10), showlegend=False)
    fig.update_xaxes(title="평균 시간 (달력 h)", gridcolor="rgba(128,128,128,0.2)", rangemode="tozero")
    fig.update_yaxes(categoryorder="array", categoryarray=list(reversed(labels)))
    return fig


def replay3d_section(params, p_weeks, end_time, realtime_allowed):
    """3D 공장 Replay. 개발자용은 실시간(이벤트 기반) 모드 선택 가능."""
    st.caption("⚠️ 방 배치(위치·크기)는 화면용 임의 배치입니다 — 실제 공장 배치는 인터뷰 확인 필요. "
               "접수·작업 배정·배치 구성·출력 대기 주문은 실물이 없어 방에 두지 않고 왼쪽 위 '실물 없음(전산)' 에 숫자로 표시합니다.")
    modes = ["전체 기간 (빠르게)", "실시간 (1초 = 1초, 이동 검증)"] if realtime_allowed else ["전체 기간 (빠르게)"]
    mode3d = st.radio("재생 모드", modes, horizontal=True, key="mode3d",
                      help="실시간: 고른 구간을 현실 시간 그대로. 작업자는 이동 이벤트 시각 그대로 움직이고, "
                           "AMR·선반도 표시. 이동 이벤트 없이 위치가 바뀌면 ⚡ 순간이동으로 표시") if len(modes) > 1 else modes[0]
    tracks = None
    if mode3d.startswith("전체"):
        step3d = st.select_slider("시간 간격", options=[15, 30, 60, 120], value=30 if p_weeks == 1 else 60,
                                  format_func=lambda m: f"{m}분", key="step3d",
                                  help="간격이 짧을수록 부드럽지만 프레임이 많아져 느려집니다 (기간이 길면 60분 이상 권장)")
        states3d = load_replay(params, step3d)
        html_kw = {"frame_ms": 400}
    else:
        n_days = int((end_time + START_HOUR) // 24) + 1
        c1, c2, c3 = st.columns(3)
        day3d = c1.number_input("시작 날짜 (Day)", min_value=1, max_value=n_days, value=min(2, n_days), key="day3d")
        clock3d = c2.time_input("시작 시각", value=dt.time(10, 0), step=dt.timedelta(minutes=10), key="clock3d")
        dur3d = c3.select_slider("구간 길이", options=[0.5, 1, 2, 4, 8], value=1, key="dur3d",
                                 format_func=lambda h: f"{int(h * 60)}분" if h < 1 else f"{h:g}시간",
                                 help="구간이 길수록 프레임 계산이 오래 걸립니다")
        start_h = (day3d - 1) * 24 + clock3d.hour + clock3d.minute / 60 - START_HOUR
        if not 0 <= start_h < end_time:
            st.warning(f"시작 시각이 시뮬레이션 기간({format_time(0)} ~ {format_time(end_time)}) 밖입니다.")
            return
        states3d, tracks = load_replay_window(params, round(start_h, 6), dur3d)
        html_kw = {"frame_ms": 60_000, "speeds": REALTIME_SPEEDS, "tracks": tracks}
        st.caption(f"{format_time(start_h)} 부터 {len(states3d) - 1}분 · 작업자 위치·설비 상태는 이벤트 시각 그대로 "
                   "(방별 주문 수만 1분 간격). 작업자는 다른 위치의 작업을 하러 갈 때 빈손으로, 운반할 때는 짐(주황 상자)을 "
                   "들고 걷습니다. AMR 은 복도에서만 문 앞 선반 사이를 오갑니다 (적재·하역 동안 정지). "
                   f"이 구간 ⚡ 순간이동 {len(tracks['jumps'])}건 (0 이 정상).")
    if not states3d:
        st.info("재생할 시뮬레이션 구간이 없습니다.")
        return
    embed_html(replay3d_html(states3d, height=760, **html_kw), height=775)
    st.caption("▶ 재생 · 속도 버튼 · 슬라이더로 이동 (D1·D2… = 그 날짜 시작). "
               "마우스 드래그 = 회전, 휠 = 확대/축소, 오른쪽 드래그(Shift+드래그) = 이동, "
               "설비·작업자·방에 마우스를 올리면 상태가 보입니다. "
               "주문 상자: 남색 = 작업 중, 주황 = 대기.")


# ------------------------------------------------------------------ 화면
st.set_page_config(page_title="VPP 디지털 트윈 대시보드", layout="wide")
st.warning(f"⚠️ {ASSUMPTION_NOTE}")
st.title("VPP 디지털 트윈 대시보드")

default_rule = SimConfig.from_parameters().DEFAULT_SCHEDULING_RULE
rules = list(RULES)
with st.sidebar:
    view = st.radio("화면", ["고객용 (요약)", "개발자용 (검증)"], key="view",
                    help="고객용 = 요약 지표 중심, 개발자용 = 설비·작업자별 세부 지표와 로직 검증")
    DEV = view.startswith("개발자")
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

# ------------------------------------------------------------------ 고객용 (요약)
if not DEV:
    tab_sum, tab_3d = st.tabs(["요약", "3D 공장 Replay"])
    with tab_sum:
        c = st.columns(4)
        c[0].metric("주당 처리량", num(k["throughput_per_week"], "건"), help="한 주에 완료된 주문 수")
        c[1].metric("납기 준수율", pct(k["on_time_all"]), help="납기 안에 완료된 주문 비율 (미완료인데 납기가 지난 주문은 지연)")
        c[2].metric("평균 리드타임", num(k["lead_work_h_mean"] / 8 if k["lead_work_h_mean"] == k["lead_work_h_mean"]
                                          else float("nan"), "근무일", 1), help="주문 접수 -> 포장 완료, 근무일(8h) 기준")
        c[3].metric("진행 중 주문 (평균)", num(k["wip_mean"], "건", 0))
        c = st.columns(4)
        c[0].metric("납기 준수율 (일반)", pct(k["on_time_normal"]))
        c[1].metric("납기 준수율 (긴급)", pct(k["on_time_urgent"]))
        c[2].metric("생산 시작 전 대기", num(k["stage_pre_print_h_mean"], "h"),
                    help="접수 -> 출력 시작 (작업 배정 + 빌드플레이트 채우기 + 프린터 대기), 달력시간")
        c[3].metric("생산 시작 후 납품까지", num(k["stage_after_start_h_mean"], "h"),
                    help="출력 시작 -> 포장 완료 (출력 + 후공정 + 검사·포장, 밤·주말 포함 달력시간)")
        if not s["stages"].empty:
            st.subheader("주문이 어디서 시간을 쓰나요? (단계별 평균)")
            st.plotly_chart(stage_chart(s["stages"]), width="stretch", config={"displayModeBar": False})
            st.caption("후공정(세척·UV·후처리)은 근무시간에만 진행되어 저녁에 출력이 끝난 주문은 다음 날 아침까지 기다립니다.")
        if not s["daily"].empty:
            st.plotly_chart(line_chart(s["daily"], [("throughput", "처리량", "#1e40af")], "일별 완료 주문 수"),
                            width="stretch")
    with tab_3d:
        replay3d_section(params, p_weeks, s["end_time"], realtime_allowed=False)
    st.stop()

# ------------------------------------------------------------------ 개발자용 (검증)
(tab_kpi, tab_units, tab_stage, tab_trend, tab_machine, tab_replay, tab_replay3d, tab_check) = st.tabs(
    ["KPI 상세", "설비·작업자 상세", "주문 대기시간 분해", "시간 추이", "장비 상태 타임라인", "2D 공장 Replay",
     "3D 공장 Replay", "검증"])

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
    st.caption("설비 대별·작업자 개인별 값은 '설비·작업자 상세' 탭.")

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
    replay3d_section(params, p_weeks, s["end_time"], realtime_allowed=True)

# ------------------------------------------------------------------ 설비·작업자 상세 (대별)
with tab_units:
    st.subheader("설비 대별 가동률 · 처리량 · 상태 시간")
    ut = s["unit_table"]
    st.dataframe(ut.style.format({"가동률 (Running+Setup)": "{:.1%}", "부품/주": "{:.1f}",
                                  **{f"{x} (h)": "{:.1f}" for x in MACHINE_STATES}}),
                 hide_index=True, width="stretch")
    st.caption("가동률 = (Running + Setup) / 가용시간 (무인운전 프린터 = 달력시간, 세척·UV = 근무시간). "
               "KPI 상세의 종류별 가동률은 세척·UV 의 적재~인출 전체(인출 작업자 대기 포함)를 점유로 보므로 이 값보다 큽니다. "
               "같은 종류 설비끼리 처리량이 크게 다르면 배정 로직을 확인하세요.")
    if not s["amr_table"].empty:
        st.subheader("AMR (운반 로봇)")
        st.dataframe(s["amr_table"].style.format({"운행 시간 (h)": "{:.2f}", "운행 비율 (달력)": "{:.1%}"}),
                     hide_index=True, width="stretch")
        st.caption(f"AMR 평균 호출 대기 {num(k.get('amr_wait_h_mean', float('nan')) * 60, '분', 2)} · "
                   "운행 = 빈 차 이동 + 적재·주행·하역. 사람은 작업 위치 ↔ 문 앞 선반까지만 들고 갑니다.")
    st.subheader("작업자 개인별 가동률")
    st.dataframe(s["workers"].style.format({"가동률": "{:.1%}"}), hide_index=True, width="stretch")

# ------------------------------------------------------------------ 주문 대기시간 분해
with tab_stage:
    sd = s["stages"]
    if sd.empty:
        st.info("완료된 주문이 없습니다.")
    else:
        c = st.columns(3)
        c[0].metric("생산 시작 전 대기 (평균)", num(sd["pre_print"].mean(), "h", 2), help="접수 -> 출력 시작")
        c[1].metric("생산 시작 후 납품까지 (평균)", num(sd["after_start"].mean(), "h", 2), help="출력 시작 -> 포장 완료")
        c[2].metric("리드타임 (평균)", num(sd["lead"].mean(), "h", 2), help="두 값의 합 = 달력 리드타임")
        st.plotly_chart(stage_chart(sd), width="stretch", config={"displayModeBar": False})
        summary = sd[[key for key, _ in ORDER_STAGES] + ["pre_print", "after_start", "lead"]].describe(
            percentiles=[0.5, 0.95]).T[["mean", "50%", "95%", "max"]]
        summary.index = [label for _, label in ORDER_STAGES] + ["생산 시작 전 대기", "생산 시작 후 납품까지", "리드타임"]
        st.dataframe(summary.rename(columns={"mean": "평균", "50%": "중앙값", "95%": "95%", "max": "최대"})
                     .style.format("{:.2f}"), width="stretch")
        st.caption("달력시간 [h]. 부품이 여러 개인 주문은 최초 출력 부품 중 가장 늦은 시각 기준, "
                   "재출력이 있으면 재출력 시간 전체가 '후공정 ~ 포장 완료' 에 들어갑니다.")
        with st.expander("주문별 표"):
            st.dataframe(sd, hide_index=True, width="stretch")

# ------------------------------------------------------------------ 검증
with tab_check:
    ck = s["checks"]
    n_bad = int((ck["판정"] == "확인 필요").sum())
    if n_bad:
        st.error(f"확인 필요 {n_bad}건 — 로직 또는 표본 크기(기간)를 점검하세요.")
    else:
        st.success("설정값과 관측값이 모두 허용 범위 안입니다.")
    st.subheader("설정값 ↔ 관측값 교차 검증")

    def _verdict(row):
        color = {"OK": "", "참고": "color: #475569", "확인 필요": "background-color: rgba(220,38,38,0.15)"}[row["판정"]]
        return [color] * len(row)

    st.dataframe(ck.style.apply(_verdict, axis=1), hide_index=True, width="stretch")
    st.caption("비율·건수는 ±3σ, 평균 작업시간은 ±3σ/√n 안이면 OK. 기간이 짧으면 우연히 벗어날 수 있으니 4주로 다시 확인하세요. "
               "불량률·고장·인력을 바꿔 실행하면 그 값이 결과에 반영되는지 이 표로 바로 확인할 수 있습니다.")

    st.subheader("위치 연속성 (순간이동)")
    c = st.columns(2)
    c[0].metric("부품 순간이동", f"{len(s['order_jumps'])}건", help="실물 부품이 이동 없이 다른 위치에 나타난 횟수 — 0 이어야 정상")
    wj = s["worker_jumps"]
    c[1].metric("작업자 순간이동", f"{len(wj)}건",
                help="작업자가 걷지 않고 다른 위치의 작업을 시작한 횟수 — 이동 모델(distance)에서는 0 이어야 정상")
    if s["order_jumps"]:
        st.dataframe(s["order_jumps"], width="stretch")
    if not wj.empty:
        st.caption("MOVE_TIME_MODE='fixed' (기존 방식) 이면 빈손 이동이 없어 여기 건수가 나옵니다. 많이 일어나는 경로:")
        top = wj.groupby(["worker", "from", "to"]).size().reset_index(name="횟수").sort_values("횟수", ascending=False)
        st.dataframe(top.head(15), hide_index=True, width="stretch")
