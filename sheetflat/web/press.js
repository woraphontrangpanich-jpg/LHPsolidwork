/* Press brake simulation: which bend first, how the part sits on the machine, and where the tools are.

   No three.js here (tested with Node). Units: mm. Machine frame: X along the bend line (the length of
   the tools), Y up, Z towards the operator. The die's top (shoulders) is at Y = 0; the V groove is
   centred on Z = 0; the punch comes down along Y.

   How a bend is made on a press brake (air bending):
     1. the operator lays the part flat on the die, pushed back against the backgauge fingers, so the
        bend line sits over the middle of the V;
     2. the punch comes down fast to touch the sheet, then presses slowly; the sheet bends around the
        punch tip and both sides of the sheet swing up (the side in front rises towards the operator);
     3. the punch goes a little past the wanted angle (over-bend) and comes back up: the steel springs
        back to the wanted angle;
     4. the part is taken out, turned or flipped for the next bend.
   The punch always pushes the inside of the bend: a bend "down" in the drawing is made with the part
   flipped over. A 180° hem is made in two hits: first an acute bend (30° inside) with acute tools, then
   the flange is pressed flat between flat tools.

   Bend order: every order is tried (up to 12 bends) and scored: hitting the tools or machine is the
   worst; then flipping/turning the part between hits (handling time); hems go first (they need the
   part still flat); outer flanges before the ones closer to the middle (outside-in); the bigger side
   in front of the operator, the smaller side against the backgauge. */
import { mapPoint } from "./fold.js";

const D2R = Math.PI / 180;
export const SPRINGBACK = 2 * D2R;          // over-bend shown before the punch lets go
export const HEM_PRE = 150 * D2R;           // first hit of a hem: 30° inside

const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const scale = (a, k) => [a[0] * k, a[1] * k, a[2] * k];
const unit = (a) => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
const eqArr = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);

/* ------------------------------------------------------------------ tool profiles (Z, Y) */
// punches: tip at (0, 0); dies: shoulders at Y = 0. Extruded along X by the viewer.
export function toolProfiles(V) {
  const t44 = Math.tan(42.5 * D2R), t15 = Math.tan(13 * D2R), d44 = Math.tan(44 * D2R), d15 = Math.tan(15 * D2R);
  const dStd = V / 2 / d44, dAcute = Math.min(55, V / 2 / d15);
  const Dw = Math.max(V * 1.5, 14);
  const clamp = [[-22, 110], [22, 110], [22, 150], [-22, 150]];
  const ram = [[-35, 150], [35, 150], [35, 330], [-35, 330]];       // upper beam (a plate about 70 mm thick)
  return {
    V,
    punch: {
      std: { name: "มีดตรง 85°", polys: [[[0, 0], [7, 7 / t44], [7, 110], [-7, 110], [-7, 7 / t44]], clamp, ram] },
      goose: { name: "มีดคอห่าน", polys: [[[0, 0], [5, 5.2], [5, 12], [-18, 32], [-18, 88], [7, 104], [7, 110], [-7, 110], [-38, 95], [-38, 30], [-5, 5.2]], clamp, ram] },
      acute: { name: "มีดแหลม 26°", polys: [[[0, 0], [7, 7 / t15], [7, 110], [-7, 110], [-7, 7 / t15]], clamp, ram] },
      flat: { name: "มีดตบ", polys: [[[-32, 0], [32, 0], [32, 110], [-32, 110]], clamp, ram] },
    },
    die: {
      std: { name: `ดาย V${fmtV(V)} 88°`, polys: [[[-Dw, 0], [-V / 2, 0], [0, -dStd], [V / 2, 0], [Dw, 0], [Dw, -60], [-Dw, -60]], ...bed()] },
      acute: { name: `ดาย V${fmtV(V)} 30°`, polys: [[[-Dw, 0], [-V / 2, 0], [0, -dAcute], [V / 2, 0], [Dw, 0], [Dw, -60], [-Dw, -60]], ...bed()] },
      flat: { name: "ดายตบ", polys: [[[-40, 0], [40, 0], [40, -60], [-40, -60]], ...bed()] },
    },
  };
}
const fmtV = (v) => String(Math.round(v * 10) / 10);
const bed = () => [[[-36, -60], [36, -60], [36, -110], [-36, -110]], [[-30, -110], [30, -110], [30, -260], [-30, -260]]];

