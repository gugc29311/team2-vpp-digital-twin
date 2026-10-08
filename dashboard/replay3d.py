# -*- coding: utf-8 -*-
"""
3D Factory Replay [명세서 13절]: data.replay_states() 의 시각별 상태 -> 브라우저 3D 화면 (HTML 한 장).

  2D Replay (replay.py) 와 같은 데이터·같은 방 배치(data.ROOM_LAYOUT, ⚠️ 임의 배치)를 3D 로 그린다.
  - 방 = 바닥 + 낮은 벽, 설비 = 상자 (상태 6종 색), 작업자 = 사람 모양 (작업 중 / 대기, 방 이동은 복도 경유),
    주문 = 방 안의 작은 상자 (작업 중 · 대기), 방 위에 주문 수와 설비 대기열
  - 전체 기간 연속 재생: Play / Pause / 속도 ×1 ×2 ×5 ×10 / 타임라인 슬라이더 (날짜 눈금)
  - 마우스: 드래그 = 회전, 휠 = 확대/축소, 오른쪽 드래그(또는 Shift+드래그) = 이동, 올리면 상태 표시
  - 실시간 모드(tracks 전달): 작업자·AMR·설비를 이벤트 시각 그대로 그림. 작업자는 작업 위치(방 안) <-> 문 앞(복도)
    <-> 다른 방으로 걷고(빈손 / 짐), AMR 은 복도에서만 문 앞 선반 사이를 오감 (적재·하역 동안 정지).
    문 앞 선반에 놓인 운반물 수도 표시. 이동 이벤트 없이 위치가 바뀌면 '순간이동' 으로 빨갛게 표시 (distance 모드면 0건)
  - 실물 없는 주문(접수·작업 배정·배치 구성·출력 대기)은 방에 두지 않고 왼쪽 위 '실물 없음(전산)' 에 숫자로
  외부 라이브러리 없이 Canvas 로 직접 그림 (인터넷 연결 없이 동작).
"""
import json

from dashboard.data import ROOM_LABELS, ROOM_LAYOUT, VIRTUAL_GROUPS
from src.analysis.event_schema import MACHINE_STATES
from src.logger.state import DAY_NAMES
from dashboard.replay import (MAX_IDS, QUEUE_TEXT, STATE_COLORS, STATE_LABELS, _machine_positions,
                              _queue_key, _worker_positions)

# 상태 번호 = MACHINE_STATES 순서 (0 Idle, 1 Setup, 2 Running …). data.replay_tracks 의 상태 구간도 같은 번호를 씀
# (예전에 STATE_COLORS 순서를 써서 실시간 모드에서 Running <-> Idle 이 뒤바뀌던 버그 수정)
_STATES = list(MACHINE_STATES)
ROOM_SHORT = {"Print Room": "프린터실", "Post-processing Room": "후공정실", "Corridor": "복도", "Packing Room": "포장실",
              "Inspection Room": "검사실", "UV Room": "UV실", "Wash Room": "세척실"}


def _kind(unit):
    if unit.startswith("WASH"):
        return "wash"
    if unit.startswith("UV"):
        return "uv"
    return "printer"


DEFAULT_SPEEDS = ((1, "×1"), (2, "×2"), (5, "×5"), (10, "×10"))
# 실시간 모드: 프레임 1장 = 1분 = 60초 동안 보여줌 -> ×1 = 현실 시간 그대로
REALTIME_SPEEDS = ((1, "실시간 ×1"), (10, "×10"), (60, "×60 (1초=1분)"), (600, "×600 (1초=10분)"))


def build_payload(states, frame_ms=400, speeds=DEFAULT_SPEEDS, tracks=None):
    """시각별 상태 목록 -> 3D 화면용 JSON 직렬화 가능한 dict (정적 배치 + 프레임별 변화).

    tracks = data.replay_tracks(...) 이면 실시간 모드: 작업자·설비를 프레임이 아니라 이벤트 시각으로 그리고 초 단위 시계."""
    mpos = _machine_positions(states[0]["machines"])
    units = list(states[0]["machines"])
    rooms = list(ROOM_LAYOUT)
    workers = list(states[0]["workers"])

    frames, day_ticks, prev_day = [], [], None
    for i, s in enumerate(states):
        parts = s["label"].split()                       # "Day 1 09:00 (월)"
        day = f"D{parts[1]}{parts[3]}"
        if day != prev_day:
            day_ticks.append([i, day])
            prev_day = day

        qtext = {}
        for u, m in s["machines"].items():
            q = _queue_key(u)
            if q:
                qtext[m["room"]] = f"{QUEUE_TEXT[q]} {s['queues'].get(q, 0)}"
        rdata = []
        for room in rooms:
            r = s["rooms"][room]
            n = r["waiting"] + r["processing"]
            if room == "Corridor":
                text = f"운반 중 주문 {n}"
            else:
                text = f"주문 {n} (작업 {r['processing']} · 대기 {r['waiting']})"
                if room in qtext:
                    text += f" · {qtext[room]}"
            detail = "<br>".join(f"{k}: {v}" for k, v in sorted(r["by_process"].items())) or "주문 없음"
            ids = r["ids"][:MAX_IDS]
            more = f" 외 {len(r['ids']) - MAX_IDS}건" if len(r["ids"]) > MAX_IDS else ""
            hover = f"<b>{ROOM_LABELS[room]}</b><br>{detail}" + (f"<br>{' '.join(ids)}{more}" if ids else "")
            rdata.append([r["waiting"], r["processing"], text, hover])

        wpos = _worker_positions(s["workers"])
        wdata = []
        for w in workers:
            info = s["workers"][w]
            x, y = wpos[w]
            wdata.append([round(x, 3), round(y, 3), 1 if info["task"] else 0, rooms.index(info["room"])
                          if info["room"] in rooms else rooms.index("Corridor"), info["task"] or ""])

        frames.append({
            "t": round(s["t"], 6),
            "v": [s.get("virtual", {}).get(g, 0) for g in VIRTUAL_GROUPS],
            "sh": [s["rooms"][r].get("shelf", 0) for r in rooms],
            "l": s["label"],
            "k": [s["wip"], s["completed"], s["late"], round(s["resin_L"], 1)],
            "m": [_STATES.index(s["machines"][u]["state"]) for u in units],
            "w": wdata,
            "r": rdata,
        })

    return {
        "frameMs": frame_ms,
        "speeds": [list(v) for v in speeds],
        "rt": tracks,
        "dayNames": list(DAY_NAMES),
        "virtualGroups": list(VIRTUAL_GROUPS),
        "states": [{"key": st, "label": STATE_LABELS[st], "color": STATE_COLORS[st]} for st in _STATES],
        "rooms": [{"key": r, "label": ROOM_LABELS[r], "short": ROOM_SHORT.get(r, r), "box": list(ROOM_LAYOUT[r]),
                   "corridor": r == "Corridor"}
                  for r in rooms],
        "machines": [{"id": u, "x": mpos[u][0], "y": mpos[u][1], "kind": _kind(u)} for u in units],
        "workers": workers,
        "dayTicks": day_ticks,
        "frames": frames,
    }


