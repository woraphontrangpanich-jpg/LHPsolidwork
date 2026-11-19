/* The press brake on screen: die, punch, upper beam, backgauge, and the part moving through the hits
   planned by press.js. Used by viewer3d.js; one PressSim per part shown. */
import * as THREE from "./vendor/three.module.min.js";
import { mapPoint } from "./fold.js";
import { prepare, planSequence, frameFor, frameFlat, topOf, toM, stepPhases, stepSeconds, phaseAt, SPRINGBACK, HEM_PRE } from "./press.js";

const ease = (f) => (f <= 0 ? 0 : f >= 1 ? 1 : f * f * (3 - 2 * f));
const lerp = (a, b, f) => a + (b - a) * f;
const lerpV = (a, b, f) => a.clone().lerp(b, f);

export class PressSim {
  constructor(viewer, piece, t, plan) {
    this.v = viewer;
    this.pc = piece;
    this.P = prepare(piece, t);
    this.plan = plan || planSequence(this.P);
    this.steps = this.plan.steps;
    this.secs = this.steps.map(stepSeconds);
    this.total = this.secs.reduce((a, b) => a + b, 0);
    this.cache = new Map();
    this.group = new THREE.Group();
    this.group.visible = false;
    viewer.scene.add(this.group);
    this.build();
  }

  /* ------------------------------------------------------------------ machine */
  build() {
    // segmented tools: as long as the longest bend plus a margin (shops build the punch and die from segments)
    const P = this.P, L = Math.max(...this.pc.bends.map((b) => b.len || 0), 40) + 80;
    this.L = L;
    const steel = new THREE.MeshStandardMaterial({ color: 0xa9b3ba, metalness: 0.45, roughness: 0.4, side: THREE.DoubleSide });
    const beam = new THREE.MeshStandardMaterial({ color: 0x6f7d88, metalness: 0.3, roughness: 0.6, side: THREE.DoubleSide });
    const gaugeM = new THREE.MeshStandardMaterial({ color: 0xd9822b, metalness: 0.3, roughness: 0.5 });
    this.mats = [steel, beam, gaugeM];
    const extrude = (poly, len, mat) => {
      const sh = new THREE.Shape(poly.map(([z, y]) => new THREE.Vector2(z, y)));
      const g = new THREE.ExtrudeGeometry(sh, { depth: len, bevelEnabled: false });
      g.rotateY(-Math.PI / 2);                 // profile (Z, Y), extruded along X
      g.translate(len / 2, 0, 0);
      const m = new THREE.Mesh(g, mat);
      const e = new THREE.LineSegments(new THREE.EdgesGeometry(g, 30), new THREE.LineBasicMaterial({ color: 0x1d262d, transparent: true, opacity: 0.5 }));
      m.add(e);
      return m;
    };
    const tools = P.tools;
    const mk = (set, isPunch) => {
      const out = {};
      for (const [k, def] of Object.entries(set)) {
        const g = new THREE.Group();
        def.polys.forEach((poly, i) => {
          const holder = isPunch ? i > 0 : i > 0;
          g.add(extrude(poly, holder ? L + 120 : L, holder ? beam : steel));
        });
        g.visible = false;
        this.group.add(g);
        out[k] = g;
      }
      return out;
    };
    this.punches = mk(tools.punch, true);
    this.dies = mk(tools.die, false);
    // backgauge: a bar with two fingers behind the die
    this.gauge = new THREE.Group();
    const bar = new THREE.Mesh(new THREE.BoxGeometry(L * 0.9, 14, 18), beam);
    bar.position.set(0, 26, -9 - 22);
    this.gauge.add(bar);
    for (const x of [-L * 0.28, L * 0.28]) {
      const f = new THREE.Mesh(new THREE.BoxGeometry(18, 22, 22), gaugeM);
      f.position.set(x, 11, -11);
      this.gauge.add(f);
    }
    this.group.add(this.gauge);
  }

  dispose() {
    this.v.scene.remove(this.group);
    this.group.traverse((o) => { if (o.geometry) o.geometry.dispose(); });
    for (const m of this.mats) m.dispose();
  }

  /* ------------------------------------------------------------------ poses */
  doneAngles(mask) { return this.pc.bends.map((b, i) => ((mask >> i) & 1 ? b.theta : 0)); }