function inPoly(z, y, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [zi, yi] = poly[i], [zj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && z < ((zj - zi) * (y - yi)) / (yj - yi) + zi) inside = !inside;
  }
  return inside;
}

/* ------------------------------------------------------------------ the part */
function pointInRegion(x, y, poly) {
  if (!inPoly(x, y, poly.outer)) return false;
  for (const h of poly.holes) if (inPoly(x, y, h)) return false;
  return true;
}

/* samples of the part (outline and a grid inside, both faces) used to test for collisions */
export function prepare(pc, t) {
  const h = t / 2, regions = pc.regions, bends = pc.bends;
  let lo = [1e9, 1e9], hi = [-1e9, -1e9];
  for (const R of regions) for (const p of R.polys) for (const [x, y] of p.outer) {
    lo = [Math.min(lo[0], x), Math.min(lo[1], y)]; hi = [Math.max(hi[0], x), Math.max(hi[1], y)];
  }
  const diag = Math.hypot(hi[0] - lo[0], hi[1] - lo[1]);
  const step = Math.max(2 * t, diag / 30);
  let samples = [];
  {
    regions.forEach((R, ri) => {
      if (R.kind === "bend" && ri % 4) return;              // a few strips of each bend zone are enough
      for (const p of R.polys) {
        for (const loop of [p.outer, ...p.holes]) {
          for (let i = 0; i < loop.length; i++) {
            const a = loop[i], b = loop[(i + 1) % loop.length];
            const n = Math.max(1, Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / step));
            for (let k = 0; k < n; k++) samples.push({ x: a[0] + (b[0] - a[0]) * k / n, y: a[1] + (b[1] - a[1]) * k / n, r: ri });
          }
        }
        if (R.kind === "flange") {
          let l0 = [1e9, 1e9], l1 = [-1e9, -1e9];
          for (const [x, y] of p.outer) { l0 = [Math.min(l0[0], x), Math.min(l0[1], y)]; l1 = [Math.max(l1[0], x), Math.max(l1[1], y)]; }
          for (let x = l0[0] + step / 2; x < l1[0]; x += step) for (let y = l0[1] + step / 2; y < l1[1]; y += step)
            if (pointInRegion(x, y, p)) samples.push({ x, y, r: ri });
        }
      }
    });
  }
  // keep the test fast: at most ~450 points per face, spread over the whole part
  if (samples.length > 450) { const k = samples.length / 450; samples = Array.from({ length: 450 }, (_, i) => samples[Math.floor(i * k)]); }
  const S = [];
  for (const s of samples) { S.push({ ...s, w: h }); S.push({ ...s, w: -h }); }
  const info = bends.map((b) => {
    const child = regions.findIndex((R) => R.kind === "flange" && eqArr(R.chain, b.chain));
    const parent = regions.findIndex((R) => R.kind === "flange" && eqArr(R.chain, b.chain.slice(1)));
    const ext = (ri, sign) => {
      let m = 0;
      if (ri < 0) return 0;
      for (const p of regions[ri].polys) for (const [x, y] of p.outer) {
        const u = (x - b.start[0]) * b.d[0] + (y - b.start[1]) * b.d[1];
        m = Math.max(m, Math.abs(u - b.ba / 2) - b.ba / 2);
        void sign;
      }
      return m;
    };
    const v = (b.mid[0] - b.start[0]) * b.ax[0] + (b.mid[1] - b.start[1]) * b.ax[1];
    const deeper = bends.filter((c) => c.id !== b.id && c.chain.includes(b.id)).map((c) => c.id);
    return { child, parent, childLen: ext(child, 1), parentLen: ext(parent, -1), v, hem: b.theta > 179 * D2R, deeper };
  });
  const minFlange = Math.min(...info.flatMap((i) => [i.childLen, i.parentLen]).filter((x) => x > 0), 1e9);
  // V opening: about 8 × thickness, smaller when a flange is too short to span the V
  let V = Math.max(6, Math.round(8 * t));
  if (minFlange < 0.7 * V) V = Math.max(4, Math.round((minFlange / 0.7) * 2) / 2);
  const ctr = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2];
  return { pc, t, h, samples: S, info, diag, V, tools: toolProfiles(V), ctr };
}

