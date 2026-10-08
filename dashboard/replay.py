# -*- coding: utf-8 -*-
"""
2D Factory Replay 그림 [명세서 13절]: data.replay_states() 의 시각별 상태 -> plotly 애니메이션 (재생·슬라이더).

  방 = 사각형 (data.ROOM_LAYOUT, ⚠️ 임의 배치), 설비 = 네모 (상태 6종 색), 작업자 = 동그라미 (작업 중 / 대기),
  방마다 주문 수(작업 중 · 대기)와 설비 대기열. 마우스를 올리면 공정별 주문 수와 주문 ID.
"""
import plotly.graph_objects as go

from dashboard.data import ROOM_LABELS, ROOM_LAYOUT

# 장비 상태 6종 고정 색: Running 진한 색, Idle·Waiting 옅은 색, Down·Maintenance 경고 색
STATE_COLORS = {
    "Running": "#1e40af",
    "Setup": "#60a5fa",
    "Idle": "#d1d5db",
    "Waiting": "#bfdbfe",
    "Down": "#dc2626",
    "Maintenance": "#f59e0b",
}
STATE_LABELS = {"Idle": "유휴", "Setup": "준비", "Running": "가동", "Waiting": "대기",
                "Down": "고장", "Maintenance": "정비"}
WORKER_BUSY, WORKER_IDLE = "#0d9488", "#ffffff"
QUEUE_TEXT = {"printer": "대기 배치", "washing": "대기 로드", "uv": "대기 로드"}
_UNIT_QUEUE = (("WASH", "washing"), ("UV", "uv"), ("P", "printer"))
MAX_IDS = 12


def _queue_key(unit):
    return next((q for prefix, q in _UNIT_QUEUE if unit.startswith(prefix)), None)


def _room_box(room):
    x0, y0, x1, y1 = ROOM_LAYOUT[room]
    return x0, y0, x1, y1, (x0 + x1) / 2, y1 - y0


def _machine_positions(machines):
    """설비 위치 (방 안에 가로로 고르게). 설비는 움직이지 않으므로 첫 시각 기준으로 한 번만 계산."""
    by_room = {}
    for u, m in machines.items():
        by_room.setdefault(m["room"], []).append(u)
    pos = {}
    for room, units in by_room.items():
        x0, y0, x1, _, _, h = _room_box(room)
        for i, u in enumerate(units):
            pos[u] = (x0 + (i + 1) * (x1 - x0) / (len(units) + 1), y0 + 0.5 * h)
    return pos


def _worker_positions(workers):
    """작업자 위치: 지금 있는 방의 아래쪽 줄에 차례로 (복도는 오른쪽 끝부터)."""
    slots, pos = {}, {}
    for w, info in workers.items():
        room = info["room"]
        k = slots.get(room, 0)
        slots[room] = k + 1
        x0, y0, x1, _, _, h = _room_box(room)
        if room == "Corridor":
            pos[w] = (8.6 + k * 0.5, y0 + h / 2)
        else:
            pos[w] = (x0 + 0.4 + k * 0.55, y0 + 0.55)
    return pos


def _frame_traces(s, mpos):
    units = list(s["machines"])
    mx = [mpos[u][0] for u in units]
    my = [mpos[u][1] for u in units]
    mstates = [s["machines"][u]["state"] for u in units]
    machines = go.Scatter(
        x=mx, y=my, mode="markers+text", showlegend=False,
        marker=dict(symbol="square", size=34, color=[STATE_COLORS[st] for st in mstates],
                    line=dict(width=1.5, color="#475569")),
        text=[f"{u}<br>{st}" for u, st in zip(units, mstates)], textposition="bottom center",
        hovertext=[f"<b>{u}</b> · {st} ({STATE_LABELS[st]})" for u, st in zip(units, mstates)],
        hoverinfo="text",
    )

    wpos = _worker_positions(s["workers"])
    ws = list(s["workers"])
    busy = [s["workers"][w]["task"] is not None for w in ws]
    workers = go.Scatter(
        x=[wpos[w][0] for w in ws], y=[wpos[w][1] for w in ws], mode="markers+text", showlegend=False,
        marker=dict(symbol="circle", size=18, color=[WORKER_BUSY if b else WORKER_IDLE for b in busy],
                    line=dict(width=2, color=WORKER_BUSY)),
        text=ws, textposition="bottom center", textfont=dict(size=10),
        hovertext=[f"<b>{w}</b> · {s['workers'][w]['task'] or '대기 (작업 없음)'}" for w in ws],
        hoverinfo="text",
    )

    qtext = {}
    for u, m in s["machines"].items():
        q = _queue_key(u)
        if q:
            qtext[m["room"]] = f"{QUEUE_TEXT[q]} {s['queues'].get(q, 0)}"
    ix, iy, itext, ihover = [], [], [], []
    for room, r in s["rooms"].items():
        x0, y0, x1, y1, cx, h = _room_box(room)
        n = r["waiting"] + r["processing"]
        line = f"주문 {n} (작업 {r['processing']} · 대기 {r['waiting']})"
        if room == "Corridor":
            ix.append(cx - 1.0)
            iy.append(y0 + h / 2)
            itext.append(f"운반 중 주문 {n}")
        else:
            ix.append(cx)
            iy.append(y1 - 0.85)
            itext.append(line + (f"<br>{qtext[room]}" if room in qtext else ""))
        detail = "<br>".join(f"{k}: {v}" for k, v in sorted(r["by_process"].items())) or "주문 없음"
        ids = r["ids"][:MAX_IDS]
        more = f" 외 {len(r['ids']) - MAX_IDS}건" if len(r["ids"]) > MAX_IDS else ""
        ihover.append(f"<b>{ROOM_LABELS[room]}</b><br>{detail}" + (f"<br>{' '.join(ids)}{more}" if ids else ""))
    info = go.Scatter(x=ix, y=iy, mode="text", text=itext, textfont=dict(size=12), showlegend=False,
                      hovertext=ihover, hoverinfo="text")

    header = go.Scatter(
        x=[6.0], y=[8.55], mode="text", showlegend=False, hoverinfo="skip", textfont=dict(size=15),
        text=[f"<b>{s['label']}</b>   WIP {s['wip']}건 · 누적 완료 {s['completed']}건 · "
              f"납기 지연(미완료) {s['late']}건 · 레진 누적 {s['resin_L']:.1f} L<br>"
              "<span style='font-size:12px'>실물 없음(전산): "
              + " · ".join(f"{g} {n}" for g, n in s.get("virtual", {}).items()) + "</span>"],
    )
    return [machines, workers, info, header]


