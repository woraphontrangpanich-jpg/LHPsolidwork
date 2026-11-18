/* 3D view of one part. Loaded on demand by app.js; three.js is bundled in ./vendor so it works offline.

   Sheet parts: built from the flat pattern (fold model from the server), shown flat first; "พับ" folds
   it bend by bend in table order (#1, #2 ...) with an animation, "คลี่" unfolds. Parts without a flat
   pattern (bolts, tubes ...) show the solid as modelled.
   Labels can be switched on/off: overall size, bend angles, lengths, holes/radii.
   Click a line, a bend or a flat area to see its details in a small card. */
import * as THREE from "./vendor/three.module.min.js";
import { OrbitControls } from "./vendor/OrbitControls.js";
import { mapPoint, anglesAt } from "./fold.js";

const fmt = (n, d = 2) => Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtG = (n) => String(Math.round(n * 100) / 100);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const SEC_PER_BEND = 0.75;

export class Viewer3D {
  constructor(wrap, onInfo, onState) {
    this.wrap = wrap;
    this.onInfo = onInfo || (() => {});
    this.onState = onState || (() => {});
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(window.devicePixelRatio || 1);
    this.renderer.domElement.className = "cv3d";
    this.overlay = document.createElement("canvas");
    this.overlay.className = "cv3d-labels";
    this.card = document.createElement("div");
    this.card.className = "card3d";
    this.card.hidden = true;
    wrap.appendChild(this.renderer.domElement);
    wrap.appendChild(this.overlay);
    wrap.appendChild(this.card);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(35, 1, 0.1, 100000);
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x8899aa, 1.5));
    this.sun = new THREE.DirectionalLight(0xffffff, 1.5);
    this.camera.add(this.sun);
    this.sun.position.set(0.4, 0.8, 1);
    this.scene.add(this.camera);
    this.root = new THREE.Group();          // the part (moved so its centre is at the origin)
    this.scene.add(this.root);
    this.dimGroup = new THREE.Group();      // overall-size lines, in world coordinates
    this.scene.add(this.dimGroup);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.12;
    this.controls.addEventListener("change", () => this.requestRender());
    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.show = { size: true, angle: true, len: false, hole: false };
    this.colors = {};
    this.lines = [];                        // pickable lines (Line objects with userData)
    this.dimLabels = [];
    this.hover = null; this.picked = null;
    const el = this.renderer.domElement;
    el.addEventListener("pointermove", (e) => this.onMove(e));
    el.addEventListener("pointerleave", () => { this.setHover(null); this.onInfo(null); });
    el.addEventListener("pointerdown", (e) => { this.down = [e.clientX, e.clientY]; });
    el.addEventListener("pointerup", (e) => {
      if (this.down && Math.hypot(e.clientX - this.down[0], e.clientY - this.down[1]) < 5) this.pick(e);
      this.down = null;
    });
    this.card.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) this.closeCard(); });
    window.addEventListener("keydown", (e) => { if (e.key === "Escape" && !this.card.hidden) this.closeCard(); });
    new ResizeObserver(() => this.resize()).observe(wrap);
    this.loop = this.loop.bind(this);
    this.resize();
  }

  /* ------------------------------------------------------------------ common */
  setColors(c) {
    this.colors = c;
    this.renderer.setClearColor(new THREE.Color(c.zinc || "#e8ecee"));
    if (this.mats) {
      this.mats.flange.color.set(c.steelFill || "#b9c4cc");
      this.mats.bend.color.set(c.bendFill || "#e7b3a8");
      this.mats.edge.color.set(c.ink); this.mats.outline.color.set(c.ink);
    }
    for (const l of this.lines) l.material.color.set(l === this.hover || l === this.picked ? c.dim : c.ink);
    for (const o of this.dimGroup.children) if (o.material) o.material.color.set(c.dim);
    this.requestRender();
  }
  resize() {
    const r = this.wrap.getBoundingClientRect();
    if (!r.width || !r.height) return;
    this.renderer.setSize(r.width, r.height, false);
    this.renderer.domElement.style.width = r.width + "px";
    this.renderer.domElement.style.height = r.height + "px";
    const dpr = window.devicePixelRatio || 1;
    this.overlay.width = Math.round(r.width * dpr); this.overlay.height = Math.round(r.height * dpr);
    this.overlay.style.width = r.width + "px"; this.overlay.style.height = r.height + "px";
    this.camera.aspect = r.width / r.height;
    this.camera.updateProjectionMatrix();
    this.requestRender();
  }
  clear() {
    this.stopAnim();
    for (const g of [this.root, this.dimGroup]) {
      for (const o of [...g.children]) {
        g.remove(o);
        o.traverse((x) => { if (x.geometry) x.geometry.dispose(); if (x.material && !Array.isArray(x.material)) x.material.dispose(); });
      }
    }
    this.root.rotation.set(0, 0, 0); this.root.position.set(0, 0, 0);
    this.lines = []; this.dimLabels = []; this.hover = null; this.picked = null; this.F = null; this.solid = null;
    this.closeCard();
  }
  setShow(key, on) {
    this.show[key] = on;
    this.dimGroup.visible = this.show.size;
    this.requestRender();
  }

  /* frame what is shown now; name = view direction, or null to keep the current direction */
  view(name) {
    const box = this.shownBox();
    const ctr = box && !box.isEmpty() ? box.getCenter(new THREE.Vector3()) : new THREE.Vector3();
    const diag = box && !box.isEmpty() ? Math.max(1, box.getSize(new THREE.Vector3()).length()) : this.diag;
    const fov = (this.camera.fov * Math.PI) / 180, aspect = Math.min(1, this.camera.aspect || 1);
    const d = (diag * 0.62) / Math.sin(Math.atan(Math.tan(fov / 2) * aspect));
    const dirs = { iso: [1, 0.9, 1.2], front: [0, 0, 1], top: [0, 1, 0.0001], right: [1, 0, 0] };
    const dir = name ? new THREE.Vector3(...(dirs[name] || dirs.iso)).normalize()
      : this.camera.position.clone().sub(this.controls.target).normalize();
    this.camera.position.copy(ctr).addScaledVector(dir, d);
    this.camera.near = this.diag / 300; this.camera.far = this.diag * 60;
    this.camera.updateProjectionMatrix();
    this.controls.target.copy(ctr);
    this.controls.update();
    this.requestRender();
  }
  shownBox() {
    if (this.F) return this.currentBox();
    this.scene.updateMatrixWorld();
    const box = new THREE.Box3();
    this.root.traverse((o) => { if (o.isMesh) { o.geometry.computeBoundingBox(); box.union(o.geometry.boundingBox.clone().applyMatrix4(o.matrixWorld)); } });
    return box;
  }
  fit() { this.view(null); }

  /* overall size of what is shown now (world axes): lines on the bounding box + labels */
  buildDims() {
    for (const o of [...this.dimGroup.children]) { this.dimGroup.remove(o); o.geometry.dispose(); o.material.dispose(); }
    this.dimLabels = [];
    this.scene.updateMatrixWorld();
    const box = new THREE.Box3();
    this.root.traverse((o) => { if (o.isMesh) { o.geometry.computeBoundingBox(); box.union(o.geometry.boundingBox.clone().applyMatrix4(o.matrixWorld)); } });
    if (box.isEmpty()) return;
    const lo = box.min, hi = box.max, s = box.getSize(new THREE.Vector3()), off = this.diag * 0.06, pts = [];
    const add = (a, b) => pts.push(...a.toArray(), ...b.toArray());
    const dim = (a, b, dir, value, name) => {
      const A = a.clone().addScaledVector(dir, off), B = b.clone().addScaledVector(dir, off);
      add(a, A.clone().addScaledVector(dir, off * 0.3)); add(b, B.clone().addScaledVector(dir, off * 0.3)); add(A, B);
      this.dimLabels.push({ p: A.clone().add(B).multiplyScalar(0.5), text: `${name} ${fmt(value, 1)}` });
    };
    if (s.x > 0.01) dim(new THREE.Vector3(lo.x, lo.y, hi.z), new THREE.Vector3(hi.x, lo.y, hi.z), new THREE.Vector3(0, 0, 1), s.x, "X");
    if (s.y > 0.01) dim(new THREE.Vector3(hi.x, lo.y, hi.z), new THREE.Vector3(hi.x, hi.y, hi.z), new THREE.Vector3(0.7071, 0, 0.7071), s.y, "Y");
    if (s.z > 0.01) dim(new THREE.Vector3(hi.x, lo.y, lo.z), new THREE.Vector3(hi.x, lo.y, hi.z), new THREE.Vector3(1, 0, 0), s.z, "Z");
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    this.dimGroup.add(new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: this.colors.dim || "#0b7683" })));
    this.dimGroup.visible = this.show.size;
    this.size = s;
  }

  /* ------------------------------------------------------------------ solid (parts without a flat pattern) */
  load(m) {
    this.clear();
    const c = this.colors;
    const lo = new THREE.Vector3(...m.bbox[0]), hi = new THREE.Vector3(...m.bbox[1]);
    this.diag = Math.max(1, hi.clone().sub(lo).length());
    this.root.position.copy(lo.clone().add(hi).multiplyScalar(-0.5));
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(m.pos, 3));
    g.setIndex(m.idx);
    g.computeVertexNormals();
    this.root.add(new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: c.steelFill || "#b9c4cc", metalness: 0.3, roughness: 0.6,
      side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 })));
    for (const e of m.edges) {
      const eg = new THREE.BufferGeometry();
      eg.setAttribute("position", new THREE.Float32BufferAttribute(e.pts, 3));
      const line = new THREE.Line(eg, new THREE.LineBasicMaterial({ color: c.ink || "#15202a" }));
      line.userData = { kind: e.kind === "line" ? "line" : e.kind === "circle" ? "arc" : "curve", len: e.len, r: e.r };
      this.lines.push(line); this.root.add(line);
    }
    this.thick = m.t;
    this.solid = true;
    this.buildDims();
    this.view("iso");
    this.onState({ folded: true, solid: true, T: 0, n: 0 });
  }

  /* ------------------------------------------------------------------ fold model */
  loadFold(model) {
    this.clear();
    const c = this.colors;
    this.mats = this.mats || {
      flange: new THREE.MeshStandardMaterial({ metalness: 0.3, roughness: 0.6, side: THREE.DoubleSide, flatShading: true,
        polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }),
      bend: new THREE.MeshStandardMaterial({ metalness: 0.2, roughness: 0.6, side: THREE.DoubleSide, flatShading: true,
        polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }),
      edge: new THREE.LineBasicMaterial(), outline: new THREE.LineBasicMaterial({ transparent: true, opacity: 0.55 }),
    };
    this.setColors(c);
    const h = model.t / 2;
    this.thick = model.t;
    // the flat lies on the floor: fold Z (up = "พับขึ้น") becomes world Y
    this.root.rotation.x = -Math.PI / 2;
    const F = { pieces: [], t: model.t, h };
    let xoff = 0;
    for (const P of model.pieces) {
      const piece = this.buildPiece(P, h, xoff);
      F.pieces.push(piece);
      xoff += (P.bbox[2] - P.bbox[0]) + Math.max(20, 0.1 * (P.bbox[2] - P.bbox[0]));
    }
    // fold order: table order (#1, #2 ...) of the first piece; one timeline per piece, side by side
    F.n = Math.max(0, ...F.pieces.map((p) => p.bends.length));
    this.F = F;
    // frame size and centre from both states (flat and folded) so nothing jumps while folding
    const box = new THREE.Box3();
    for (const T of [0, F.n]) { this.setT(T, true); box.union(this.currentBox()); }
    const ctr = box.getCenter(new THREE.Vector3());
    this.diag = Math.max(1, box.getSize(new THREE.Vector3()).length());
    this.root.position.sub(ctr);
    this.setT(0);
    this.view("iso");
  }

  buildPiece(P, h, xoff) {
    const pos = [], base = [], reg = [], fIdx = [], bIdx = [], triRegF = [], triRegB = [];
    const addV = (x, y, w, r) => { base.push(x + 0, y, w); reg.push(r); pos.push(0, 0, 0); return reg.length - 1; };
    P.regions.forEach((R, ri) => {
      const isBend = R.kind === "bend";
      for (const poly of R.polys) {
        const contour = poly.outer.map(([x, y]) => new THREE.Vector2(x, y));
        const holes = poly.holes.map((hh) => hh.map(([x, y]) => new THREE.Vector2(x, y)));
        let tris;
        try { tris = THREE.ShapeUtils.triangulateShape(contour, holes); } catch (_) { continue; }
        const all = contour.concat(...holes);
        const top = all.map((p) => addV(p.x, p.y, h, ri)), bot = all.map((p) => addV(p.x, p.y, -h, ri));
        const I = isBend ? bIdx : fIdx, TR = isBend ? triRegB : triRegF;
        for (const [a, b, cc] of tris) { I.push(top[a], top[b], top[cc], bot[a], bot[cc], bot[b]); TR.push(ri, ri); }
      }
    });
    // side walls along the outline (and holes)
    const wIdx = [], triRegW = [], outline = [];
    for (const wl of P.walls) {
      const n = wl.pts.length, t0 = [], b0 = [];
      wl.pts.forEach(([x, y], i) => { t0.push(addV(x, y, h, wl.reg[i])); b0.push(addV(x, y, -h, wl.reg[i])); });
      for (let i = 0; i < n; i++) {
        const j = (i + 1) % n;
        wIdx.push(t0[i], b0[i], t0[j], t0[j], b0[i], b0[j]); triRegW.push(wl.reg[i], wl.reg[i]);
        outline.push(b0[i], b0[j]);
      }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    geo.setIndex([...fIdx, ...wIdx, ...bIdx]);
    geo.addGroup(0, fIdx.length + wIdx.length, 0);
    geo.addGroup(fIdx.length + wIdx.length, bIdx.length, 1);
    const mesh = new THREE.Mesh(geo, [this.mats.flange, this.mats.bend]);
    mesh.userData.triReg = [...triRegF, ...triRegW, ...triRegB];
    this.root.add(mesh);
    // bottom outline (thin) so the sheet edges read well
    const og = new THREE.BufferGeometry();
    og.setAttribute("position", geo.getAttribute("position"));
    og.setIndex(outline);
    this.root.add(new THREE.LineSegments(og, this.mats.outline));
    // drawing lines on the top face: pickable, with their size
    const lines = [];
    for (const q of P.prims) {
      const lb = [], lr = [];
      q.pts.forEach(([x, y], i) => { lb.push(x + xoff, y, h); lr.push(q.reg[i]); });
      const lg = new THREE.BufferGeometry();
      lg.setAttribute("position", new THREE.Float32BufferAttribute(new Float32Array(lb.length), 3));
      const line = new THREE.Line(lg, new THREE.LineBasicMaterial({ color: this.colors.ink }));
      line.userData = { kind: q.t === "line" ? "line" : q.t === "circle" ? "hole" : q.t === "arc" ? "arc" : "curve", len: q.len, r: q.r };
      this.lines.push(line); this.root.add(line);
      lines.push({ line, base: lb, reg: lr, prim: q });
    }
    for (let i = 0; i < base.length; i += 3) base[i] += xoff;
    const order = P.bends.map((b, i) => i).sort((a, b) => P.bends[a].n - P.bends[b].n);
    return { P, mesh, geo, base, reg, lines, bends: P.bends, regions: P.regions, order, xoff, h };
  }

  /* fold progress T: 0 = flat, n = every bend folded (bend k folds while T goes from k to k+1) */
  setT(T, quiet) {
    const F = this.F; if (!F) return;
    F.T = T;
    for (const pc of F.pieces) {
      const ang = anglesAt(pc.bends, T, pc.order);
      pc.angles = ang;
      const attr = pc.geo.getAttribute("position"), arr = attr.array, b = pc.base, R = pc.regions;
      for (let i = 0, v = 0; v < pc.reg.length; v++, i += 3) {
        const r = R[pc.reg[v]];
        const p = r ? mapPoint(b[i] - pc.xoff, b[i + 1], b[i + 2], r, pc.bends, ang) : [b[i] - pc.xoff, b[i + 1], b[i + 2]];
        arr[i] = p[0] + pc.xoff; arr[i + 1] = p[1]; arr[i + 2] = p[2];
      }
      attr.needsUpdate = true;
      pc.geo.computeBoundingSphere(); pc.geo.computeBoundingBox();
      for (const L of pc.lines) {
        const la = L.line.geometry.getAttribute("position"), out = la.array;
        for (let i = 0, v = 0; v < L.reg.length; v++, i += 3) {
          const r = R[L.reg[v]];
          const p = r ? mapPoint(L.base[i] - pc.xoff, L.base[i + 1], L.base[i + 2], r, pc.bends, ang) : [L.base[i] - pc.xoff, L.base[i + 1], L.base[i + 2]];
          out[i] = p[0] + pc.xoff; out[i + 1] = p[1]; out[i + 2] = p[2];
        }
        la.needsUpdate = true;
        L.line.geometry.computeBoundingSphere();
      }
    }
    if (quiet) return;
    const settled = Math.abs(T - Math.round(T)) < 1e-9;
    if (settled && !this.anim) this.buildDims();
    this.onState({ folded: T >= F.n - 1e-9, flat: T <= 1e-9, T, n: F.n, current: this.currentBend() });
    this.requestRender();
  }
  currentBox() {
    this.scene.updateMatrixWorld();
    const box = new THREE.Box3();
    for (const pc of this.F.pieces) box.union(pc.geo.boundingBox.clone().applyMatrix4(pc.mesh.matrixWorld));
    return box;
  }
  currentBend() {
    const F = this.F; if (!F || !F.pieces.length) return null;
    const pc = F.pieces[0], k = Math.floor(F.T);
    if (k < 0 || k >= pc.order.length || Math.abs(F.T - k) < 1e-9) return null;
    return pc.bends[pc.order[k]];
  }

  play(target) {
    const F = this.F; if (!F) return;
    this.stopAnim();
    const from = F.T, dist = Math.abs(target - from);
    if (dist < 1e-6) return;
    const dur = Math.min(8, Math.max(0.6, dist * SEC_PER_BEND)) * 1000, t0 = performance.now();
    this.dimGroup.visible = false;
    this.anim = (now) => {
      const k = Math.min(1, (now - t0) / dur);
      // ease within each bend so every bend starts and stops gently
      const T = from + (target - from) * k, whole = Math.floor(T), frac = T - whole;
      const eased = whole + (frac < 1e-9 ? 0 : 0.5 - 0.5 * Math.cos(Math.PI * frac));
      this.setT(k >= 1 ? target : eased);
      if (k < 1) this.animRaf = requestAnimationFrame(this.anim);
      else { this.anim = null; this.dimGroup.visible = this.show.size; this.setT(target); this.view(null); }
    };
    this.animRaf = requestAnimationFrame(this.anim);
  }
  fold() { if (this.F) this.play(this.F.n); }
  unfold() { if (this.F) this.play(0); }
  stopAnim() { if (this.animRaf) cancelAnimationFrame(this.animRaf); this.animRaf = null; this.anim = null; }
  scrub(frac) { if (!this.F) return; this.stopAnim(); this.setT(frac * this.F.n); }

  /* ------------------------------------------------------------------ hover, pick, detail card */
  ray(e) {
    const r = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    this.scene.updateMatrixWorld();
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const px = this.camera.position.distanceTo(this.controls.target) * Math.tan((this.camera.fov * Math.PI) / 360) * 2 / r.height;
    this.raycaster.params.Line.threshold = px * 6;
    const meshes = []; this.root.traverse((o) => { if (o.isMesh) meshes.push(o); });
    const mh = this.raycaster.intersectObjects(meshes, false)[0];
    const lh = this.raycaster.intersectObjects(this.lines, false)[0];
    const line = lh && (!mh || lh.distance <= mh.distance + px * 10) ? lh.object : null;
    return { line, mesh: mh, px };
  }
  onMove(e) {
    if (e.buttons) return;
    const { line } = this.ray(e);
    this.setHover(line);
    this.onInfo(line ? line.userData : null);
  }
  setHover(line) {
    if (this.hover === line) return;
    const c = this.colors;
    if (this.hover && this.hover !== this.picked) this.hover.material.color.set(c.ink);
    this.hover = line;
    if (line) line.material.color.set(c.dim);
    this.requestRender();
  }
  pick(e) {
    const { line, mesh } = this.ray(e);
    if (this.picked && this.picked !== line) this.picked.material.color.set(this.colors.ink);
    this.picked = line || null;
    if (line) { line.material.color.set(this.colors.dim); this.showCard(e, this.lineCard(line.userData)); return; }
    if (mesh && this.F) {
      const pc = this.F.pieces.find((p) => p.mesh === mesh.object);
      const ri = pc && pc.mesh.userData.triReg[mesh.faceIndex];
      const R = pc && pc.regions[ri];
      if (R) { this.showCard(e, R.kind === "bend" ? this.bendCard(pc.bends[R.bend]) : this.flangeCard(R, pc)); return; }
    }
    if (mesh && this.solid) { this.showCard(e, { title: "ชิ้นงาน", rows: [["ความหนา", this.thick ? `${fmt(this.thick)} มม.` : "–"]] }); return; }
    this.closeCard();
  }
  lineCard(d) {
    const t = this.thick ? [["ความหนาแผ่น", `${fmt(this.thick)} มม.`]] : [];
    if (d.kind === "hole") return { title: "รู", rows: [["เส้นผ่านศูนย์กลาง", `Ø ${fmt(d.r * 2)} มม.`], ["รัศมี", `R ${fmt(d.r)} มม.`], ...t] };
    if (d.kind === "arc") return { title: "ส่วนโค้ง", rows: [["รัศมี", d.r ? `R ${fmt(d.r)} มม.` : "–"], ["ความยาวตามโค้ง", `${fmt(d.len)} มม.`], ...t] };
    if (d.kind === "line") return { title: "เส้นตรง (ขอบตัด)", rows: [["ความยาว", `${fmt(d.len)} มม.`], ...t] };
    return { title: "เส้นโค้งอิสระ", rows: [["ความยาว", `${fmt(d.len)} มม.`], ...t] };
  }
  bendCard(b) {
    return { title: `รอยพับ #${b.n}`, bend: true, rows: [
      ["มุมด้านใน", `${fmtG(b.angle_in)}°`], ["ทิศ", b.dir === "up" ? "พับขึ้น" : "พับลง"], ["รัศมีใน (R)", `${fmt(b.r)} มม.`],
      ["วิธีคิด", b.method === "table" ? `ตารางค่ายืด BD ${fmt(b.bd)}` : `K-factor ${b.k}`], ["ความกว้างแถบพับ (BA)", `${fmt(b.ba)} มม.`],
      ["ความยาวรอยพับ", `${fmt(b.len)} มม.`], ["ความหนาแผ่น", `${fmt(this.thick)} มม.`]] };
  }
  flangeCard(R, pc) {
    // the bend this flange hangs from, and the bends that hang from it
    const joins = pc.bends.filter((b) => R.chain[0] === b.id || b.chain.slice(1).join() === R.chain.join());
    return { title: "แผ่นเรียบ (ปีก)", rows: [["ขนาดในแบบคลี่", `${fmt(R.size[0])} × ${fmt(R.size[1])} มม.`], ["ความหนาแผ่น", `${fmt(this.thick)} มม.`],
      ["ต่อกับรอยพับ", joins.length ? joins.map((b) => "#" + b.n).join(", ") : "–"]] };
  }
  showCard(e, info) {
    const r = this.wrap.getBoundingClientRect();
    this.card.innerHTML = `<header><b>${esc(info.title)}</b><button type="button" data-close aria-label="ปิด">×</button></header>
      <dl>${info.rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>`;
    this.card.hidden = false;
    const cw = this.card.offsetWidth, ch = this.card.offsetHeight;
    let x = e.clientX - r.left + 14, y = e.clientY - r.top + 14;
    if (x + cw > r.width - 8) x = Math.max(8, e.clientX - r.left - cw - 14);
    if (y + ch > r.height - 8) y = Math.max(8, r.height - ch - 8);
    this.card.style.left = x + "px"; this.card.style.top = y + "px";
    this.requestRender();
  }
  closeCard() {
    this.card.hidden = true;
    if (this.picked) { this.picked.material.color.set(this.colors.ink || "#15202a"); this.picked = null; }
    this.requestRender();
  }

  /* ------------------------------------------------------------------ render + labels */
  requestRender() { if (!this.raf) this.raf = requestAnimationFrame(this.loop); }
  loop() {
    this.raf = null;
    const moving = this.controls.update();
    this.renderer.render(this.scene, this.camera);
    this.drawLabels();
    if (moving) this.requestRender();
  }
  toScreen(v, w, h) {
    const p = v.clone().project(this.camera);
    return p.z > 1 ? null : [(p.x + 1) / 2 * w, (1 - p.y) / 2 * h];
  }
  drawLabels() {
    const ctx = this.overlay.getContext("2d"), dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, this.overlay.width, this.overlay.height);
    const w = this.overlay.width / dpr, h = this.overlay.height / dpr, c = this.colors;
    const tag = (text, x, y, color, bold) => {
      ctx.font = `${bold ? 700 : 600} 12px ${c.font || "sans-serif"}`;
      const tw = ctx.measureText(text).width + 10;
      ctx.fillStyle = c.paper || "#fff"; ctx.globalAlpha = 0.88; ctx.fillRect(x - tw / 2, y - 9, tw, 18); ctx.globalAlpha = 1;
      ctx.fillStyle = color; ctx.fillText(text, x, y);
    };
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    if (this.show.size && !this.anim) for (const l of this.dimLabels) { const s = this.toScreen(l.p, w, h); if (s) tag(l.text, s[0], s[1], c.dim); }
    if (this.F) {
      this.scene.updateMatrixWorld();
      const cur = this.currentBend();
      for (const pc of this.F.pieces) {
        const M = pc.mesh.matrixWorld, at = (x, y, z, r) => {
          const p = r ? mapPoint(x, y, z, r, pc.bends, pc.angles) : [x, y, z];
          return this.toScreen(new THREE.Vector3(p[0] + pc.xoff, p[1], p[2]).applyMatrix4(M), w, h);
        };
        if (this.show.angle) for (const b of pc.bends) {
          const s = at(b.mid[0], b.mid[1], pc.h * (b.s > 0 ? 1 : -1), { kind: "bend", bend: b.id, chain: b.chain });
          if (s) tag(`#${b.n} ${b.dir === "up" ? "พับขึ้น" : "พับลง"} ${fmtG(b.angle_in)}°`, s[0], s[1], c.bend || "#cf4129", cur === b);
        }
        if (this.show.len || this.show.hole) for (const L of pc.lines) {
          const d = L.line.userData, isLen = d.kind === "line", isHole = d.kind === "hole" || d.kind === "arc";
          if ((isLen && !this.show.len) || (isHole && !this.show.hole) || (!isLen && !isHole)) continue;
          if (isLen && d.len < this.diag * 0.03) continue;
          if (isHole && (d.r || 0) < 0.3) continue;
          const i = Math.floor(L.reg.length / 2) * 3;
          const p = L.line.geometry.getAttribute("position");
          const v = new THREE.Vector3(p.array[i], p.array[i + 1], p.array[i + 2]).applyMatrix4(L.line.matrixWorld);
          const s = this.toScreen(v, w, h);
          if (s) tag(isLen ? fmt(d.len, 1) : d.kind === "hole" ? `Ø${fmt(d.r * 2, 1)}` : `R${fmt(d.r, 1)}`, s[0], s[1], c.ink);
        }
      }
    }
    if (this.thick) {
      ctx.textAlign = "left"; ctx.fillStyle = c.steel || "#4d5c68"; ctx.font = `500 12.5px ${c.font || "sans-serif"}`;
      const sz = this.size ? ` · ขนาดรวม ${fmt(this.size.x, 1)} × ${fmt(this.size.y, 1)} × ${fmt(this.size.z, 1)} มม.` : "";
      ctx.fillText(`หนา ${fmt(this.thick, 2)} มม.${this.anim ? "" : sz}`, 14, h - 16);
    }
  }
}