  /* rotation (part -> machine) and translation for a frame */
  mat(F) {
    const m = new THREE.Matrix4().set(F.X[0], F.X[1], F.X[2], 0, F.Y[0], F.Y[1], F.Y[2], 0, F.Z[0], F.Z[1], F.Z[2], 0, 0, 0, 0, 1);
    const p0 = new THREE.Vector3(...F.p0).applyMatrix4(m);
    m.setPosition(-p0.x, -p0.y + F.y0, -p0.z);
    return m;
  }

  /* things that stay the same during a hit (computed once) */
  info(k) {
    if (this.cache.has(k)) return this.cache.get(k);
    const s = this.steps[k], P = this.P, j = s.bend, b = this.pc.bends[j];
    const a0 = this.doneAngles(s.mask);
    const F0 = frameFor(P, a0, j, s.place);
    // centre of the part (for moving it around) and the backgauge contact behind the die
    let c = [0, 0, 0], n = 0, gz = Infinity, back = Infinity, high = 0;
    for (const p of P.samples) {
      const q = mapPoint(p.x, p.y, p.w, this.pc.regions[p.r], this.pc.bends, a0);
      c = [c[0] + q[0], c[1] + q[1], c[2] + q[2]]; n++;
      const m = toM(F0, q);
      if (m[2] < -P.V) {
        back = Math.min(back, m[2]);
        if (m[1] > -2 && m[1] < 30) gz = Math.min(gz, m[2]);     // the fingers touch the back edge
      }
      if (Math.abs(m[2]) < 45) high = Math.max(high, m[1]);
    }
    const centre = new THREE.Vector3(c[0] / n, c[1] / n, c[2] / n);
    const gaugeZ = Number.isFinite(gz) ? gz : Number.isFinite(back) ? back : -P.V * 3;
    const home = Math.max(F0.y0 + 70, high + 25);
    const target = s.hem ? HEM_PRE : b.theta;
    const r = { s, j, b, a0, F0, centre, gaugeZ, home, target };
    this.cache.set(k, r);
    return r;
  }

  /* where the part is at the end of hit k (after the last phase) */
  endPose(k) {
    const I = this.info(k), a = I.a0.slice();
    a[I.j] = I.b.theta;
    const F = I.s.hem ? frameFlat(this.P, a, I.j) : frameFor(this.P, a, I.j, I.s.place);
    return { M: this.mat(F), a };
  }

  /* ------------------------------------------------------------------ one moment of the timeline */
  // T in [0, steps]: hit k runs while T goes from k to k + 1
  state(T) {
    const n = this.steps.length;
    const k = Math.min(n - 1, Math.max(0, Math.floor(T)));
    const I = this.info(k), s = I.s, P = this.P;
    const tau = Math.min(1, Math.max(0, T - k)) * this.secs[k];
    const ph = phaseAt(s, tau), f = ease(ph.f);
    const a = I.a0.slice();
    let M, tip = I.home, punch = s.punch, die = s.die, gaugeZ = I.gaugeZ;
    const R = P.diag, off = new THREE.Vector3(0, 0.22 * R + 40, 0.5 * R + 90);
    const at = (ang) => { const aa = I.a0.slice(); aa[I.j] = ang; return aa; };
    const Fat = (ang) => frameFor(P, at(ang), I.j, s.place);
    if (ph.name === "load") {
      a[I.j] = 0;
      const B = this.mat(I.F0);
      const prev = k > 0 ? this.endPose(k - 1) : null;
      if (prev) {
        // the previous hit's part: lift out towards the operator, turn it, push it back in
        const A = prev.M;
        M = this.moveBetween(A, B, I.centre, off, ph.f);
        const prevGauge = this.info(k - 1).gaugeZ;
        gaugeZ = lerp(prevGauge, I.gaugeZ, ease(Math.min(1, ph.f / 0.5)));
      } else {
        M = this.slide(B, I.centre, new THREE.Vector3(0, 0, off.z), 1 - ease(ph.f));
      }
    } else if (ph.name === "approach") {
      a[I.j] = 0;
      M = this.mat(I.F0);
      tip = lerp(I.home, I.F0.y0, f);
    } else if (ph.name === "press") {
      a[I.j] = (I.target + SPRINGBACK) * f;
      const F = Fat(a[I.j]);
      M = this.mat(F); tip = F.y0;
    } else if (ph.name === "release") {
      const sb = ease(Math.min(1, ph.f / 0.45));
      a[I.j] = I.target + SPRINGBACK * (1 - sb);
      const F = Fat(a[I.j]);
      M = this.mat(F);
      const fTip = Fat(I.target + SPRINGBACK).y0;
      tip = lerp(fTip, I.home, f);
    } else {
      // hem: second hit on the flat tools
      punch = "flat"; die = "flat";
      const flatAt = (ang) => frameFlat(P, at(ang), I.j);
      if (ph.name === "toflat") {
        a[I.j] = I.target;
        const A = this.mat(Fat(I.target)), B = this.mat(flatAt(I.target));
        M = this.moveBetween(A, B, I.centre, off, ph.f);
        tip = I.home + 40;
        punch = ph.f < 0.5 ? s.punch : "flat"; die = ph.f < 0.5 ? s.die : "flat";
      } else if (ph.name === "flatten") {
        a[I.j] = lerp(I.target, I.b.theta, f);
        const F = flatAt(a[I.j]);
        M = this.mat(F);
        const top = topOf(P, a, F, I.j);
        const startTop = topOf(P, at(I.target), flatAt(I.target), I.j);
        tip = ph.f < 0.25 ? lerp(I.home + 40, startTop, ease(ph.f / 0.25)) : top;
      } else {
        a[I.j] = I.b.theta;
        const F = flatAt(I.b.theta);
        M = this.mat(F);
        tip = lerp(topOf(P, a, F, I.j), I.home + 40, f);
      }
    }
    return { k, ph, a, M, tip, punch, die, gaugeZ, step: s, info: I };
  }