def _slider_labels(states):
    """연속 재생용 슬라이더 라벨: 날짜가 바뀌는 첫 프레임에만 'DN(요일)', 나머지는 빈 칸 (눈금이 겹치지 않게)."""
    labels, prev = [], None
    for s in states:
        parts = s["label"].split()                    # "Day 1 09:00 (월)"
        day = f"D{parts[1]}{parts[3]}"
        labels.append(day if day != prev else "")
        prev = day
    return labels


def replay_figure(states, frame_ms=400):
    mpos = _machine_positions(states[0]["machines"])
    frames = [go.Frame(data=_frame_traces(s, mpos), traces=[0, 1, 2, 3], name=str(i))
              for i, s in enumerate(states)]
    fig = go.Figure(data=list(frames[0].data))

    # 범례 전용 (데이터 없음): 설비 상태 6종 · 작업자
    for st, color in STATE_COLORS.items():
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name=f"{st} ({STATE_LABELS[st]})",
                                 marker=dict(symbol="square", size=14, color=color,
                                             line=dict(width=1, color="#475569"))))
    for name, color in (("작업자: 작업 중", WORKER_BUSY), ("작업자: 대기", WORKER_IDLE)):
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name=name,
                                 marker=dict(symbol="circle", size=12, color=color,
                                             line=dict(width=2, color=WORKER_BUSY))))

    for room, (x0, y0, x1, y1) in ROOM_LAYOUT.items():
        fig.add_shape(type="rect", x0=x0, y0=y0, x1=x1, y1=y1, layer="below",
                      line=dict(color="#94a3b8", width=1.5, dash="dot" if room == "Corridor" else "solid"),
                      fillcolor="rgba(148,163,184,0.08)")
        fig.add_annotation(x=x0 + 0.1, y=y1 - 0.08 if room != "Corridor" else (y0 + y1) / 2,
                           text=f"<b>{ROOM_LABELS[room]}</b>", showarrow=False,
                           xanchor="left", yanchor="top" if room != "Corridor" else "middle",
                           font=dict(size=12))

    play = {"frame": {"duration": frame_ms, "redraw": True}, "fromcurrent": True, "transition": {"duration": 0}}
    still = {"frame": {"duration": 0, "redraw": True}, "mode": "immediate", "transition": {"duration": 0}}
    fig.frames = frames
    fig.update_layout(
        height=720, margin=dict(l=10, r=10, t=40, b=90), dragmode=False,
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(range=[-0.2, 12.2], visible=False, fixedrange=True),
        yaxis=dict(range=[-0.3, 9.0], visible=False, fixedrange=True, scaleanchor="x"),
        legend=dict(orientation="h", y=1.0, x=0.5, xanchor="center", yanchor="bottom"),
        updatemenus=[dict(type="buttons", direction="left", x=0, y=-0.02, xanchor="left", yanchor="top",
                          showactive=False,
                          buttons=[dict(label="▶ 재생", method="animate", args=[None, play]),
                                   dict(label="⏸ 정지", method="animate", args=[[None], still])])],
        sliders=[dict(active=0, x=0.17, len=0.83, y=-0.02, yanchor="top", pad=dict(t=0, b=0),
                      ticklen=0, minorticklen=0,
                      currentvalue=dict(visible=False),
                      steps=[dict(method="animate", label=lbl, args=[[str(i)], still])
                             for i, lbl in enumerate(_slider_labels(states))])],
    )
    return fig