/* ------------------------------------------------------------------ where the part sits */
const region = (P, b, side) => side === "bend" ? { kind: "bend", bend: b.id, chain: b.chain }
  : { kind: "flange", chain: side === "child" ? b.chain : b.chain.slice(1) };

function mapAt(P, angles, b, u, v, w, side) {
  const x = b.start[0] + b.d[0] * u + b.ax[0] * v, y = b.start[1] + b.d[1] * u + b.ax[1] * v;
  return mapPoint(x, y, w, region(P, b, side), P.pc.bends, angles);
}

/* Machine frame for bend j at the current angles. place = +1: the child side (the flange that moves in
   the drawing) faces the operator, -1: it faces the backgauge. Returns rows X, Y, Z (machine axes in
   part coordinates), the part point at the punch tip p0, and its height y0 (sheet resting on the die). */
export function frameFor(P, angles, j, place) {
  const b = P.pc.bends[j], I = P.info[j], h = P.h, e = 0.01;
  const wIn = b.s * h, um = b.ba / 2;
  const p0 = mapAt(P, angles, b, um, I.v, wIn, "bend");
  const T = unit(sub(mapAt(P, angles, b, um + e, I.v, wIn, "bend"), mapAt(P, angles, b, um - e, I.v, wIn, "bend")));
  const A = unit(sub(mapAt(P, angles, b, um, I.v + e, wIn, "bend"), p0));
  const Nn = unit(sub(mapAt(P, angles, b, um, I.v, wIn + e, "bend"), mapAt(P, angles, b, um, I.v, wIn - e, "bend")));
  const Y = scale(Nn, b.s);                                   // the inside of the bend faces the punch
  let Z = scale(T, place);
  Z = unit(sub(Z, scale(Y, dot(Z, Y))));
  const X = cross(Y, Z);
  void A;
  const F = { X, Y, Z, p0, y0: 0 };
  // rest on the shoulders: the outer face crosses Z = ±V/2 at height 0
  const V2 = P.V / 2, wOut = -b.s * h, pts = [];
  const Lp = Math.min(I.parentLen, P.V * 3), Lc = Math.min(I.childLen, P.V * 3);
  const n = 24;
  for (let i = n; i > 0; i--) pts.push(toM(F, mapAt(P, angles, b, -Lp * i / n, I.v, wOut, "parent")));
  for (let i = 0; i <= 8; i++) pts.push(toM(F, mapAt(P, angles, b, b.ba * i / 8, I.v, wOut, "bend")));
  for (let i = 1; i <= n; i++) pts.push(toM(F, mapAt(P, angles, b, b.ba + Lc * i / n, I.v, wOut, "child")));
  const yAt = (zc) => {
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1], c = pts[i];
      if ((a[2] - zc) * (c[2] - zc) <= 0 && a[2] !== c[2]) return a[1] + (c[1] - a[1]) * (zc - a[2]) / (c[2] - a[2]);
    }
    return null;
  };
  const ys = [yAt(V2), yAt(-V2)].filter((y) => y !== null);
  F.y0 = ys.length ? -Math.min(...ys) : -Math.min(...pts.map((p) => p[1]));
  return F;
}