  moveBetween(A, B, centre, off, f) {
    const qa = new THREE.Quaternion().setFromRotationMatrix(A), qb = new THREE.Quaternion().setFromRotationMatrix(B);
    const ca = centre.clone().applyMatrix4(A), cb = centre.clone().applyMatrix4(B);
    let q, c;
    if (f < 0.3) { const e = ease(f / 0.3); q = qa; c = ca.clone().addScaledVector(off, e); }
    else if (f < 0.7) { const e = ease((f - 0.3) / 0.4); q = qa.clone().slerp(qb, e); c = lerpV(ca, cb, e).add(off); }
    else { const e = ease((f - 0.7) / 0.3); q = qb; c = cb.clone().addScaledVector(off, 1 - e); }
    return this.compose(q, c, centre);
  }
  slide(B, centre, off, amount) {
    const q = new THREE.Quaternion().setFromRotationMatrix(B);
    const c = centre.clone().applyMatrix4(B).addScaledVector(off, amount);
    return this.compose(q, c, centre);
  }
  compose(q, c, centre) {
    // machine = R (p - centre) + c
    const m = new THREE.Matrix4().makeRotationFromQuaternion(q);
    const rc = centre.clone().applyMatrix4(m);
    m.setPosition(c.x - rc.x, c.y - rc.y, c.z - rc.z);
    return m;
  }

  /* apply a state to the tools on screen */
  show(st) {
    for (const [k, g] of Object.entries(this.punches)) { g.visible = k === st.punch; g.position.y = st.tip; }
    for (const [k, g] of Object.entries(this.dies)) g.visible = k === st.die;
    this.gauge.position.z = st.gaugeZ;
    this.gauge.visible = st.die !== "flat";
  }

  /* text for the overlay */
  caption(st) {
    const s = st.step, b = st.info.b, tools = this.P.tools;
    const dir = b.dir === "up" ? "พับขึ้น" : "พับลง";
    const tool = st.punch === "flat" ? `${tools.punch.flat.name} + ${tools.die.flat.name}` : `${tools.punch[st.punch].name} + ${tools.die[st.die].name}`;
    const extra = [];
    if (st.ph.name === "load" && s.flip) extra.push("กลับด้านชิ้นงาน");
    if (st.ph.name === "load" && s.turn) extra.push("หมุนชิ้นงานกลับหน้า-หลัง");
    if (!s.ok) extra.push("⚠ ชิ้นงานอาจชนเครื่องมือ ตรวจสอบก่อนพับจริง");
    if (s.short) extra.push("⚠ ปีกสั้นกว่าครึ่งร่อง V");
    return {
      title: `ขั้นที่ ${st.k + 1}/${this.steps.length} · รอยพับ #${b.n} · ${s.hem ? "พับตบ 180° (2 จังหวะ: พับแหลม 30° แล้วตบให้แนบ)" : `${dir} มุมใน ${Math.round(b.angle_in * 10) / 10}°`}`,
      line: `${st.ph.text} · ${tool}`,
      extra,
    };
  }

  seconds() { return this.total; }
  phases(k) { return stepPhases(this.steps[k]); }
}
