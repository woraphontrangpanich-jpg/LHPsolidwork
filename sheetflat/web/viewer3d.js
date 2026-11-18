/* 3D view of one part as modelled (folded): orbit, zoom, pan, overall dimensions, edge lengths.
   Loaded on demand by app.js; three.js is bundled in ./vendor so it works offline. */
import * as THREE from "./vendor/three.module.min.js";
import { OrbitControls } from "./vendor/OrbitControls.js";

const fmt = (n, d = 2) => Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });

export class Viewer3D {
  constructor(wrap, onInfo) {
    this.wrap = wrap;
    this.onInfo = onInfo || (() => {});
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(window.devicePixelRatio || 1);
    this.renderer.domElement.className = "cv3d";
    this.overlay = document.createElement("canvas");
    this.overlay.className = "cv3d-labels";
    wrap.appendChild(this.renderer.domElement);
    wrap.appendChild(this.overlay);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(35, 1, 0.1, 100000);
    this.camera.up.set(0, 1, 0);
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x8899aa, 1.6));
    this.sun = new THREE.DirectionalLight(0xffffff, 1.4);
    this.camera.add(this.sun);
    this.sun.position.set(0.4, 0.8, 1);
    this.scene.add(this.camera);
    this.root = new THREE.Group();
    this.scene.add(this.root);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.12;
    this.controls.addEventListener("change", () => this.requestRender());
    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.showDims = true;
    this.hover = null;
    this.edges = [];
    this.labels = [];
    this.colors = {};
    this.renderer.domElement.addEventListener("pointermove", (e) => this.onMove(e));
    this.renderer.domElement.addEventListener("pointerleave", () => { this.setHover(null); this.onInfo(null); });
    new ResizeObserver(() => this.resize()).observe(wrap);
    this.loop = this.loop.bind(this);
    this.resize();
  }

  setColors(c) {
    this.colors = c;
    this.renderer.setClearColor(new THREE.Color(c.zinc || "#e8ecee"));
    if (this.meshObj) this.meshObj.material.color.set(c.steelFill || "#b9c4cc");
    for (const l of this.edges) l.material.color.set(l === this.hover ? c.dim : c.ink);
    if (this.dimLines) this.dimLines.material.color.set(c.dim);
    this.requestRender();
  }

  resize() {
    const r = this.wrap.getBoundingClientRect();
    if (!r.width || !r.height) return;
    this.renderer.setSize(r.width, r.height, false);
    this.renderer.domElement.style.width = r.width + "px";
    this.renderer.domElement.style.height = r.height + "px";
    const dpr = window.devicePixelRatio || 1;
    this.overlay.width = Math.round(r.width * dpr);
    this.overlay.height = Math.round(r.height * dpr);
    this.overlay.style.width = r.width + "px";
    this.overlay.style.height = r.height + "px";
    this.camera.aspect = r.width / r.height;
    this.camera.updateProjectionMatrix();
    this.requestRender();
  }

  clear() {
    for (const o of [...this.root.children]) {
      this.root.remove(o);
      o.traverse((x) => { if (x.geometry) x.geometry.dispose(); if (x.material) x.material.dispose(); });
    }
    this.edges = []; this.labels = []; this.hover = null; this.meshObj = null; this.dimLines = null;
  }

  load(m) {
    this.clear();
    const c = this.colors;
    const lo = new THREE.Vector3(...m.bbox[0]), hi = new THREE.Vector3(...m.bbox[1]);
    const center = lo.clone().add(hi).multiplyScalar(0.5);
    this.size = hi.clone().sub(lo);
    this.diag = Math.max(1, this.size.length());
    this.root.position.copy(center).multiplyScalar(-1);

    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(m.pos, 3));
    g.setIndex(m.idx);
    g.computeVertexNormals();
    const mat = new THREE.MeshStandardMaterial({ color: c.steelFill || "#b9c4cc", metalness: 0.35, roughness: 0.55,
      side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
    this.meshObj = new THREE.Mesh(g, mat);
    this.root.add(this.meshObj);

    for (const e of m.edges) {
      const eg = new THREE.BufferGeometry();
      eg.setAttribute("position", new THREE.Float32BufferAttribute(e.pts, 3));
      const line = new THREE.Line(eg, new THREE.LineBasicMaterial({ color: c.ink || "#15202a" }));
      line.userData = e;
      this.edges.push(line);
      this.root.add(line);
    }
    this.buildDims(lo, hi, m.t);
    this.scene.updateMatrixWorld();
    this.view("iso");
  }

  /* overall size of the part along X, Y, Z, drawn on the bounding box, plus the sheet thickness */
  buildDims(lo, hi, t) {
    const off = this.diag * 0.06, pts = [], add = (a, b) => pts.push(...a, ...b);
    const dim = (a, b, dir, value, name) => {
      const A = a.clone().addScaledVector(dir, off), B = b.clone().addScaledVector(dir, off);
      add(a.toArray(), A.clone().addScaledVector(dir, off * 0.3).toArray());   // extension lines
      add(b.toArray(), B.clone().addScaledVector(dir, off * 0.3).toArray());
      add(A.toArray(), B.toArray());                                          // dimension line
      this.labels.push({ p: A.clone().add(B).multiplyScalar(0.5), text: `${name} ${fmt(value, 1)}` });
    };
    const s = hi.clone().sub(lo);
    // on the box edges facing the default (iso) view: X along the front-bottom edge, Y up the front-right
    // corner, Z along the right-bottom edge; each pushed outwards from the box
    if (s.x > 0.01) dim(new THREE.Vector3(lo.x, lo.y, hi.z), new THREE.Vector3(hi.x, lo.y, hi.z), new THREE.Vector3(0, 0, 1), s.x, "X");
    if (s.y > 0.01) dim(new THREE.Vector3(hi.x, lo.y, hi.z), new THREE.Vector3(hi.x, hi.y, hi.z), new THREE.Vector3(0.7071, 0, 0.7071), s.y, "Y");
    if (s.z > 0.01) dim(new THREE.Vector3(hi.x, lo.y, lo.z), new THREE.Vector3(hi.x, lo.y, hi.z), new THREE.Vector3(1, 0, 0), s.z, "Z");
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    this.dimLines = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: this.colors.dim || "#0b7683" }));
    this.dimLines.visible = this.showDims;
    this.root.add(this.dimLines);
    this.thick = t;
  }

  setDims(on) { this.showDims = on; if (this.dimLines) this.dimLines.visible = on; this.requestRender(); }

  view(name) {
    // distance so the whole part (and its dimension lines) fits the narrower side of the view
    const fov = (this.camera.fov * Math.PI) / 180, aspect = Math.min(1, this.camera.aspect || 1);
    const d = (this.diag * 0.62) / Math.sin(Math.atan(Math.tan(fov / 2) * aspect));
    const dirs = { iso: [1, 0.8, 1.2], front: [0, 0, 1], top: [0, 1, 0.0001], right: [1, 0, 0], back: [0, 0, -1] };
    const v = new THREE.Vector3(...(dirs[name] || dirs.iso)).normalize().multiplyScalar(d);
    this.camera.position.copy(v);
    this.camera.near = this.diag / 200; this.camera.far = this.diag * 50;
    this.camera.updateProjectionMatrix();
    this.controls.target.set(0, 0, 0);
    this.controls.update();
    this.requestRender();
  }
  fit() { this.view("iso"); }

  onMove(e) {
    const r = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    this.scene.updateMatrixWorld();                          // picking must not wait for the next frame
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const px = this.camera.position.length() * Math.tan((this.camera.fov * Math.PI) / 360) * 2 / r.height;
    this.raycaster.params.Line.threshold = px * 6;
    const hit = this.raycaster.intersectObjects(this.edges, false)[0];
    const meshHit = this.meshObj ? this.raycaster.intersectObject(this.meshObj, false)[0] : null;
    const edge = hit && (!meshHit || hit.distance <= meshHit.distance + px * 8) ? hit.object : null;
    this.setHover(edge);
    this.onInfo(edge ? edge.userData : null);
  }
  setHover(line) {
    if (this.hover === line) return;
    if (this.hover) this.hover.material.color.set(this.colors.ink);
    this.hover = line;
    if (line) line.material.color.set(this.colors.dim);
    this.requestRender();
  }

  requestRender() { if (!this.raf) this.raf = requestAnimationFrame(this.loop); }
  loop() {
    this.raf = null;
    const moving = this.controls.update();
    this.renderer.render(this.scene, this.camera);
    this.drawLabels();
    if (moving) this.requestRender();
  }

  drawLabels() {
    const ctx = this.overlay.getContext("2d"), dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, this.overlay.width, this.overlay.height);
    if (!this.showDims) return;
    const w = this.overlay.width / dpr, h = this.overlay.height / dpr, c = this.colors;
    ctx.font = "600 12.5px " + (c.font || "sans-serif");
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    const tmp = new THREE.Vector3();
    for (const l of this.labels) {
      tmp.copy(l.p).add(this.root.position).project(this.camera);
      if (tmp.z > 1) continue;
      const x = (tmp.x + 1) / 2 * w, y = (1 - tmp.y) / 2 * h, tw = ctx.measureText(l.text).width + 10;
      ctx.fillStyle = c.paper || "#fff"; ctx.globalAlpha = 0.9; ctx.fillRect(x - tw / 2, y - 10, tw, 20); ctx.globalAlpha = 1;
      ctx.fillStyle = c.dim || "#0b7683"; ctx.fillText(l.text, x, y);
    }
    if (this.thick) {
      ctx.textAlign = "left"; ctx.fillStyle = c.steel || "#4d5c68";
      ctx.fillText(`หนา ${fmt(this.thick, 2)} มม. · ขนาดรวม ${fmt(this.size.x, 1)} × ${fmt(this.size.y, 1)} × ${fmt(this.size.z, 1)} มม.`, 14, h - 16);
    }
  }
}