/* the second hit of a hem: the parent side lies flat on the flat die, the hem edge towards the back */
export function frameFlat(P, angles, j) {
  const b = P.pc.bends[j], I = P.info[j], h = P.h, e = 0.01;
  const wIn = b.s * h;
  const p0 = mapAt(P, angles, b, -e, I.v, -b.s * h, "parent");
  const T = unit(sub(mapAt(P, angles, b, -e, I.v, wIn, "parent"), mapAt(P, angles, b, -1 - e, I.v, wIn, "parent")));
  const Nn = unit(sub(mapAt(P, angles, b, -e, I.v, wIn, "parent"), mapAt(P, angles, b, -e, I.v, -wIn, "parent")));
  const Y = Nn;
  const Z = unit(sub(scale(T, -1), scale(Y, dot(scale(T, -1), Y))));
  return { X: cross(Y, Z), Y, Z, p0, y0: 0 };
}

export const toM = (F, p) => { const q = sub(p, F.p0); return [dot(F.X, q), dot(F.Y, q) + F.y0, dot(F.Z, q)]; };

/* ------------------------------------------------------------------ collision test */
export function hits(P, angles, F, j, punchKey, dieKey, tipY, detail) {
  const b = P.pc.bends[j], I = P.info[j], tools = P.tools;
  const near = P.V / 2 + b.ba / 2 + 3 * P.t, tol = 0.25 * P.t;
  const pPolys = tools.punch[punchKey].polys, dPolys = tools.die[dieKey].polys;
  let n = 0;
  for (const s of P.samples) {
    const R = P.pc.regions[s.r];
    // sheet lying along the bend line is the sheet being bent (in the V, on the shoulders, under the tip)
    const u = (s.x - b.start[0]) * b.d[0] + (s.y - b.start[1]) * b.d[1];
    if (Math.abs(u - b.ba / 2) < near && (s.r === I.child || s.r === I.parent || R.chain.length <= b.chain.length)) continue;
    // material beyond the ends of the bend line, on the far side of it (an L-shaped flat around the end
    // of the bend): segmented tools leave it out, it is not checked
    const childSide = R.chain.includes(j);
    if ((childSide && u < b.ba / 2) || (!childSide && u > b.ba / 2)) continue;
    const m = toM(F, mapPoint(s.x, s.y, s.w, R, P.pc.bends, angles));
    const z = m[2], y = m[1];
    // the sheet being bent lies in the V and rests on the shoulders: that is contact, not a collision
    if ((s.r === I.child || s.r === I.parent || (R.kind === "bend" && R.bend === j)) && dieKey !== "flat" && Math.abs(z) < P.V / 2 + P.t && y < P.t) continue;
    if (n > 2 && !detail) break;                              // a few touching points are noise; more is a hit
    if (dPolys.some((pl) => inPoly(z, y + tol, pl))) { n++; if (detail) detail.push(["die", s.r, z.toFixed(1), y.toFixed(1)]); continue; }
    if (pPolys.some((pl) => inPoly(z, y - tipY - tol, pl))) { n++; if (detail) detail.push(["punch", s.r, z.toFixed(1), (y - tipY).toFixed(1)]); }
  }
  return n;
}

const doneAngles = (P, mask) => P.pc.bends.map((b, i) => (mask >> i) & 1 ? b.theta : 0);

/* one hit: bend j with the bends in mask already made; tries the straight punch, then the gooseneck */
export function evalStep(P, mask, j, place) {
  const b = P.pc.bends[j], I = P.info[j];
  const ang = doneAngles(P, mask);
  const target = I.hem ? HEM_PRE : b.theta;
  // inside angle at the over-bend decides the tools: 85° punch / 88° die, or 26° punch / 30° die
  const acute = I.hem || Math.PI - (target + SPRINGBACK) < 87 * D2R;
  const keys = acute ? [["acute", "acute"]] : [["std", "std"], ["goose", "std"]];
  let best = null;
  for (const [pk, dk] of keys) {
    let n = 0;
    for (const f of P.pc.bends.length <= 6 ? [0.5, 1] : [1]) {
      const a = ang.slice(); a[j] = Math.min(Math.PI, (target + SPRINGBACK) * f);
      const F = frameFor(P, a, j, place);
      n += hits(P, a, F, j, pk, dk, F.y0);
    }
    if (I.hem) {
      const a = ang.slice(); a[j] = b.theta;
      const F = frameFlat(P, a, j);
      n += hits(P, a, F, j, "flat", "flat", topOf(P, a, F, j));
    }
    if (!best || n < best.n) best = { n, punch: pk, die: dk };
    if (n <= 2) break;
  }
  return best;
}