def replay3d_html(states, frame_ms=400, height=760, speeds=DEFAULT_SPEEDS, tracks=None):
    """3D Replay HTML (app.embed_html -> st.iframe 으로 띄움). tracks 를 주면 실시간(이벤트 기반) 모드."""
    payload = json.dumps(build_payload(states, frame_ms, speeds, tracks), ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")               # </script> 로 끊기지 않게
    return _TEMPLATE.replace("__HEIGHT__", str(int(height))).replace("__PAYLOAD__", payload)


_TEMPLATE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<style>
  :root { --bg:#f1f5f9; --panel:rgba(255,255,255,.92); --ink:#0f172a; --muted:#475569; --line:#cbd5e1; --accent:#1e40af; }
  * { box-sizing:border-box; }
  html,body { margin:0; height:100%; background:var(--bg); font-family:"Malgun Gothic","Apple SD Gothic Neo","Noto Sans KR",system-ui,sans-serif; color:var(--ink); }
  #wrap { position:relative; width:100%; height:__HEIGHT__px; overflow:hidden; border-radius:10px; background:linear-gradient(#e2e8f0,#f8fafc 55%); }
  canvas { position:absolute; inset:0; width:100%; height:100%; cursor:grab; }
  canvas.drag { cursor:grabbing; }
  .panel { position:absolute; background:var(--panel); border:1px solid var(--line); border-radius:8px; box-shadow:0 1px 3px rgba(15,23,42,.08); }
  #hud { top:10px; left:10px; padding:8px 12px; font-size:14px; line-height:1.5; }
  #hud .t { font-size:17px; font-weight:700; }
  #hud .k span { margin-right:12px; white-space:nowrap; }
  #hud .v { font-size:12px; color:var(--muted); }
  #hud .m { font-size:12px; max-width:560px; }
  #hud .m .j { color:#b91c1c; font-weight:700; }
  #view { top:10px; right:10px; padding:6px; display:flex; gap:6px; }
  #legend { left:10px; bottom:86px; padding:6px 10px; font-size:12px; display:flex; flex-wrap:wrap; gap:4px 12px; max-width:calc(100% - 20px); }
  #legend i { display:inline-block; width:12px; height:12px; border:1px solid #475569; vertical-align:-2px; margin-right:4px; }
  #legend i.c { border-radius:50%; border:2px solid #0d9488; }
  #bar { left:10px; right:10px; bottom:10px; padding:8px 12px 20px; display:flex; align-items:center; gap:10px; }
  button { font:inherit; font-size:13px; padding:4px 10px; border:1px solid var(--line); background:#fff; border-radius:6px; cursor:pointer; color:var(--ink); }
  button:hover { border-color:#94a3b8; }
  button.on { background:var(--accent); color:#fff; border-color:var(--accent); }
  #play { min-width:78px; font-weight:600; }
  #track { position:relative; flex:1; min-width:120px; }
  #slider { width:100%; margin:0; accent-color:var(--accent); }
  #ticks { position:absolute; left:0; right:0; top:22px; height:14px; font-size:10px; color:var(--muted); }
  #ticks span { position:absolute; transform:translateX(-50%); white-space:nowrap; }
  #ticks span::before { content:""; position:absolute; left:50%; top:-6px; height:5px; border-left:1px solid #94a3b8; }
  #now { min-width:150px; text-align:right; font-size:13px; color:var(--muted); font-variant-numeric:tabular-nums; }
  #tip { position:absolute; pointer-events:none; display:none; padding:6px 9px; font-size:12px; line-height:1.45; background:#0f172a; color:#f8fafc; border-radius:6px; max-width:320px; z-index:5; }
  #speeds { display:flex; gap:4px; }
</style></head>
<body>
<div id="wrap">
  <canvas id="cv"></canvas>
  <div id="hud" class="panel"><div class="t" id="hudT"></div><div class="k" id="hudK"></div>
    <div class="v" id="hudV"></div><div class="m" id="hudM"></div></div>
  <div id="view" class="panel">
    <button id="vHome" title="처음 시점으로">기본 시점</button>
    <button id="vTop" title="위에서 내려다보기">위에서</button>
    <button id="vSpin" title="천천히 자동 회전">자동 회전</button>
  </div>
  <div id="legend" class="panel"></div>
  <div id="bar" class="panel">
    <button id="play">▶ 재생</button>
    <div id="speeds"></div>
    <div id="track"><input id="slider" type="range" min="0" value="0" step="1"><div id="ticks"></div></div>
    <div id="now"></div>
  </div>
  <div id="tip"></div>
</div>
<script>
const D = __PAYLOAD__;
const N = D.frames.length;
const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
const wrap = document.getElementById('wrap'), tip = document.getElementById('tip');
const slider = document.getElementById('slider'), playBtn = document.getElementById('play');
slider.max = Math.max(0, N - 1);

// ---------------------------------------------------------------- 좌표: 2D (x, y) -> 3D (X = x, Z = y, Y = 높이)
const CORRIDOR_Z = (() => { const c = D.rooms.find(r => r.corridor); return c ? (c.box[1] + c.box[3]) / 2 : 3.9; })();
const SC = D.states.map(s => s.color);
const ROOM_FLOOR = { 'Print Room':'#e0e7ff', 'Post-processing Room':'#e0f2fe', 'Corridor':'#e5e7eb', 'Packing Room':'#fef3c7',
                     'Inspection Room':'#dcfce7', 'UV Room':'#fae8ff', 'Wash Room':'#ccfbf1' };
const WORKER_BUSY = '#0d9488', WORKER_IDLE = '#ffffff';
const ORDER_PROC = '#1e3a8a', ORDER_WAIT = '#f59e0b';

// ---------------------------------------------------------------- 카메라
const HOME = { az: -0.32, el: 0.86, dist: 18.5, tx: 6.0, ty: 0.0, tz: 4.2 };
const TOP  = { az: 0.0,  el: 1.53, dist: 19.5, tx: 6.0, ty: 0.0, tz: 4.5 };
let cam = Object.assign({}, HOME), camGoal = null, spin = false;
let W = 0, H = 0, DPR = 1;
let basis = null;

function resize() {
  DPR = window.devicePixelRatio || 1;
  W = wrap.clientWidth; H = wrap.clientHeight;
  cv.width = Math.round(W * DPR); cv.height = Math.round(H * DPR);
}
function setupCamera() {
  const ce = Math.cos(cam.el), se = Math.sin(cam.el);
  const ex = cam.tx + cam.dist * ce * Math.sin(cam.az);
  const ey = cam.ty + cam.dist * se;
  const ez = cam.tz - cam.dist * ce * Math.cos(cam.az);
  let fx = cam.tx - ex, fy = cam.ty - ey, fz = cam.tz - ez;
  const fl = Math.hypot(fx, fy, fz); fx /= fl; fy /= fl; fz /= fl;
  // r = up x f (up = +Y), u = f x r
  let rx = fz, ry = 0, rz = -fx; const rl = Math.hypot(rx, rz) || 1; rx /= rl; rz /= rl;
  const ux = fy * rz - fz * ry, uy = fz * rx - fx * rz, uz = fx * ry - fy * rx;
  const scale = Math.min(W, H * 1.6) * 1.05;
  basis = { ex, ey, ez, fx, fy, fz, rx, ry, rz, ux, uy, uz, scale, cx: W / 2, cy: H * 0.47 };
}
function proj(x, y, z) {
  const b = basis, px = x - b.ex, py = y - b.ey, pz = z - b.ez;
  const zc = px * b.fx + py * b.fy + pz * b.fz;
  if (zc < 0.2) return null;
  const xc = px * b.rx + py * b.ry + pz * b.rz, yc = px * b.ux + py * b.uy + pz * b.uz;
  return [b.cx + b.scale * xc / zc, b.cy - b.scale * yc / zc, zc];
}

// ---------------------------------------------------------------- 그리기 목록 (painter's algorithm)
let items = [];
const LIGHT = (() => { const v = [-0.45, 0.85, -0.35]; const l = Math.hypot(...v); return v.map(a => a / l); })();
function shade(hex, k) {
  const n = parseInt(hex.slice(1), 16);
  const f = c => Math.max(0, Math.min(255, Math.round(c * k)));
  return `rgb(${f(n >> 16)},${f((n >> 8) & 255)},${f(n & 255)})`;
}
function quad(pts3, color, normal, opts) {
  const b = basis;
  // back-face culling (바닥 평면 제외)
  if (normal && !(opts && opts.noCull)) {
    const c = pts3.reduce((a, p) => [a[0] + p[0] / 4, a[1] + p[1] / 4, a[2] + p[2] / 4], [0, 0, 0]);
    if ((b.ex - c[0]) * normal[0] + (b.ey - c[1]) * normal[1] + (b.ez - c[2]) * normal[2] <= 0) return;
  }
  const pp = []; let depth = 0;
  for (const p of pts3) { const q = proj(p[0], p[1], p[2]); if (!q) return; pp.push(q); depth += q[2]; }
  const lit = normal ? 0.62 + 0.38 * Math.max(0, normal[0] * LIGHT[0] + normal[1] * LIGHT[1] + normal[2] * LIGHT[2]) : 1;
  items.push({ t: 'poly', pp, depth: depth / pp.length + ((opts && opts.bias) || 0), fill: shade(color, lit),
               stroke: opts && opts.stroke, alpha: opts && opts.alpha, layer: (opts && opts.layer != null) ? opts.layer : 1 });
}
function box(x0, y0, z0, x1, y1, z1, color, opts) {
  const o = Object.assign({ stroke: 'rgba(15,23,42,.35)' }, opts || {});
  quad([[x0, y1, z0], [x1, y1, z0], [x1, y1, z1], [x0, y1, z1]], color, [0, 1, 0], o);   // 윗면
  quad([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0]], color, [0, 0, -1], o);  // 앞 (z0)
  quad([[x1, y0, z1], [x0, y0, z1], [x0, y1, z1], [x1, y1, z1]], color, [0, 0, 1], o);   // 뒤 (z1)
  quad([[x0, y0, z1], [x0, y0, z0], [x0, y1, z0], [x0, y1, z1]], color, [-1, 0, 0], o);  // 왼쪽
  quad([[x1, y0, z0], [x1, y0, z1], [x1, y1, z1], [x1, y1, z0]], color, [1, 0, 0], o);   // 오른쪽
}
function circle(x, y, z, r, fill, stroke) {
  const q = proj(x, y, z); if (!q) return;
  items.push({ t: 'circle', q, r: basis.scale * r / q[2], depth: q[2], fill, stroke, layer: 1 });
}

// ---------------------------------------------------------------- 정적 장면
function addFloor() {
  quad([[-0.6, 0, -0.6], [12.6, 0, -0.6], [12.6, 0, 8.6], [-0.6, 0, 8.6]], '#cbd5e1', [0, 1, 0], { layer: 0, stroke: null });
  for (const r of D.rooms) {
    const [x0, z0, x1, z1] = r.box;
    quad([[x0, 0.002, z0], [x1, 0.002, z0], [x1, 0.002, z1], [x0, 0.002, z1]], ROOM_FLOOR[r.key] || '#e5e7eb', [0, 1, 0],
         { layer: 0.5, stroke: r.corridor ? 'rgba(100,116,139,.6)' : null });
    if (r.corridor) {  // 복도 가운데 점선
      for (let x = x0 + 0.2; x < x1 - 0.3; x += 0.6)
        quad([[x, 0.004, CORRIDOR_Z - 0.02], [x + 0.3, 0.004, CORRIDOR_Z - 0.02], [x + 0.3, 0.004, CORRIDOR_Z + 0.02], [x, 0.004, CORRIDOR_Z + 0.02]],
             '#94a3b8', [0, 1, 0], { layer: 0.6 });
    }
  }
}
function addWalls() {
  const t = 0.06, wallC = '#f8fafc';
  for (const r of D.rooms) {
    if (r.corridor) continue;
    const [x0, z0, x1, z1] = r.box;
    const hb = 0.42, hf = 0.12;                 // 뒤·옆 벽은 높게, 앞 벽은 낮게 (안이 보이도록)
    // 복도 쪽 벽에는 문(가운데 틈)
    const door = (a, b) => [[a, (a + b) / 2 - 0.35], [(a + b) / 2 + 0.35, b]];
    const zFront = z0, zBack = z1;
    const frontSegs = (Math.abs(z0 - 4.6) < 0.01) ? door(x0, x1) : [[x0, x1]];  // 윗줄 방: 앞(z0)이 복도 쪽
    const backSegs = (Math.abs(z1 - 3.2) < 0.01) ? door(x0, x1) : [[x0, x1]];   // 아랫줄 방: 뒤(z1)가 복도 쪽
    for (const [a, b] of frontSegs) box(a, 0, zFront - t / 2, b, hf, zFront + t / 2, wallC, { stroke: 'rgba(100,116,139,.5)' });
    for (const [a, b] of backSegs) box(a, 0, zBack - t / 2, b, hb, zBack + t / 2, wallC, { stroke: 'rgba(100,116,139,.5)' });
    box(x0 - t / 2, 0, z0, x0 + t / 2, hb, z1, wallC, { stroke: 'rgba(100,116,139,.5)' });
    box(x1 - t / 2, 0, z0, x1 + t / 2, hb, z1, wallC, { stroke: 'rgba(100,116,139,.5)' });
  }
}

// ---------------------------------------------------------------- 동적: 설비 · 작업자 · 주문
let hits = [];   // {kind, poly|rect, depth, html}
function lastStart(segs, T) {    // 시작 시각 <= T 인 마지막 구간 번호 (없으면 -1). segs 는 시작 시각 순
  let lo = 0, hi = segs.length - 1, k = -1;
  while (lo <= hi) { const mid = (lo + hi) >> 1; if (segs[mid][0] <= T) { k = mid; lo = mid + 1; } else hi = mid - 1; }
  return k;
}
function rtMachine(i, T, fallback) {
  const segs = D.rt.machines[i], k = lastStart(segs, T);
  return (k >= 0 && T < segs[k][1]) ? segs[k][2] : fallback;
}
function addMachines(fr, pulse, T) {
  D.machines.forEach((m, i) => {
    const st = T == null ? fr.m[i] : rtMachine(i, T, fr.m[i]), color = SC[st], x = m.x, z = m.y;
    let w = 0.78, d = 0.7, h = 1.05;
    if (m.kind !== 'printer') { w = 0.8; d = 0.8; h = 0.72; }
    box(x - w / 2, 0, z - d / 2, x + w / 2, h, z + d / 2, color);
    if (m.kind === 'printer') {   // 프린터: 위쪽 덮개 + 작업 창
      box(x - w / 2 + 0.06, h, z - d / 2 + 0.06, x + w / 2 - 0.06, h + 0.12, z + d / 2 - 0.06, color);
      quad([[x - 0.22, 0.42, z - d / 2 - 0.002], [x + 0.22, 0.42, z - d / 2 - 0.002], [x + 0.22, 0.85, z - d / 2 - 0.002], [x - 0.22, 0.85, z - d / 2 - 0.002]],
           st === 0 ? '#e2e8f0' : '#bae6fd', [0, 0, -1], { stroke: 'rgba(15,23,42,.4)', bias: -0.001 });
    } else {                      // 세척기·UV기: 둥근 투입구 느낌의 윗판
      box(x - 0.22, h, z - 0.22, x + 0.22, h + 0.05, z + 0.22, m.kind === 'uv' ? '#7c3aed' : '#0891b2');
    }
    const top = (m.kind === 'printer' ? h + 0.12 : h + 0.05);
    const key = D.states[st].key;
    if (key === 'Running') circle(x + w / 2 - 0.12, top + 0.08, z - d / 2 + 0.12, 0.05 + 0.02 * pulse, '#22c55e', '#14532d');
    if (key === 'Down') circle(x, top + 0.18, z, 0.09 + 0.03 * pulse, '#dc2626', '#7f1d1d');
    if (key === 'Maintenance') circle(x, top + 0.18, z, 0.08, '#f59e0b', '#78350f');
    labels.push({ x, y: top + 0.35, z, text: `${m.id} · ${key}`, sub: D.states[st].label, size: 12, bold: true });
    const corners = [[x - w / 2, 0, z - d / 2], [x + w / 2, 0, z - d / 2], [x + w / 2, top, z - d / 2], [x - w / 2, top, z - d / 2],
                     [x - w / 2, top, z + d / 2], [x + w / 2, top, z + d / 2], [x + w / 2, 0, z + d / 2], [x - w / 2, 0, z + d / 2]];
    pushHitBox(corners, `<b>${m.id}</b> · ${key} (${D.states[st].label})`, 1);
  });
}
function pathPos(A, B, a) {   // 출발 -> 복도 -> 도착 (경로 길이에 비례), a = 0..1
  const P = [[A[0], A[1]], [A[0], CORRIDOR_Z], [B[0], CORRIDOR_Z], [B[0], B[1]]];
  const seg = [0]; for (let i = 1; i < P.length; i++) seg.push(seg[i - 1] + Math.hypot(P[i][0] - P[i - 1][0], P[i][1] - P[i - 1][1]));
  const L = seg[seg.length - 1] || 1, s = Math.max(0, Math.min(1, a)) * L;
  for (let i = 1; i < P.length; i++) if (s <= seg[i] + 1e-9) {
    const k = (s - seg[i - 1]) / ((seg[i] - seg[i - 1]) || 1);
    return [P[i - 1][0] + (P[i][0] - P[i - 1][0]) * k, P[i - 1][1] + (P[i][1] - P[i - 1][1]) * k];
  }
  return [B[0], B[1]];
}
function roomSlot(r, slot) { const b = D.rooms[r].box; return [b[0] + 0.4 + slot * 0.55, b[1] + 0.55]; }
// 실시간 모드 위치 (parameters 배치도와 같은 축척): 문 앞 = 복도 가운데, 작업 위치 = 문에서 방 안쪽으로 ROOM_DEPTH
function point(r, door, slot, lane) {
  const g = D.rt.roomGeo[r];
  if (g.doorX == null) return [6, CORRIDOR_Z];
  if (door) return [g.doorX, CORRIDOR_Z];
  return [g.doorX + (slot - 1) * 0.42, CORRIDOR_Z + g.side * (D.rt.depth + lane * 0.16)];
}
function rtWorker(j, T) {        // 이벤트 기반 위치: 이동(걷기·운반) 구간만 움직이고, 작업 중·대기 중에는 제자리
  const w = D.rt.workers[j], k = lastStart(w.segs, T);
  const at = (r, d) => point(r, d, w.slot, w.lane);
  if (k < 0) { const [x, z] = at(w.room0, w.door0); return { x, z, busy: false, task: '' }; }
  const g = w.segs[k];
  if (T < g[1] && g[2]) {
    const [x, z] = pathPos(at(g[3], g[4]), at(g[5], g[6]), (T - g[0]) / ((g[1] - g[0]) || 1e-9));
    return { x, z, busy: true, task: moveText(w.id, g, T), moving: true, carrying: !g[7].startsWith('WALK') };
  }
  const [x, z] = at(g[5], g[6]);
  return T < g[1] ? { x, z, busy: true, task: g[7] } : { x, z, busy: false, task: '' };
}
const AMR_Z = () => CORRIDOR_Z + 0.24;            // AMR 주행 차선 (사람은 복도 가운데)
function rtAmr(k, T) {           // AMR: 적재 xfer 동안 출발 문에 정지 -> 주행 -> 하역 xfer 동안 도착 문에 정지
  const a = D.rt.amrs[k], i = lastStart(a.segs, T);
  if (i < 0) return { x: a.x0, loaded: false, task: '대기' };
  const g = a.segs[i];
  if (T >= g[1]) return { x: g[3], loaded: false, task: '대기' };
  const s = g[0] + g[5], e = g[1] - g[5];
  const f = T <= s ? 0 : T >= e ? 1 : (T - s) / ((e - s) || 1e-9);
  const phase = g[4] ? (T < s ? '적재 중' : T > e ? '하역 중' : '운반 중') : '빈 차 이동';
  return { x: g[2] + (g[3] - g[2]) * f, loaded: !!g[4] && T < g[1], task: phase, g };
}
function recentJump(j, T) {      // 방금(시뮬레이션 30초 또는 화면 2초 이내) 순간이동했으면 그 기록
  const win = Math.max(30, 2 * speed) / 3600;
  for (const J of D.rt.jumps) if (J[0] === j && T >= J[1] && T - J[1] < win) return J;
  return null;
}
function workerPos(fa, fb, j, a) {
  const A = fa.w[j], B = fb.w[j];
  if (a <= 0 || A[3] === B[3] && Math.hypot(A[0] - B[0], A[1] - B[1]) < 1e-6) return [A[0], A[1]];
  if (A[3] === B[3]) return [A[0] + (B[0] - A[0]) * a, A[1] + (B[1] - A[1]) * a];
  // 다른 방으로 이동: 출발 -> 복도 -> 도착 (경로 길이에 비례)
  const P = [[A[0], A[1]], [A[0], CORRIDOR_Z], [B[0], CORRIDOR_Z], [B[0], B[1]]];
  const seg = [0]; for (let i = 1; i < P.length; i++) seg.push(seg[i - 1] + Math.hypot(P[i][0] - P[i - 1][0], P[i][1] - P[i - 1][1]));
  const L = seg[seg.length - 1] || 1, s = a * L;
  for (let i = 1; i < P.length; i++) if (s <= seg[i] + 1e-9) {
    const k = (s - seg[i - 1]) / ((seg[i] - seg[i - 1]) || 1);
    return [P[i - 1][0] + (P[i][0] - P[i - 1][0]) * k, P[i - 1][1] + (P[i][1] - P[i - 1][1]) * k];
  }
  return [B[0], B[1]];
}
function addWorkers(fa, fb, a, T) {
  D.workers.forEach((id, j) => {
    let x, z, busy, task;
    if (T == null) { [x, z] = workerPos(fa, fb, j, a); busy = fa.w[j][2] === 1; task = fa.w[j][4]; }
    else {
      const s = rtWorker(j, T); x = s.x; z = s.z; busy = s.busy; task = s.task;
      if (s.carrying) box(x - 0.09, 0.3, z - 0.2, x + 0.09, 0.42, z - 0.08, ORDER_WAIT, { stroke: 'rgba(120,53,15,.8)' });   // 운반물
      const J = recentJump(j, T);
      if (J) {
        const [gx, gz] = point(J[2], 0, D.rt.workers[j].slot, D.rt.workers[j].lane);
        circle(gx, 0.3, gz, 0.14, 'rgba(254,226,226,.6)', '#dc2626');                       // 출발 방의 잔상
        circle(x, 0.47, z, 0.2, 'rgba(254,202,202,.35)', '#dc2626');
        labels.push({ x, y: 0.95, z, text: `⚡ 순간이동 ${D.rooms[J[2]].short}→${D.rooms[J[3]].short}`, size: 11, bold: true });
      }
    }
    const body = busy ? WORKER_BUSY : '#f8fafc';
    box(x - 0.11, 0, z - 0.09, x + 0.11, 0.36, z + 0.09, body, { stroke: 'rgba(13,148,136,.9)' });
    circle(x, 0.47, z, 0.1, busy ? '#99f6e4' : '#ffffff', WORKER_BUSY);
    labels.push({ x, y: 0.72, z, text: id, size: 10 });
    const corners = [[x - 0.14, 0, z], [x + 0.14, 0, z], [x + 0.14, 0.6, z], [x - 0.14, 0.6, z]];
    pushHitBox(corners, `<b>${id}</b> · ${task || '대기 (작업 없음)'}`, 2);
  });
}
function addAmrs(T) {
  D.rt.amrs.forEach((a, k) => {
    const s = rtAmr(k, T), x = s.x, z = AMR_Z();
    box(x - 0.26, 0.04, z - 0.17, x + 0.26, 0.2, z + 0.17, '#334155', { stroke: 'rgba(15,23,42,.8)' });
    circle(x + 0.18, 0.21, z, 0.035, s.task === '대기' ? '#94a3b8' : '#22c55e', '#0f172a');      // 상태 표시등
    if (s.loaded) box(x - 0.13, 0.2, z - 0.11, x + 0.13, 0.36, z + 0.11, ORDER_WAIT, { stroke: 'rgba(120,53,15,.8)' });
    labels.push({ x, y: 0.55, z, text: a.id, size: 10, bold: true });
    pushHitBox([[x - 0.26, 0, z - 0.17], [x + 0.26, 0, z - 0.17], [x + 0.26, 0.36, z + 0.17], [x - 0.26, 0.36, z + 0.17]],
               `<b>${a.id}</b> · ${s.task}` + (s.g ? `<br>${s.g[6]}` : ''), 2);
  });
}
function addShelves(fr) {       // 문 앞 선반 (AMR 이 싣고 내리는 곳) + 놓인 운반물 수
  D.rooms.forEach((r, i) => {
    const g = D.rt.roomGeo[i];
    if (g.doorX == null) return;
    const x = g.doorX + 0.55, z = CORRIDOR_Z + g.side * 0.3, n = (fr.sh || [])[i] || 0;
    box(x - 0.16, 0, z - 0.08, x + 0.16, 0.22, z + 0.08, '#cbd5e1', { stroke: 'rgba(71,85,105,.7)' });
    for (let k = 0; k < Math.min(n, 4); k++)
      box(x - 0.12 + (k % 2) * 0.13, 0.22 + Math.floor(k / 2) * 0.09, z - 0.06, x - 0.01 + (k % 2) * 0.13, 0.3 + Math.floor(k / 2) * 0.09, z + 0.06, ORDER_WAIT);
    if (n) labels.push({ x, y: 0.5, z, text: `선반 ${n}`, size: 10, chip: true });
  });
}
function addOrders(fr) {
  D.rooms.forEach((r, i) => {
    const [x0, z0, x1, z1] = r.box;
    const [wait, proc, text, hover] = fr.r[i];
    const s = 0.13, gap = 0.18;
    let zs, cols, xs;
    if (r.corridor) { xs = x0 + 0.4; zs = z0 + 0.22; cols = Math.floor((x1 - x0 - 4.5) / gap); }
    else { xs = x0 + 0.3; zs = z1 - 0.32; cols = Math.max(1, Math.floor((x1 - x0 - 0.6) / gap)); }
    const rows = r.corridor ? 2 : 3, perLayer = cols * rows, maxShow = perLayer * 3;
    const total = proc + wait;
    let shown = Math.min(total, maxShow);
    if (D.rt && r.corridor) shown = 0;      // 실시간: 운반 중인 주문은 작업자 손의 운반물로 표시
    for (let k = 0; k < shown; k++) {
      const layer = Math.floor(k / perLayer), rem = k % perLayer, row = Math.floor(rem / cols), col = rem % cols;
      const x = xs + col * gap, z = zs - row * gap * (r.corridor ? -1 : 1), y = layer * (s + 0.01);
      box(x, y, z - s / 2, x + s, y + s, z + s / 2, k < proc ? ORDER_PROC : ORDER_WAIT, { stroke: 'rgba(15,23,42,.45)' });
    }
    if (total > shown && !(D.rt && r.corridor)) labels.push({ x: xs + cols * gap * 0.5, y: 3 * (s + 0.01) + 0.15, z: zs, text: `+${total - shown}`, size: 11, bold: true });
    // 방 이름 (바닥 앞쪽) · 방 정보 (뒤쪽 공중)
    if (r.corridor) {
      labels.push({ x: x1 - 2.0, y: 0.05, z: (z0 + z1) / 2, text: `${r.label} · ${text}`, size: 12, floor: true });
    } else {
      labels.push({ x: (x0 + x1) / 2, y: 1.75, z: z1 - 0.15, text: r.label, size: 13, bold: true, chip: true });
      labels.push({ x: (x0 + x1) / 2, y: 1.45, z: z1 - 0.62, text, size: 11, chip: true });
    }
    const corners = [[x0, 0, z0], [x1, 0, z0], [x1, 0, z1], [x0, 0, z1]];
    pushHitBox(corners, hover, 0);
  });
}
function pushHitBox(corners3, html, prio) {
  const pts = []; let depth = 0;
  for (const c of corners3) { const q = proj(c[0], c[1], c[2]); if (!q) return; pts.push(q); depth += q[2]; }
  hits.push({ pts: hull(pts), depth: depth / pts.length, html, prio });
}
function hull(p) {     // 투영된 점들의 볼록 껍질 (마우스 판정용)
  const a = p.map(q => [q[0], q[1]]).sort((u, v) => u[0] - v[0] || u[1] - v[1]);
  const cross = (o, u, v) => (u[0] - o[0]) * (v[1] - o[1]) - (u[1] - o[1]) * (v[0] - o[0]);
  const lo = [], up = [];
  for (const q of a) { while (lo.length >= 2 && cross(lo[lo.length - 2], lo[lo.length - 1], q) <= 0) lo.pop(); lo.push(q); }
  for (let i = a.length - 1; i >= 0; i--) { const q = a[i]; while (up.length >= 2 && cross(up[up.length - 2], up[up.length - 1], q) <= 0) up.pop(); up.push(q); }
  return lo.slice(0, -1).concat(up.slice(0, -1));
}
function inside(pt, poly) {
  let c = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > pt[1]) !== (yj > pt[1]) && pt[0] < (xj - xi) * (pt[1] - yi) / (yj - yi) + xi) c = !c;
  }
  return c;
}

// ---------------------------------------------------------------- 렌더
let labels = [];
let playhead = 0, playing = false, speed = D.speeds[0][0], lastTs = null;

function render(ts) {
  if (camGoal) {   // 시점 전환 부드럽게
    let done = true;
    for (const k of ['az', 'el', 'dist', 'tx', 'ty', 'tz']) {
      const d = camGoal[k] - cam[k]; cam[k] += d * 0.15; if (Math.abs(d) > 1e-3) done = false;
    }
    if (done) { Object.assign(cam, camGoal); camGoal = null; }
  }
  if (spin && !dragging) cam.az += 0.0025;
  setupCamera();
  items = []; labels = []; hits = [];
  const i = Math.min(N - 1, Math.floor(playhead)), a = playhead - i;
  const fa = D.frames[i], fb = D.frames[Math.min(N - 1, i + 1)];
  const pulse = 0.5 + 0.5 * Math.sin((ts || 0) / 220);
  const T = D.rt ? fa.t + a * (fb.t - fa.t) : null;          // 실시간 모드: 프레임 사이 연속 시각 [h]

  addFloor(); addWalls(); addOrders(fa); addMachines(fa, pulse, T); addWorkers(fa, fb, a, T);
  if (T != null && D.rt.amrs.length) { addAmrs(T); addShelves(fa); }

  items.sort((p, q) => (p.layer - q.layer) || (q.depth - p.depth));
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  ctx.clearRect(0, 0, W, H);
  for (const it of items) {
    ctx.globalAlpha = it.alpha || 1;
    if (it.t === 'poly') {
      ctx.beginPath(); ctx.moveTo(it.pp[0][0], it.pp[0][1]);
      for (let k = 1; k < it.pp.length; k++) ctx.lineTo(it.pp[k][0], it.pp[k][1]);
      ctx.closePath(); ctx.fillStyle = it.fill; ctx.fill();
      if (it.stroke) { ctx.strokeStyle = it.stroke; ctx.lineWidth = 0.8; ctx.stroke(); }
    } else {
      ctx.beginPath(); ctx.arc(it.q[0], it.q[1], Math.max(1.5, it.r), 0, Math.PI * 2);
      ctx.fillStyle = it.fill; ctx.fill();
      if (it.stroke) { ctx.strokeStyle = it.stroke; ctx.lineWidth = 1.5; ctx.stroke(); }
    }
  }
  ctx.globalAlpha = 1;
  drawLabels();
  updateHud(fa, i, T);
}
function drawLabels() {
  const L = labels.map(l => Object.assign(l, { q: proj(l.x, l.y, l.z) })).filter(l => l.q).sort((a, b) => b.q[2] - a.q[2]);
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  for (const l of L) {
    const k = Math.max(0.75, Math.min(1.25, 14 / l.q[2]));
    ctx.font = `${l.bold ? '700 ' : ''}${Math.round(l.size * k)}px "Malgun Gothic","Apple SD Gothic Neo","Noto Sans KR",sans-serif`;
    const txt = l.text, w = ctx.measureText(txt).width;
    if (l.chip || l.bold) {
      ctx.fillStyle = 'rgba(255,255,255,.85)';
      const h = l.size * k + 6;
      ctx.beginPath(); ctx.roundRect ? ctx.roundRect(l.q[0] - w / 2 - 5, l.q[1] - h / 2, w + 10, h, 4) : ctx.rect(l.q[0] - w / 2 - 5, l.q[1] - h / 2, w + 10, h);
      ctx.fill();
    }
    ctx.fillStyle = l.floor ? '#334155' : '#0f172a';
    ctx.fillText(txt, l.q[0], l.q[1]);
  }
}
function fmtT(T) {      // 달력 h -> "Day 2 10:03:27 (화)" (t=0 = Day 1 09:00)
  const h = T + 9; let day = Math.floor(h / 24) + 1, sec = Math.round((h - (day - 1) * 24) * 3600);
  if (sec >= 86400) { day += 1; sec -= 86400; }
  const p = n => String(n).padStart(2, '0');
  return `Day ${day} ${p(Math.floor(sec / 3600))}:${p(Math.floor(sec / 60) % 60)}:${p(sec % 60)} (${D.dayNames[(day - 1) % 7]})`;
}
const CIRC = ['', '①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧', '⑨'];
const KIND = { WALK: '빈손', HANDOFF: '선반에 놓기', RECEIVE: '선반에서 꺼내기', TRANSPORT: '운반', AMR: '빈 차' };
const locName = (r, door) => D.rooms[r].short + (door ? ' 문' : '');
function leftText(e, T) { const left = Math.max(0, Math.round((e - T) * 3600)); return `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`; }
function moveText(id, g, T) {    // "PP2 ② 선반에 놓기 후공정실→후공정실 문 B00003-W1 (10건) · 남은 0:03"
  const m = /^(WALK|HANDOFF|RECEIVE|TRANSPORT)_?(\d)?\S*\s+(.*)$/.exec(g[7]) || [null, '', '', g[7]];
  const what = m[1] === 'WALK' ? '' : ' ' + m[3];
  return `${id} ${CIRC[+m[2]] || ''} ${KIND[m[1]] || ''} ${locName(g[3], g[4])}→${locName(g[5], g[6])}${what} · 남은 ${leftText(g[1], T)}`;
}
function updateHud(fr, i, T) {
  const t = T != null ? fmtT(T) : fr.l;
  document.getElementById('hudT').textContent = t;
  document.getElementById('hudV').textContent = '실물 없음(전산): ' + D.virtualGroups.map((g, k) => `${g} ${fr.v[k]}`).join(' · ');
  if (T != null) {
    const moves = [];
    D.rt.workers.forEach(w => { const k = lastStart(w.segs, T); if (k >= 0 && w.segs[k][2] && T < w.segs[k][1]) moves.push(moveText(w.id, w.segs[k], T)); });
    const amrs = D.rt.amrs.map((a, k) => { const s = rtAmr(k, T); return s.g ? `${a.id} ${s.task} ${s.g[6].replace(/^\S+\s/, '')} · 남은 ${leftText(s.g[1], T)}` : null; }).filter(Boolean);
    const nj = D.rt.jumps.filter(J => J[1] <= T).length;
    document.getElementById('hudM').innerHTML = (moves.length ? '🚶 ' + moves.join(' / ') : '이동 중인 작업자 없음') +
      (D.rt.amrs.length ? '<br>🤖 ' + (amrs.length ? amrs.join(' / ') : 'AMR 대기') : '') +
      (D.rt.jumps.length ? `<br><span class="j">⚡ 순간이동 ${nj}건</span> / 이 구간 ${D.rt.jumps.length}건 — 이동 이벤트 없이 위치 변경`
                         : '<br>✓ 순간이동 0건 — 작업자 빈손 이동까지 모두 모델에 있음');
  }
  document.getElementById('hudK').innerHTML =
    `<span>WIP <b>${fr.k[0]}</b>건</span><span>누적 완료 <b>${fr.k[1]}</b>건</span>` +
    `<span>납기 지연(미완료) <b>${fr.k[2]}</b>건</span><span>레진 누적 <b>${fr.k[3].toFixed(1)}</b> L</span>`;
  document.getElementById('now').textContent = T != null ? t : `${t}  (${i + 1}/${N})`;
  if (document.activeElement !== slider) slider.value = i;
}

function loop(ts) {
  if (playing) {
    if (lastTs !== null) {
      playhead += (ts - lastTs) / (D.frameMs / speed);
      if (playhead >= N - 1) { playhead = N - 1; setPlaying(false); }
    }
    lastTs = ts;
  }
  render(ts);
  requestAnimationFrame(loop);
}
function setPlaying(p) {
  playing = p; lastTs = null;
  if (p && playhead >= N - 1) playhead = 0;
  playBtn.textContent = p ? '⏸ 정지' : '▶ 재생';
}

// ---------------------------------------------------------------- UI
playBtn.onclick = () => setPlaying(!playing);
slider.oninput = () => { playhead = +slider.value; lastTs = null; };
const sp = document.getElementById('speeds');
D.speeds.forEach(([v, lbl], k) => {
  const b = document.createElement('button'); b.textContent = lbl; if (k === 0) b.classList.add('on');
  b.onclick = () => { speed = v; [...sp.children].forEach(c => c.classList.toggle('on', c === b)); };
  sp.appendChild(b);
});
const ticks = document.getElementById('ticks');
D.dayTicks.forEach(([idx, lbl]) => {
  const s = document.createElement('span'); s.textContent = lbl;
  s.style.left = (N > 1 ? 100 * idx / (N - 1) : 0) + '%'; ticks.appendChild(s);
});
const lg = document.getElementById('legend');
lg.innerHTML = D.states.map(s => `<span><i style="background:${s.color}"></i>${s.key} (${s.label})</span>`).join('') +
  `<span><i class="c" style="background:${WORKER_BUSY}"></i>작업자: 작업 중</span><span><i class="c" style="background:#fff"></i>작업자: 대기</span>` +
  `<span><i style="background:${ORDER_PROC}"></i>주문: 작업 중</span><span><i style="background:${ORDER_WAIT}"></i>주문: 대기</span>` +
  (D.rt ? `<span><i style="background:${ORDER_WAIT}"></i>손·AMR 위 주황 상자: 운반 중</span>` +
          (D.rt.amrs.length ? `<span><i style="background:#334155"></i>AMR (복도 전용)</span><span><i style="background:#cbd5e1"></i>문 앞 선반</span>` : '') +
          `<span style="color:#b91c1c">⚡ 순간이동 = 이동 이벤트 없이 위치 변경</span>` : '');
document.getElementById('vHome').onclick = () => { spin = false; vSpin.classList.remove('on'); camGoal = Object.assign({}, HOME); };
document.getElementById('vTop').onclick = () => { spin = false; vSpin.classList.remove('on'); camGoal = Object.assign({}, TOP); };
const vSpin = document.getElementById('vSpin');
vSpin.onclick = () => { spin = !spin; vSpin.classList.toggle('on', spin); };

// 마우스: 회전 · 이동 · 확대 · 툴팁
let dragging = false, dragMode = null, lx = 0, ly = 0;
cv.addEventListener('contextmenu', e => e.preventDefault());
cv.addEventListener('mousedown', e => {
  dragging = true; camGoal = null; dragMode = (e.button === 2 || e.shiftKey) ? 'pan' : 'rot';
  lx = e.clientX; ly = e.clientY; cv.classList.add('drag');
});
window.addEventListener('mouseup', () => { dragging = false; cv.classList.remove('drag'); });
window.addEventListener('mousemove', e => {
  if (dragging) {
    const dx = e.clientX - lx, dy = e.clientY - ly; lx = e.clientX; ly = e.clientY;
    if (dragMode === 'rot') {
      cam.az -= dx * 0.008;
      cam.el = Math.max(0.12, Math.min(1.53, cam.el + dy * 0.006));
    } else {
      const k = cam.dist / 900;
      cam.tx -= (dx * Math.cos(cam.az) + dy * Math.sin(cam.az)) * k;
      cam.tz -= (dx * Math.sin(cam.az) - dy * Math.cos(cam.az)) * k;
    }
    tip.style.display = 'none';
    return;
  }
  const r = cv.getBoundingClientRect(), pt = [e.clientX - r.left, e.clientY - r.top];
  if (pt[0] < 0 || pt[1] < 0 || pt[0] > r.width || pt[1] > r.height) { tip.style.display = 'none'; return; }
  const cand = hits.filter(h => inside(pt, h.pts)).sort((a, b) => (b.prio - a.prio) || (a.depth - b.depth));
  if (!cand.length) { tip.style.display = 'none'; return; }
  tip.innerHTML = cand[0].html; tip.style.display = 'block';
  const tw = tip.offsetWidth, th = tip.offsetHeight;
  tip.style.left = Math.min(pt[0] + 14, r.width - tw - 6) + 'px';
  tip.style.top = Math.min(pt[1] + 14, r.height - th - 6) + 'px';
});
cv.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
cv.addEventListener('wheel', e => {
  e.preventDefault(); camGoal = null;
  cam.dist = Math.max(5, Math.min(30, cam.dist * Math.exp(e.deltaY * 0.001)));
}, { passive: false });
window.addEventListener('keydown', e => { if (e.code === 'Space' && e.target === document.body) { e.preventDefault(); setPlaying(!playing); } });
window.addEventListener('resize', resize);

resize();
requestAnimationFrame(loop);
</script></body></html>
"""
