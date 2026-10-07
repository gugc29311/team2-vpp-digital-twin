# -*- coding: utf-8 -*-
"""
3D Factory Replay [명세서 13절]: data.replay_states() 의 시각별 상태 -> 브라우저 3D 화면 (HTML 한 장).

  2D Replay (replay.py) 와 같은 데이터·같은 방 배치(data.ROOM_LAYOUT, ⚠️ 임의 배치)를 3D 로 그린다.
  - 방 = 바닥 + 낮은 벽, 설비 = 상자 (상태 6종 색), 작업자 = 사람 모양 (작업 중 / 대기, 방 이동은 복도 경유),
    주문 = 방 안의 작은 상자 (작업 중 · 대기), 방 위에 주문 수와 설비 대기열
  - 전체 기간 연속 재생: Play / Pause / 속도 ×1 ×2 ×5 ×10 / 타임라인 슬라이더 (날짜 눈금)
  - 마우스: 드래그 = 회전, 휠 = 확대/축소, 오른쪽 드래그(또는 Shift+드래그) = 이동, 올리면 상태 표시
  외부 라이브러리 없이 Canvas 로 직접 그림 (인터넷 연결 없이 동작).
"""
import json

from dashboard.data import ROOM_LABELS, ROOM_LAYOUT
from dashboard.replay import (MAX_IDS, QUEUE_TEXT, STATE_COLORS, STATE_LABELS, _machine_positions,
                              _queue_key, _worker_positions)

_STATES = list(STATE_COLORS)


def _kind(unit):
    if unit.startswith("WASH"):
        return "wash"
    if unit.startswith("UV"):
        return "uv"
    return "printer"


def build_payload(states, frame_ms=400):
    """시각별 상태 목록 -> 3D 화면용 JSON 직렬화 가능한 dict (정적 배치 + 프레임별 변화)."""
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
                text = f"이동 대기 주문 {n}"
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
            "l": s["label"],
            "k": [s["wip"], s["completed"], s["late"], round(s["resin_L"], 1)],
            "m": [_STATES.index(s["machines"][u]["state"]) for u in units],
            "w": wdata,
            "r": rdata,
        })

    return {
        "frameMs": frame_ms,
        "states": [{"key": st, "label": STATE_LABELS[st], "color": STATE_COLORS[st]} for st in _STATES],
        "rooms": [{"key": r, "label": ROOM_LABELS[r], "box": list(ROOM_LAYOUT[r]), "corridor": r == "Corridor"}
                  for r in rooms],
        "machines": [{"id": u, "x": mpos[u][0], "y": mpos[u][1], "kind": _kind(u)} for u in units],
        "workers": workers,
        "dayTicks": day_ticks,
        "frames": frames,
    }


def replay3d_html(states, frame_ms=400, height=760):
    """3D Replay HTML (streamlit.components.v1.html 로 띄움)."""
    payload = json.dumps(build_payload(states, frame_ms), ensure_ascii=False, separators=(",", ":"))
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
  <div id="hud" class="panel"><div class="t" id="hudT"></div><div class="k" id="hudK"></div></div>
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
function addMachines(fr, pulse) {
  D.machines.forEach((m, i) => {
    const st = fr.m[i], color = SC[st], x = m.x, z = m.y;
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
function addWorkers(fa, fb, a) {
  D.workers.forEach((id, j) => {
    const [x, z] = workerPos(fa, fb, j, a);
    const busy = fa.w[j][2] === 1, task = fa.w[j][4];
    const body = busy ? WORKER_BUSY : '#f8fafc';
    box(x - 0.11, 0, z - 0.09, x + 0.11, 0.36, z + 0.09, body, { stroke: 'rgba(13,148,136,.9)' });
    circle(x, 0.47, z, 0.1, busy ? '#99f6e4' : '#ffffff', WORKER_BUSY);
    labels.push({ x, y: 0.72, z, text: id, size: 10 });
    const corners = [[x - 0.14, 0, z], [x + 0.14, 0, z], [x + 0.14, 0.6, z], [x - 0.14, 0.6, z]];
    pushHitBox(corners, `<b>${id}</b> · ${task || '대기 (작업 없음)'}`, 2);
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
    const total = proc + wait, shown = Math.min(total, maxShow);
    for (let k = 0; k < shown; k++) {
      const layer = Math.floor(k / perLayer), rem = k % perLayer, row = Math.floor(rem / cols), col = rem % cols;
      const x = xs + col * gap, z = zs - row * gap * (r.corridor ? -1 : 1), y = layer * (s + 0.01);
      box(x, y, z - s / 2, x + s, y + s, z + s / 2, k < proc ? ORDER_PROC : ORDER_WAIT, { stroke: 'rgba(15,23,42,.45)' });
    }
    if (total > shown) labels.push({ x: xs + cols * gap * 0.5, y: 3 * (s + 0.01) + 0.15, z: zs, text: `+${total - shown}`, size: 11, bold: true });
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
let playhead = 0, playing = false, speed = 1, lastTs = null;

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

  addFloor(); addWalls(); addOrders(fa); addMachines(fa, pulse); addWorkers(fa, fb, a);

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
  updateHud(fa, i);
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
function updateHud(fr, i) {
  document.getElementById('hudT').textContent = fr.l;
  document.getElementById('hudK').innerHTML =
    `<span>WIP <b>${fr.k[0]}</b>건</span><span>누적 완료 <b>${fr.k[1]}</b>건</span>` +
    `<span>납기 지연(미완료) <b>${fr.k[2]}</b>건</span><span>레진 누적 <b>${fr.k[3].toFixed(1)}</b> L</span>`;
  document.getElementById('now').textContent = `${fr.l}  (${i + 1}/${N})`;
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
[1, 2, 5, 10].forEach(v => {
  const b = document.createElement('button'); b.textContent = `×${v}`; if (v === 1) b.classList.add('on');
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
  `<span><i style="background:${ORDER_PROC}"></i>주문: 작업 중</span><span><i style="background:${ORDER_WAIT}"></i>주문: 대기</span>`;
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