/* height of the top of the hem flange (where the flat punch stops) */
export function topOf(P, angles, F, j) {
  const b = P.pc.bends[j], I = P.info[j];
  let m = 2 * P.t;
  for (const s of P.samples) if (s.r === I.child) m = Math.max(m, toM(F, mapPoint(s.x, s.y, s.w, P.pc.regions[s.r], P.pc.bends, angles))[1]);
  void b;
  return m;
}

/* how much of the part is on each side of the bend line (bigger side should face the operator) */
function sideMass(P, j) {
  const b = P.pc.bends[j];
  let child = 0, parent = 0;
  for (const s of P.samples) if (s.w > 0) (P.pc.regions[s.r].chain.includes(j) ? child++ : parent++);
  void b;
  return { child, parent };
}

/* ------------------------------------------------------------------ bend order */
export function planSequence(P) {
  const bends = P.pc.bends, n = bends.length;
  if (!n) return { steps: [], order: [] };
  const mass = bends.map((_, j) => sideMass(P, j));
  const memo = new Map();
  const step = (mask, j, place) => {
    const k = `${mask}|${j}|${place}`;
    if (!memo.has(k)) memo.set(k, evalStep(P, mask, j, place));
    return memo.get(k);
  };
  const upMemo = new Map();
  const upOf = (mask, j, place, folded) => {
    const k = `${mask}|${j}|${place}|${folded ? 1 : 0}`;
    if (!upMemo.has(k)) {
      const a = doneAngles(P, mask); if (folded) a[j] = bends[j].theta;
      upMemo.set(k, frameFor(P, a, j, place));
    }
    return upMemo.get(k);
  };
  const handling = (prev, next) => {
    if (!prev) return { cost: 0, flip: false, turn: false };
    const flip = dot(prev.Y, next.Y) < 0;
    const turn = dot(prev.Z, next.Z) < 0.5;
    return { cost: (flip ? 4 : 0) + (turn ? 1.5 : 0), flip, turn };
  };
  const stepCost = (mask, j, place, prevF) => {
    const I = P.info[j], r = step(mask, j, place);
    let c = r.n > 2 ? 100 + r.n * 0.01 : 0;
    if (I.hem) for (let i = 0; i < n; i++) if ((mask >> i) & 1 && !P.info[i].hem) c += 15;
    c += 0.6 * I.deeper.filter((d) => !((mask >> d) & 1)).length;          // outside-in
    const front = place > 0 ? mass[j].child : mass[j].parent, back = place > 0 ? mass[j].parent : mass[j].child;
    if (front < back) c += back > 2 * front ? 5 : 1;               // short flange to the backgauge, the big side to the operator
    if (r.punch === "goose") c += 0.2;
    const F = upOf(mask, j, place, false);
    const hd = handling(prevF, F);
    return { c: c + hd.cost, r, F, hd };
  };
  let best;
  if (n <= 12) {
    // shortest path over (bends done, last hit)
    const full = (1 << n) - 1;
    const dist = new Map(), from = new Map();
    const key = (mask, j, pl) => `${mask}|${j}|${pl}`;
    dist.set(key(0, -1, 0), 0);
    const frames = new Map();
    for (let mask = 0; mask <= full; mask++) {
      const cnt = popcount(mask);
      void cnt;
      for (let j = -1; j < n; j++) for (const pl of j < 0 ? [0] : [1, -1]) {
        const k = key(mask, j, pl), d0 = dist.get(k);
        if (d0 === undefined) continue;
        let prevF = null;
        if (j >= 0) {
          const fk = key(mask, j, pl);
          if (!frames.has(fk)) frames.set(fk, upOf(mask & ~(1 << j), j, pl, true));
          prevF = frames.get(fk);
        }
        for (let nj = 0; nj < n; nj++) {
          if ((mask >> nj) & 1) continue;
          for (const npl of [1, -1]) {
            const sc = stepCost(mask, nj, npl, prevF);
            const nk = key(mask | (1 << nj), nj, npl), nd = d0 + sc.c;
            if (dist.get(nk) === undefined || nd < dist.get(nk) - 1e-9) { dist.set(nk, nd); from.set(nk, [k, nj, npl, sc]); }
          }
        }
      }
    }
    let bk = null, bd = Infinity;
    for (let j = 0; j < n; j++) for (const pl of [1, -1]) { const d = dist.get(key(full, j, pl)); if (d !== undefined && d < bd) { bd = d; bk = key(full, j, pl); } }
    const seq = [];
    for (let k = bk; from.has(k); k = from.get(k)[0]) { const [, j, pl, sc] = from.get(k); seq.unshift({ j, pl, sc }); }
    best = seq;
  } else {
    best = []; let mask = 0, prevF = null;
    for (let i = 0; i < n; i++) {
      let pick = null;
      for (let j = 0; j < n; j++) if (!((mask >> j) & 1)) for (const pl of [1, -1]) {
        const sc = stepCost(mask, j, pl, prevF);
        if (!pick || sc.c < pick.sc.c) pick = { j, pl, sc };
      }
      best.push(pick); mask |= 1 << pick.j;
      prevF = upOf(mask & ~(1 << pick.j), pick.j, pick.pl, true);
    }
  }
  let mask = 0;
  const steps = best.map(({ j, pl, sc }, k) => {
    const s = { bend: j, place: pl, mask, punch: sc.r.punch, die: sc.r.die, hem: P.info[j].hem, ok: sc.r.n <= 2,
      short: Math.min(P.info[j].childLen || 1e9, P.info[j].parentLen || 1e9) < P.V / 2,
      flip: k > 0 && sc.hd.flip, turn: k > 0 && sc.hd.turn };
    mask |= 1 << j;
    return s;
  });
  return { steps, order: steps.map((s) => s.bend) };
}

function popcount(x) { let c = 0; while (x) { x &= x - 1; c++; } return c; }

/* ------------------------------------------------------------------ the timeline of one hit */
// seconds; a hem adds a second hit on the flat tools
export const PHASES = [
  ["load", 1.5, "วางชิ้นงานบนดาย ดันชนฉากหลัง (backgauge)"],
  ["approach", 0.45, "มีดลงมาแตะแผ่น"],
  ["press", 1.3, "กดพับ แผ่นยกตัวขึ้นทั้งสองข้าง"],
  ["release", 0.6, "มีดขึ้น เหล็กเด้งกลับ (springback) ได้มุมพอดี"],
];
export const HEM_PHASES = [
  ["toflat", 1.2, "ย้ายไปชุดตบ"],
  ["flatten", 1.0, "ตบพับให้แนบ (hem)"],
  ["flatrelease", 0.5, "มีดขึ้น"],
];
export const stepPhases = (s) => (s.hem ? [...PHASES, ...HEM_PHASES] : PHASES);
export const stepSeconds = (s) => stepPhases(s).reduce((a, p) => a + p[1], 0);

/* where everything is at local time tau (s) of a hit */
export function phaseAt(s, tau) {
  const ph = stepPhases(s);
  let t0 = 0;
  for (let i = 0; i < ph.length; i++) {
    const [name, d, text] = ph[i];
    if (tau <= t0 + d || i === ph.length - 1) return { name, f: Math.min(1, Math.max(0, (tau - t0) / d)), text, i };
    t0 += d;
  }
  return null;
}
