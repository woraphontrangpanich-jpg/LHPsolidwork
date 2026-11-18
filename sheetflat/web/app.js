"use strict";
/* ======================================================================
   SheetFlat front end - vanilla JS, no build step.
   ====================================================================== */
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n, d = 2) => (n === null || n === undefined || Number.isNaN(n) ? "–" : Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
const fmtG = (n) => String(Math.round(n * 1000) / 1000);

const S = {
  jobId: null, project: null, profilesData: null,
  sel: null,            // {kind:'group'|'part', id}
  sheetIdx: 0, version: 0, cache: new Map(), openGroups: new Set(),
  closedMats: new Set(), closedPlates: new Set(),   // folded sections of the DXF list
  editGroups: false, picked: new Set(),             // overview "edit groups" mode
  price: null,
};

/* ---- static demo (GitHub Pages): API calls are served from pre-computed files ---- */
const DEMO = window.SHEETFLAT_DEMO || null;
const DEMO_MSG = "เวอร์ชันตัวอย่างออนไลน์แก้ค่าไม่ได้ ดาวน์โหลดโปรแกรมไปรันในเครื่องเพื่อใช้งานจริง";
function demoPath(path) {
  let m;
  if (path === "/api/profiles") return DEMO + "profiles.json";
  if ((m = path.match(/^\/api\/jobs\/[^/]+$/))) return DEMO + "job.json";
  if ((m = path.match(/\/parts\/([^/]+)\/geometry$/))) return DEMO + `geom/${m[1]}.json`;
  if ((m = path.match(/\/groups\/([^/]+)\/layout\/(\d+)$/))) return DEMO + `layout/${m[1]}_${m[2]}.json`;
  if (path.match(/^\/api\/jobs\/[^/]+\/pricing$/)) return DEMO + "pricing.json";
  return null;
}
async function api(path, opts = {}) {
  if (DEMO) {
    const method = (opts.method || "GET").toUpperCase();
    if (method === "POST" && (path === "/api/upload" || path === "/api/open-path")) {
      return new Response(JSON.stringify({ id: "demo" }), { headers: { "Content-Type": "application/json" } });
    }
    if (method !== "GET") throw new Error(DEMO_MSG);
    const f = demoPath(path);
    if (!f) throw new Error(DEMO_MSG);
    path = f;
  }
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = j.detail || msg; } catch (_) { /* ignore */ }
    throw new Error(msg);
  }
  return res;
}
const getJSON = async (p) => (await api(p)).json();
const sendJSON = async (p, method, body) => (await api(p, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();

function toast(msg, ms = 2600) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms);
}

/* ======================================================================
   Landing
   ====================================================================== */
async function initLanding() {
  S.profilesData = await getJSON("/api/profiles");
  fillProfileSelects();
  const box = $("#matRadios"); box.innerHTML = "";
  S.profilesData.materials.forEach((m, i) => {
    box.insertAdjacentHTML("beforeend", `<label class="radio"><input type="radio" name="mat" value="${esc(m.id)}" ${i === 0 ? "checked" : ""}><span>${esc(m.name)}</span></label>`);
  });
  $("#landProfile").addEventListener("change", updateLandHint);
  $("#landProfileView").addEventListener("click", () => openProfileDialog($("#landProfile").value));
  $("#landProfileNew").addEventListener("click", () => openProfileDialog($("#landProfile").value, true));
  $("#loadJob").addEventListener("click", () => { $("#file").accept = ".sheetflat"; $("#file").click(); $("#file").accept = ".step,.stp,.sldasm,.sldprt,.sheetflat"; });
  updateLandHint();
  const drop = $("#drop"), file = $("#file");
  drop.addEventListener("click", () => file.click());
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); file.click(); } });
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { if (e.dataTransfer.files[0]) startUpload(e.dataTransfer.files[0]); });
  file.addEventListener("change", () => { if (file.files[0]) startUpload(file.files[0]); });
  $("#pathGo").addEventListener("click", startPath);
  $("#pathInput").addEventListener("keydown", (e) => { if (e.key === "Enter") startPath(); });
}

function fillProfileSelects() {
  const d = S.profilesData;
  for (const sel of [$("#landProfile"), $("#profileSel")]) {
    sel.innerHTML = d.profiles.map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
  }
  $("#landProfile").value = d.active;
}
function updateLandHint() {
  const p = S.profilesData.profiles.find((x) => x.id === $("#landProfile").value);
  if (!p) return;
  const own = p.table.filter((r) => r.material).length;
  $("#landProfileHint").textContent = `ตาราง ${p.table.length} แถว${own ? ` (${own} แถวใช้กับเหล็กเฉพาะชนิด)` : ""} · K-factor ${p.k_factor} (ใช้กับรอยพับไม่ฉากและโค้ง)`;
}
function landError(msg) { const e = $("#landErr"); e.textContent = msg; e.hidden = !msg; }
const chosenMaterial = () => (document.querySelector("input[name=mat]:checked") || {}).value || "ZN";

async function startUpload(file) {
  landError("");
  if (DEMO) { landError("เวอร์ชันออนไลน์ยังคลี่ไฟล์ของคุณไม่ได้ (ต้องใช้ OpenCASCADE ที่รันบนเครื่อง) กด “เปิดงานตัวอย่าง” เพื่อลองดู หรือดาวน์โหลดโปรแกรมไปรันในเครื่อง"); return; }
  const isJob = /\.sheetflat$/i.test(file.name);
  const fd = new FormData();
  fd.append("file", file);
  if (!isJob) { fd.append("material", chosenMaterial()); fd.append("profile", $("#landProfile").value); }
  showBusy((isJob ? "กำลังเปิดไฟล์งาน " : "กำลังอัปโหลด ") + file.name);
  try {
    const r = await (await api(isJob ? "/api/load" : "/api/upload", { method: "POST", body: fd })).json();
    if (isJob) S.profilesData = await getJSON("/api/profiles");
    await pollJob(r.id);
  } catch (e) { backToLanding(e.message); }
}
async function startPath() {
  landError("");
  const p = $("#pathInput").value.trim();
  if (!p) return;
  showBusy("กำลังเปิดไฟล์");
  try {
    const r = await sendJSON("/api/open-path", "POST", { path: p, material: chosenMaterial(), profile: $("#landProfile").value });
    await pollJob(r.id);
  } catch (e) { backToLanding(e.message); }
}
function showBusy(msg, pct) {
  $("#landing").hidden = true; $("#bench").hidden = true; $("#busy").hidden = false;
  $("#busyMsg").textContent = msg;
  const m = $(".meter"); m.classList.toggle("indet", pct === undefined);
  if (pct !== undefined) $("#meterFill").style.width = Math.max(6, pct) + "%";
}
function backToLanding(msg) {
  $("#busy").hidden = true; $("#bench").hidden = true; $("#landing").hidden = false; landError(msg || "");
}
async function pollJob(id) {
  for (;;) {
    const j = await getJSON("/api/jobs/" + id);
    if (j.state === "error") throw new Error(j.error || j.progress.msg);
    if (j.state === "done") { S.jobId = id; setProject(j.project); enterBench(); return; }
    const p = j.progress;
    const msg = { reading: "กำลังอ่านไฟล์ STEP", converting: "กำลังให้ SolidWorks แปลงเป็น STEP", analyzing: `วิเคราะห์ชิ้นส่วน ${p.done}/${p.total}`, unfolding: "กำลังคลี่แผ่น" }[j.state] || "กำลังประมวลผล";
    showBusy(p.msg && j.state === "analyzing" ? `${msg} · ${p.msg}` : msg, p.total ? Math.min(95, (p.done / p.total) * 100) : undefined);
    await new Promise((r) => setTimeout(r, 400));
  }
}

/* ======================================================================
   Workbench state
   ====================================================================== */
function setProject(p) {
  p.groups.forEach((g) => (g.key = g.material_id + "|" + g.thickness));   // stable id across regrouping
  S.project = p; S.version++; S.cache.clear();
  S.picked.forEach((id) => { if (!p.parts.some((x) => x.id === id)) S.picked.delete(id); });
}
function enterBench() {
  $("#busy").hidden = true; $("#landing").hidden = true; $("#bench").hidden = false;
  $("#topJob").hidden = false; $("#topActions").hidden = false;
  fillProfileSelects(); $("#profileSel").value = S.project.settings.profile || S.profilesData.active;
  const P = S.project;
  $("#jobName").textContent = P.source;
  if (!S.sel) { S.sel = { kind: "overview" }; S.sheetIdx = 0; }
  P.groups.forEach((g) => S.openGroups.add(g.id));
  renderAll();
}
function renderAll() {
  renderTop(); renderRail(); renderInspector(); loadScene();
}
function thumbSVG(t, cls = "thumb") {
  if (!t) return `<svg class="${cls}" aria-hidden="true"></svg>`;
  const pad = Math.max(t.w, t.h) * 0.04;
  return `<svg class="${cls}" viewBox="${-pad} ${-pad} ${t.w + 2 * pad} ${t.h + 2 * pad}" preserveAspectRatio="xMidYMid meet" aria-hidden="true"><path d="${t.d}"/></svg>`;
}
const part = (id) => S.project.parts.find((p) => p.id === id);
const groupOfPart = (pid) => S.project.groups.find((g) => g.part_ids.includes(pid));
const curGroup = () => S.project.groups.find((g) => g.id === (S.sel && S.sel.kind === "group" ? S.sel.id : (groupOfPart(S.sel && S.sel.id) || {}).id));

function renderTop() {
  const s = S.project.summary;
  $("#jobMeta").textContent = `${s.pieces} ชิ้นใน Assembly · ตัด ${s.cut_pieces} ชิ้น (${s.cut_unique} แบบ) · ไม่ตัด ${s.excluded_pieces} ชิ้น`;
  $("#btnAll").disabled = S.project.groups.length === 0;
}

/* ---------------- rail ---------------- */
function renderRail() {
  const P = S.project, box = $("#groups");
  const ov = $("#ovLink");
  ov.classList.toggle("sel", !!S.sel && S.sel.kind === "overview");
  $("#ovSub").textContent = `${P.groups.reduce((n, g) => n + g.sheets.length, 0)} ไฟล์ DXF · ตัด ${P.summary.cut_pieces} ชิ้น · ไม่ตัด ${P.summary.excluded_pieces} ชิ้น`;
  ov.onclick = () => selectOverview();
  $("#railCount").textContent = P.groups.reduce((n, g) => n + g.sheets.length, 0) + " ไฟล์";
  if (!P.groups.length) box.innerHTML = `<p class="empty-note">ยังไม่มีชิ้นส่วนที่จะตัด ลองติ๊ก “นำมาตัด” ในรายการด้านล่าง</p>`;
  else {
    const mats = [];
    P.groups.forEach((g) => { let m = mats.find((x) => x.id === g.material_id); if (!m) mats.push((m = { id: g.material_id, groups: [] })); m.groups.push(g); });
    box.innerHTML = mats.map((m) => {
      const info = P.materials.find((x) => x.id === m.id) || { name: m.id };
      const open = !S.closedMats.has(m.id);
      const pcs = m.groups.reduce((n, g) => n + g.pieces, 0), files = m.groups.reduce((n, g) => n + g.sheets.length, 0);
      return `<section class="matsec">
        <button class="mat-head" data-mat="${esc(m.id)}" aria-expanded="${open}"><i class="chev" aria-hidden="true"></i>
          <span class="mat-name">${esc(info.name)}</span><span class="mat-sum">${files} ไฟล์ · ${pcs} ชิ้น</span></button>
        <div class="mat-body" ${open ? "" : "hidden"}>${m.groups.map(groupHTML).join("")}</div></section>`;
    }).join("");
  }
  box.querySelectorAll("[data-mat]").forEach((el) => el.addEventListener("click", () => { const id = el.dataset.mat; S.closedMats.has(id) ? S.closedMats.delete(id) : S.closedMats.add(id); renderRail(); }));
  box.querySelectorAll("[data-fold]").forEach((el) => el.addEventListener("click", (e) => { e.stopPropagation(); const id = el.dataset.fold; S.closedPlates.has(id) ? S.closedPlates.delete(id) : S.closedPlates.add(id); renderRail(); }));
  box.querySelectorAll("[data-group]").forEach((el) => el.addEventListener("click", () => selectGroup(el.dataset.group)));
  box.querySelectorAll("[data-part]").forEach((el) => el.addEventListener("click", () => selectPart(el.dataset.part)));
  box.querySelectorAll("[data-dl]").forEach((el) => el.addEventListener("click", (e) => { e.stopPropagation(); download(el.dataset.dl); }));
  const ex = P.parts.filter((p) => !p.include);
  $("#exclCount").textContent = ex.length ? ex.reduce((n, p) => n + p.qty, 0) + " ชิ้น" : "";
  $("#exclHint").hidden = !ex.length;
  $("#excluded").innerHTML = ex.length ? ex.map(exclHTML).join("") : `<p class="empty-note">ทุกชิ้นถูกนำไปตัด</p>`;
  $("#excluded").querySelectorAll("[data-xpart]").forEach((el) => el.addEventListener("click", () => selectPart(el.dataset.xpart)));
  $("#excluded").querySelectorAll("[data-xinc]").forEach((el) => el.addEventListener("click", (e) => { e.stopPropagation(); patchPart(el.dataset.xinc, { include: true }); }));
}
function groupHTML(g) {
  const selG = S.sel && ((S.sel.kind === "group" && S.sel.id === g.id) || (S.sel.kind === "part" && g.part_ids.includes(S.sel.id)));
  const tpx = Math.max(3, Math.round(g.thickness * 5));
  const rows = g.part_ids.map((pid) => {
    const p = part(pid), f = p.flat;
    const w = p.warnings.length ? `<span class="badge" title="${esc(p.warnings.join("\n"))}">ตรวจสอบ</span>` : "";
    return `<button class="prow has-thumb ${S.sel && S.sel.kind === "part" && S.sel.id === pid ? "sel" : ""}" data-part="${pid}">
      ${thumbSVG(p.thumb)}<span class="pn">${esc(p.name)}${w}</span><span class="pq">${p.qty} ชิ้น</span>
      <span class="ps">${f ? `${fmt(f.w)} × ${fmt(f.h)} มม. · พับ ${f.bends.length} จุด` : ""}</span></button>`;
  }).join("");
  const sheets = g.sheets.map((s) => `<button class="btn small" data-dl="dxf/${g.id}/${s.index}" title="ดาวน์โหลด ${esc(s.filename)}">ดาวน์โหลด${g.sheets.length > 1 ? " แผ่น " + (s.index + 1) : " DXF"}</button>`).join("");
  const open = !S.closedPlates.has(g.key);
  return `<div class="plate ${selG ? "sel" : ""}">
    <div class="plate-top">
    <button class="plate-head" data-group="${g.id}" aria-label="ดูไฟล์ ${esc(g.filename)}">
      <span class="plate-code">${esc(g.name)}${g.renamed ? `<small class="plate-steel">${esc(g.code)}</small>` : ""}</span>
      <span class="plate-t"><i class="tbar" style="height:${tpx}px"></i><b>${fmtG(g.thickness)} มม.</b></span>
      <span class="plate-file">${esc(g.filename)}</span>
      <span class="plate-sum">${g.pieces} ชิ้น · ${g.part_ids.length} แบบ · ${g.sheets.length > 1 ? g.sheets.length + " แผ่น" : fmt(g.sheets[0].w, 0) + " × " + fmt(g.sheets[0].h, 0) + " มม."}</span>
    </button>
    <button class="fold" data-fold="${esc(g.key)}" aria-expanded="${open}" aria-label="${open ? "ซ่อน" : "แสดง"}รายการชิ้นใน ${esc(g.code)}" title="${open ? "ซ่อน" : "แสดง"}รายการชิ้น"><i class="chev" aria-hidden="true"></i></button>
    </div>
    <div class="plate-body" ${open ? "" : "hidden"}>${rows}</div>
    <div class="plate-foot">${sheets}</div></div>`;
}
function exclHTML(p) {
  const sel = S.sel && S.sel.kind === "part" && S.sel.id === p.id;
  const can = p.reason_code !== "not_sheet" || p.thickness.measured;
  return `<div class="xrow ${sel ? "sel" : ""}"><div class="xt" data-xpart="${p.id}"><b>${esc(p.name)}</b><span>× ${p.qty}</span></div>
    <p class="why">${p.category ? `กลุ่ม “${esc(p.category)}”` : esc(p.reason || "ผู้ใช้เลือกไม่ตัด")}</p>
    ${can && p.reason_code !== "not_sheet" ? `<button class="btn small" data-xinc="${p.id}">นำมาตัดอยู่ดี</button>` : ""}</div>`;
}

/* ---- static site: "try" and "download" tabs ---- */
function route() {
  const dl = location.hash === "#download";
  $("#navDl").setAttribute("aria-current", dl ? "page" : "false");
  $("#navTry").setAttribute("aria-current", dl ? "false" : "page");
  $("#dlPage").hidden = !dl;
  if (dl) {
    $("#landing").hidden = true; $("#busy").hidden = true; $("#bench").hidden = true;
    $("#topJob").hidden = true; $("#topActions").hidden = true;
    window.scrollTo(0, 0);
    releaseInfo();
  } else if (S.project) {
    enterBench();
  } else {
    $("#landing").hidden = false;
  }
}
/* version, size and SHA-256 of the latest release, straight from GitHub (page works without it) */
let releaseLoaded = false;
async function releaseInfo() {
  if (releaseLoaded) return;
  releaseLoaded = true;
  try {
    const r = await fetch("https://api.github.com/repos/woraphontrangpanich-jpg/LHPsolidwork/releases/latest");
    if (!r.ok) return;
    const rel = await r.json();
    const a = (rel.assets || []).find((x) => x.name === "SheetFlat-windows.zip");
    if (!a) return;
    $("#dlMeta").textContent = `SheetFlat-windows.zip · เวอร์ชัน ${rel.tag_name} · Windows 10 / 11 (64-bit) · ${Math.round(a.size / 1e6)} MB · มีทุกอย่างในไฟล์เดียว ไม่ต้องติดตั้งอะไรเพิ่ม`;
    if (a.digest && a.digest.startsWith("sha256:")) {
      $("#dlHashVal").textContent = a.digest.slice(7).toUpperCase();
      $("#dlHash").hidden = false;
    }
  } catch (e) { /* offline or rate-limited: keep the static text */ }
}
function selectOverview() { S.sel = { kind: "overview" }; renderRail(); renderInspector(); loadScene(); }
function selectGroup(id) { S.sel = { kind: "group", id }; S.sheetIdx = 0; renderRail(); renderInspector(); loadScene(); }
function selectPart(id) { S.sel = { kind: "part", id }; renderRail(); renderInspector(); loadScene(); }

/* ---------------- inspector ---------------- */
function chk(kind, title, sub) {
  const ic = { ok: "✓", warn: "!", bad: "×" }[kind];
  return `<div class="chk ${kind}"><i>${ic}</i><span>${title}</span>${sub ? `<small>${sub}</small>` : ""}</div>`;
}
function renderInspector() {
  const box = $("#inspector"), P = S.project;
  if (!S.sel) { box.innerHTML = `<div class="sec"><p class="insp-sub">เลือกไฟล์ DXF หรือชิ้นส่วนทางซ้ายเพื่อดูรายละเอียด</p></div>`; return; }
  if (S.sel.kind === "overview") {
    box.innerHTML = overviewInspector();
    box.querySelectorAll("[data-dl]").forEach((b) => b.addEventListener("click", () => download(b.dataset.dl)));
    box.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => (b.dataset.act === "price" ? openPricing() : saveJob())));
    return;
  }
  if (S.sel.kind === "group") { box.innerHTML = groupInspector(P.groups.find((g) => g.id === S.sel.id)); bindGroupInspector(); return; }
  const p = part(S.sel.id);
  box.innerHTML = partInspector(p); bindPartInspector(p);
}
function overviewInspector() {
  const P = S.project, s = P.summary, prof = P.profile;
  const kg = P.groups.reduce((n, g) => n + g.weight_kg, 0);
  return `<div class="sec"><h2 class="insp-title">${esc(P.job)}</h2><p class="insp-sub">${esc(P.source)}</p></div>
  <div class="sec"><dl class="kv">
    <dt>ชิ้นส่วนใน Assembly</dt><dd>${s.pieces} ชิ้น (${s.unique} แบบ)</dd>
    <dt>นำไปตัด</dt><dd>${s.cut_pieces} ชิ้น (${s.cut_unique} แบบ)</dd>
    <dt>ไม่นำไปตัด</dt><dd>${s.excluded_pieces} ชิ้น</dd>
    <dt>ไฟล์ DXF</dt><dd>${P.groups.reduce((n, g) => n + g.sheets.length, 0)} ไฟล์</dd>
    <dt>น้ำหนักเหล็กรวม</dt><dd>${fmt(kg, 2)} กก.</dd></dl></div>
  <div class="sec"><h3>โหมดค่ายืดที่ใช้</h3><dl class="kv"><dt>โหมด</dt><dd>${esc(prof.name)}</dd><dt>K-factor</dt><dd>${prof.k_factor}</dd>
    <dt>DXF ใส่ชิ้น</dt><dd>${P.settings.copies === "qty" ? "ตามจำนวนจริง" : "1 ชิ้นต่อแบบ"}</dd></dl></div>
  <div class="sec"><div class="dl-btns"><button class="btn primary" data-dl="all">ดาวน์โหลด DXF ทั้งหมด + Excel (.zip)</button><button class="btn" data-dl="xlsx">ดาวน์โหลดใบสรุป Excel</button>
    <button class="btn" data-act="price">คิดราคา</button><button class="btn" data-act="save">บันทึกงาน (.sheetflat)</button></div>
  <p class="hint">คลิกแถวในใบสั่งตัดเพื่อดูแบบคลี่ของชิ้นนั้น คลิกชื่อความหนาเพื่อดูไฟล์ DXF ทั้งแผ่น · บันทึกงานแล้วส่งไฟล์ให้คนอื่นเปิดต่อได้ ผลเหมือนเดิมทุกอย่าง</p></div>`;
}
function renderOverview() {
  const P = S.project, s = P.summary, ed = S.editGroups;
  const pick = (pid) => ed ? `<td class="pick"><input type="checkbox" data-pick="${pid}" ${S.picked.has(pid) ? "checked" : ""} aria-label="เลือก ${esc(part(pid).name)}"></td>` : "";
  const pickAll = (ids, label) => ed ? `<th class="pick"><input type="checkbox" data-pickall="${ids.join(",")}" ${ids.length && ids.every((i) => S.picked.has(i)) ? "checked" : ""} aria-label="เลือกทั้งหมดใน${esc(label)}"></th>` : "";
  const groups = P.groups.map((g) => {
    const rows = g.part_ids.map((pid) => {
      const p = part(pid), f = p.flat;
      return `<tr class="click ${S.picked.has(pid) && ed ? "picked" : ""}" data-part="${pid}">${pick(pid)}<td>${thumbSVG(p.thumb)}</td><td>${esc(p.name)}${p.warnings.length ? ' <span class="badge">ตรวจสอบ</span>' : ""}</td>
        <td class="n">${fmt(f.w)} × ${fmt(f.h)}</td><td class="n">${f.bends.length}</td><td class="n">${f.holes}</td><td class="qty">${p.qty}</td></tr>`;
    }).join("");
    const tpx = Math.max(3, Math.round(g.thickness * 5));
    return `<section class="ogroup"><div class="ogroup-head">
        <h3>${renameBox("g", g.key, g.name) || `<button class="linkish" data-group="${g.id}">${esc(g.name)}</button>${g.renamed ? ` <small class="steel-code">${esc(g.code)}</small>` : ""}
          <button class="rename-btn" data-rename="g|${esc(g.key)}" title="เปลี่ยนชื่อกลุ่ม (ใช้เป็นชื่อไฟล์ DXF)" aria-label="เปลี่ยนชื่อกลุ่ม ${esc(g.name)}">เปลี่ยนชื่อ</button>`}</h3>
        <span class="tb"><i class="tbar" style="height:${tpx}px"></i>หนา ${fmtG(g.thickness)} มม.</span>
        <button class="btn small" ${g.sheets.length > 1 ? `data-group="${g.id}">ดู ${g.sheets.length} แผ่น` : `data-dl="dxf/${g.id}/0">ดาวน์โหลด DXF`}</button>
        <span class="meta">${esc(g.sheets.map((x) => x.filename).join(", "))} · ${g.part_ids.length} แบบ · ${g.pieces} ชิ้น · ${fmt(g.weight_kg, 2)} กก.</span></div>
      <table class="otable"><thead><tr>${pickAll(g.part_ids, g.code)}<th></th><th>ชิ้นส่วน</th><th class="n">ขนาดแผ่นคลี่ (มม.)</th><th class="n">รอยพับ</th><th class="n">รู</th><th class="n">จำนวน</th></tr></thead>
      <tbody>${rows}</tbody><tfoot><tr>${ed ? "<td></td>" : ""}<td></td><td>รวม ${g.part_ids.length} แบบ</td><td></td><td></td><td></td><td class="qty">${g.pieces}</td></tr></tfoot></table></section>`;
  }).join("");
  // parts that are not cut, grouped by the user's group name (e.g. ซื้อสำเร็จรูป) or "ไม่นำไปตัด"
  const ex = P.parts.filter((p) => !p.include);
  const cats = [];
  ex.forEach((p) => { const k = p.category || ""; let c = cats.find((x) => x.k === k); if (!c) cats.push((c = { k, parts: [] })); c.parts.push(p); });
  cats.sort((a, b) => (a.k ? 0 : 1) - (b.k ? 0 : 1));
  const exHTML = cats.map((c) => `<section class="ogroup"><div class="ogroup-head"><h3>${c.k ? (renameBox("c", c.k, c.k) || `${esc(c.k)} <button class="rename-btn" data-rename="c|${esc(c.k)}" aria-label="เปลี่ยนชื่อกลุ่ม ${esc(c.k)}">เปลี่ยนชื่อ</button>`) : "ไม่นำไปตัด"}</h3><span class="tb">${c.k ? "กลุ่มที่ตั้งเอง · ไม่ตัด ไม่อยู่ใน DXF" : "ท่อ ชิ้นส่วนซื้อ หรือชิ้นที่คลี่ไม่ได้"}</span><span></span></div>
    <table class="otable"><thead><tr>${pickAll(c.parts.map((p) => p.id), c.k || "ไม่นำไปตัด")}<th></th><th>ชิ้นส่วน</th><th colspan="3">เหตุผล</th><th class="n">จำนวน</th></tr></thead><tbody>
    ${c.parts.map((p) => `<tr class="click ${S.picked.has(p.id) && ed ? "picked" : ""}" data-part="${p.id}">${pick(p.id)}<td>${thumbSVG(p.thumb)}</td><td>${esc(p.name)}</td><td class="why" colspan="3">${esc(c.k ? "ย้ายมากลุ่มนี้เอง" : p.reason || "ผู้ใช้เลือกไม่ตัด")}</td><td class="qty">${p.qty}</td></tr>`).join("")}
    </tbody><tfoot><tr>${ed ? "<td></td>" : ""}<td></td><td>รวม</td><td colspan="3"></td><td class="qty">${c.parts.reduce((n, p) => n + p.qty, 0)}</td></tr></tfoot></table></section>`).join("");
  const nfiles = P.groups.reduce((n, g) => n + g.sheets.length, 0);
  const box = $("#overview");
  box.innerHTML = `<article class="order"><header class="order-head"><div><h2>ใบสั่งตัด ${esc(P.job)}</h2><p>${esc(P.source)} · ${new Date().toLocaleDateString("th-TH")} · ${esc(P.profile.name)}</p></div>
    <div class="totals"><div><b>${nfiles}</b><span>ไฟล์ DXF</span></div><div><b>${s.cut_pieces}</b><span>ชิ้นที่ตัด</span></div><div><b>${s.excluded_pieces}</b><span>ไม่ตัด</span></div></div></header>
    <div class="edit-row">${ed ? "" : `<button class="btn small" id="ovEdit">แก้ไขกลุ่ม</button><span class="hint">ย้ายชิ้นไปเหล็กชนิดหรือความหนาอื่น หรือแยกเป็นกลุ่มที่ไม่ต้องตัด เช่น ซื้อสำเร็จรูป</span>`}</div>
    ${ed ? moveBarHTML() : ""}
    ${groups || '<p class="hint">ยังไม่มีชิ้นส่วนที่จะตัด</p>'}${exHTML}</article>`;
  box.querySelectorAll("tr[data-part]").forEach((tr) => tr.addEventListener("click", (e) => {
    if (e.target.closest("input")) return;
    if (ed) { togglePick(tr.dataset.part); return; }
    selectPart(tr.dataset.part);
  }));
  box.querySelectorAll("[data-pick]").forEach((c) => c.addEventListener("change", () => togglePick(c.dataset.pick, c.checked)));
  box.querySelectorAll("[data-pickall]").forEach((c) => c.addEventListener("change", () => { syncMove(); c.dataset.pickall.split(",").forEach((id) => (c.checked ? S.picked.add(id) : S.picked.delete(id))); renderOverview(); }));
  box.querySelectorAll("[data-group]").forEach((b) => b.addEventListener("click", () => selectGroup(b.dataset.group)));
  box.querySelectorAll("[data-dl]").forEach((b) => b.addEventListener("click", () => download(b.dataset.dl)));
  bindRename(box);
  const e1 = $("#ovEdit"); if (e1) e1.onclick = () => { if (DEMO) { toast(DEMO_MSG); return; } S.editGroups = true; S.picked.clear(); S.move = null; renderOverview(); };
  if (ed) bindMoveBar();
}
/* ---- rename a group: DXF groups (name -> DXF file name) and not-cut groups ---- */
function renameBox(kind, key, current) {
  if (!S.renaming || S.renaming.kind !== kind || S.renaming.key !== key) return "";
  return `<form class="rename-form" data-rkind="${kind}" data-rkey="${esc(key)}">
    <input type="text" class="rename-in" value="${esc(current)}" maxlength="60" aria-label="ชื่อกลุ่มใหม่" ${kind === "g" ? 'placeholder="ว่าง = ใช้รหัสเหล็กเดิม"' : ""}>
    <button class="btn primary small" type="submit">บันทึก</button><button class="btn small" type="button" data-rcancel>ยกเลิก</button></form>`;
}
function bindRename(box) {
  box.querySelectorAll("[data-rename]").forEach((b) => b.addEventListener("click", (e) => {
    e.stopPropagation();
    if (DEMO) { toast(DEMO_MSG); return; }
    const [kind, ...rest] = b.dataset.rename.split("|");
    S.renaming = { kind, key: rest.join("|") }; renderOverview();
    const i = box.querySelector(".rename-in"); if (i) { i.focus(); i.select(); }
  }));
  box.querySelectorAll("[data-rcancel]").forEach((b) => (b.onclick = () => { S.renaming = null; renderOverview(); }));
  box.querySelectorAll(".rename-in").forEach((i) => i.addEventListener("keydown", (e) => { if (e.key === "Escape") { S.renaming = null; renderOverview(); } }));
  box.querySelectorAll(".rename-form").forEach((f) => f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = f.querySelector(".rename-in").value.trim(), kind = f.dataset.rkind, key = f.dataset.rkey;
    const body = kind === "g" ? { key, name } : { category: key, name };
    try {
      setProject(await sendJSON(`/api/jobs/${S.jobId}/rename`, "POST", body));
      S.renaming = null; S.price = null;
      renderTop(); renderRail(); renderInspector(); renderOverview();
      toast(kind === "g" ? (name ? `เปลี่ยนชื่อกลุ่มเป็น “${name}” แล้ว ไฟล์ DXF ใช้ชื่อนี้` : "ใช้ชื่อตามรหัสเหล็กเหมือนเดิมแล้ว") : `เปลี่ยนชื่อกลุ่มเป็น “${name}” แล้ว`);
    } catch (err) { toast(err.message, 4000); }
  }));
}
function togglePick(id, on) {
  syncMove();
  if (on === undefined) on = !S.picked.has(id);
  on ? S.picked.add(id) : S.picked.delete(id);
  renderOverview();
}
/* where the picked parts can go. A part keeps its own flat size and thickness; moving it to another
   steel makes (or joins) the DXF group of that steel at that thickness. */
const matName = (id) => ((S.project.materials.find((m) => m.id === id) || {}).name || id);
const matPrefix = (id) => ((S.project.materials.find((m) => m.id === id) || {}).prefix || id);
/* same rule as profiles.match_row: a row for this steel wins over a row for every steel */
function bendRow(t, mid) {
  const prof = S.project.profile, tol = prof.match_tol ?? 0.06;
  let best = null, key = null;
  for (const r of prof.table) {
    if (r.material && r.material !== mid) continue;
    const d = Math.abs(r.thickness - t);
    if (d > tol) continue;
    const k = [Math.round(d * 1e6), r.material ? 0 : 1];
    if (!key || k[0] < key[0] || (k[0] === key[0] && k[1] < key[1])) { best = r; key = k; }
  }
  return best;
}
const groupCode = (mid, t) => { const r = bendRow(t, mid); return r && r.gauge ? matPrefix(mid) + r.gauge : `${matPrefix(mid)}#${fmtG(t)}mm`; };
function pickedThicknesses() {
  return [...new Set([...S.picked].map((id) => part(id)).filter((p) => p && p.thickness.used).map((p) => p.thickness.used))].sort((a, b) => a - b);
}
function moveBarHTML() {
  const P = S.project, n = S.picked.size, mv = S.move || (S.move = { target: "", base: P.materials[0].id, mode: "base" });
  const cats = [...new Set(P.parts.map((p) => p.category).filter(Boolean))];
  const opt = (v, l) => `<option value="${esc(v)}" ${mv.target === v ? "selected" : ""}>${esc(l)}</option>`;
  let h = `<div class="movebar" role="region" aria-label="ย้ายชิ้นที่เลือก">
    <div class="mv-row">
      <b class="mv-n">${n ? `เลือก ${n} แบบ` : "ติ๊กเลือกชิ้นที่จะย้าย"}</b>
      <label class="mv-to">ย้ายไปที่
        <select id="mvTarget"><option value="" ${mv.target ? "" : "selected"} disabled>เลือกปลายทาง</option>
          <optgroup label="เหล็ก (ตัด · ขนาดและความหนาเดิมของชิ้น)">${P.materials.map((m) => opt("m|" + m.id, m.name)).join("")}${opt("new|", "+ เหล็กชนิดใหม่…")}</optgroup>
          <optgroup label="ไม่ตัด">${opt("x|", "ไม่นำไปตัด")}${cats.map((c) => opt("c|" + c, c)).join("")}${opt("cnew|", "+ กลุ่มใหม่ (พิมพ์ชื่อ เช่น ซื้อสำเร็จรูป)…")}</optgroup>
        </select></label>
      ${mv.target === "cnew|" ? `<label class="mv-to">ชื่อกลุ่ม<input type="text" id="mvName" value="${esc(mv.name || "")}" placeholder="เช่น ซื้อสำเร็จรูป" list="mvNames" maxlength="60"></label>
        <datalist id="mvNames"><option value="ซื้อสำเร็จรูป"><option value="ลูกค้าจัดหาเอง"><option value="งานกลึง"></datalist>` : ""}
      <span class="grow"></span>
      <button class="btn primary small" id="mvGo" ${n && mv.target ? "" : "disabled"}>${mv.target === "new|" ? "บันทึกเหล็กใหม่และย้าย" : "ย้าย"}</button>
      <button class="btn small" id="mvDone">เสร็จ</button>
    </div>`;
  if (mv.target === "new|") h += newSteelHTML(mv);
  const [kind, mid] = mv.target.split("|");
  if (n && (kind === "m" || kind === "new")) h += movePreviewHTML(kind === "m" ? mid : (mv.id || "ใหม่"), kind === "new" ? mv : null);
  return h + `</div>`;
}
function newSteelHTML(mv) {
  const P = S.project, ts = pickedThicknesses();
  const base = mv.base || P.materials[0].id;
  mv.rows = ts.map((t) => {
    const old = (mv.rows || []).find((r) => Math.abs(r.thickness - t) < 1e-6);
    const br = bendRow(t, base);
    const bd = mv.mode === "base" || !old ? (br ? br.bd : "") : old.bd;
    return { thickness: t, gauge: br ? br.gauge : (old && old.gauge) || "", bd };
  });
  return `<div class="newsteel">
    <div class="grid3">
      <label>รหัสเหล็ก<input type="text" id="nsId" value="${esc(mv.id || "")}" placeholder="เช่น SUS" maxlength="12"></label>
      <label>ชื่อ<input type="text" id="nsName" value="${esc(mv.nm || "")}" placeholder="เช่น สแตนเลส (SUS)"></label>
      <label>ราคา / กก. (บาท)<input type="number" id="nsPrice" min="0" step="0.5" value="${esc(mv.price ?? "")}" placeholder="ตามเหล็กที่อิง"></label>
    </div>
    <fieldset class="ns-mode"><legend>ค่ายืดของเหล็กนี้</legend>
      <label class="radio-line"><input type="radio" name="nsMode" value="base" ${mv.mode === "base" ? "checked" : ""}> อิงค่ายืดของ
        <select id="nsBase">${P.materials.map((m) => `<option value="${esc(m.id)}" ${m.id === base ? "selected" : ""}>${esc(m.name)}</option>`).join("")}</select></label>
      <label class="radio-line"><input type="radio" name="nsMode" value="own" ${mv.mode === "own" ? "checked" : ""}> กำหนดเอง</label>
    </fieldset>
    <table class="pt ns-rows"><thead><tr><th>เบอร์</th><th>ความหนา (มม.)</th><th>ค่ายืด 90° (มม.)</th></tr></thead><tbody>
      ${mv.rows.map((r, i) => `<tr><td><input type="text" data-ns="${i}" data-f="gauge" value="${esc(r.gauge)}" aria-label="เบอร์" ${mv.mode === "base" ? "readonly" : ""}></td>
        <td class="n">${fmtG(r.thickness)}</td>
        <td><input type="number" step="0.01" min="0" data-ns="${i}" data-f="bd" value="${r.bd}" aria-label="ค่ายืด หนา ${fmtG(r.thickness)}" ${mv.mode === "base" ? "readonly" : ""}></td></tr>`).join("")}
    </tbody></table>
    <p class="hint">บันทึกไว้ในเครื่องนี้ ใช้ได้กับงานถัดไป (ค่ายืดลงในโหมด “${esc(P.profile.name)}” เป็นแถวเฉพาะเหล็กนี้ และเพิ่มในตารางราคา)</p>
  </div>`;
}
function movePreviewHTML(mid, mv) {
  const rows = [...S.picked].map((id) => part(id)).filter(Boolean).map((p) => {
    const t = p.thickness.used, f = p.flat;
    if (!t || !f) return `<tr><td>${esc(p.name)}</td><td colspan="4" class="why">ไม่ใช่แผ่นเหล็ก ย้ายไปกลุ่มตัดไม่ได้</td></tr>`;
    let bd, src;
    if (mv) {
      const r = (mv.rows || []).find((x) => Math.abs(x.thickness - t) < 1e-6);
      bd = r && r.bd ? +r.bd : null; src = mv.mode === "base" ? `อิง ${esc(matName(mv.base))}` : "กำหนดเอง";
    } else {
      const r = bendRow(t, mid);
      bd = r ? r.bd : null; src = r ? (r.material ? `แถวเฉพาะ ${esc(mid)}` : `ตาราง ${esc(r.gauge || "")} (ทุกชนิด)`) : "ไม่อยู่ในตาราง ใช้ K-factor";
    }
    const existing = !mv && S.project.groups.find((g) => g.material_id === mid && Math.abs(g.thickness - t) < 1e-6);
    const code = mv ? `${esc(mv.id || "?")}${(mv.rows.find((x) => Math.abs(x.thickness - t) < 1e-6) || {}).gauge || "#" + fmtG(t) + "mm"}` : esc(existing ? existing.name : groupCode(mid, t));
    return `<tr><td>${esc(p.name)}</td><td class="n">${fmt(f.w)} × ${fmt(f.h)}</td><td class="n">${fmtG(t)}</td><td><b>${code}</b></td><td>${bd ? fmt(bd, 2) + " มม." : "–"} <small>${src}</small></td></tr>`;
  }).join("");
  return `<table class="otable mv-prev"><thead><tr><th>ชิ้นส่วน</th><th class="n">ขนาดแผ่นคลี่ (มม.)</th><th class="n">หนา</th><th>กลุ่มใหม่ / ไฟล์ DXF</th><th>ค่ายืด 90° ที่จะใช้</th></tr></thead><tbody>${rows}</tbody></table>`;
}
function syncMove() {
  const mv = S.move; if (!mv) return;
  const v = (id) => { const e = $("#" + id); return e ? e.value : undefined; };
  if (v("mvName") !== undefined) mv.name = v("mvName");
  if (v("nsId") !== undefined) { mv.id = v("nsId").trim(); mv.nm = v("nsName"); mv.price = v("nsPrice"); }
  const mode = document.querySelector("input[name=nsMode]:checked"); if (mode) mv.mode = mode.value;
  if (v("nsBase") !== undefined) mv.base = v("nsBase");
  document.querySelectorAll("[data-ns]").forEach((i) => { const r = mv.rows[+i.dataset.ns]; if (r) r[i.dataset.f] = i.dataset.f === "bd" ? i.value : i.value.trim(); });
}
function bindMoveBar() {
  const rerender = () => { syncMove(); renderOverview(); };
  $("#mvDone").onclick = () => { S.editGroups = false; S.picked.clear(); S.move = null; renderOverview(); };
  $("#mvTarget").onchange = (e) => { syncMove(); S.move.target = e.target.value; renderOverview(); const f = $("#nsId") || $("#mvName"); if (f) f.focus(); };
  document.querySelectorAll("input[name=nsMode]").forEach((r) => (r.onchange = rerender));
  const base = $("#nsBase"); if (base) base.onchange = () => { syncMove(); S.move.mode = "base"; renderOverview(); };
  const nsId = $("#nsId"); if (nsId) nsId.addEventListener("change", rerender);
  document.querySelectorAll("[data-ns][data-f=bd]").forEach((i) => i.addEventListener("change", rerender));
  const nm = $("#mvName"); if (nm) nm.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("#mvGo").click(); } });
  $("#mvGo").onclick = doMove;
}
async function doMove() {
  syncMove();
  const ids = [...S.picked], mv = S.move; if (!ids.length || !mv.target) return;
  const [kind, val] = mv.target.split("|");
  let target, label;
  try {
    if (kind === "new") {
      if (!mv.id) throw new Error("กรุณาใส่รหัสเหล็ก เช่น SUS");
      if (S.project.materials.some((m) => m.id.toLowerCase() === mv.id.toLowerCase())) throw new Error(`มีเหล็ก ${mv.id} อยู่แล้ว เลือกจากรายการ “ย้ายไปที่” แทน`);
      if (mv.rows.some((r) => !(+r.bd > 0))) throw new Error("ใส่ค่ายืดให้ครบทุกความหนา");
      const r = await sendJSON(`/api/jobs/${S.jobId}/materials`, "POST", {
        material: { id: mv.id, name: mv.nm || mv.id, prefix: mv.id }, base: mv.base,
        rows: mv.rows.map((x) => ({ thickness: x.thickness, bd: +x.bd, gauge: x.gauge })), price_kg: mv.price === "" ? null : +mv.price });
      S.profilesData = r.profiles; setProject(r.project); fillProfileSelects();
      target = { material_id: mv.id }; label = matName(mv.id);
    } else if (kind === "m") { target = { material_id: val }; label = matName(val); }
    else if (kind === "c") { target = { category: val }; label = `กลุ่ม “${val}”`; }
    else if (kind === "cnew") {
      const name = (mv.name || "").trim(); if (!name) throw new Error("กรุณาพิมพ์ชื่อกลุ่ม");
      target = { category: name }; label = `กลุ่ม “${name}”`;
    } else { target = { include: false }; label = "ไม่นำไปตัด"; }
    setProject(await sendJSON(`/api/jobs/${S.jobId}/move`, "POST", { ids, target }));
    S.picked.clear(); S.price = null; S.move = { target: "", base: mv.base, mode: "base" };
    renderTop(); renderRail(); renderInspector(); renderOverview();
    toast(`ย้าย ${ids.length} แบบไปที่ ${label} แล้ว${kind === "new" ? " · บันทึกเหล็กชนิดใหม่ไว้ในเครื่องแล้ว" : ""}`, 4000);
  } catch (e) { toast(e.message, 4500); }
}
function groupInspector(g) {
  if (!g) return "";
  const st = S.project.settings;
  const sheetOpts = [["", "ไม่จำกัด (แถวละไม่เกิน " + fmt(st.max_width, 0) + " มม.)"], ["1219x2438", "1219 × 2438 (4×8 ฟุต)"], ["1250x2500", "1250 × 2500"], ["1500x3000", "1500 × 3000"]];
  const cur = st.sheet ? st.sheet.join("x") : "";
  const total = g.sheets[S.sheetIdx] || g.sheets[0];
  return `
  <div class="sec"><h2 class="insp-title">${esc(g.filename)}</h2>
    <p class="insp-sub">${esc(g.code)} · เหล็กหนา ${fmtG(g.thickness)} มม.</p></div>
  <div class="sec"><dl class="kv">
    <dt>จำนวนแบบ</dt><dd>${g.part_ids.length}</dd>
    <dt>จำนวนชิ้นใน DXF</dt><dd>${g.pieces}</dd>
    <dt>ขนาดในไฟล์</dt><dd>${fmt(total.w, 0)} × ${fmt(total.h, 0)} มม.</dd>
    <dt>พื้นที่คลี่รวม</dt><dd>${fmt(g.area / 1e6, 3)} ตร.ม.</dd>
    <dt>น้ำหนักเหล็กรวม</dt><dd>${fmt(g.weight_kg, 2)} กก.</dd></dl></div>
  ${g.sheets.length > 1 ? `<div class="sec"><h3>แผ่นเหล็ก</h3><div class="dl-btns">${g.sheets.map((s) => `<button class="btn small ${s.index === S.sheetIdx ? "primary" : ""}" data-sheet="${s.index}">แผ่น ${s.index + 1} · ${s.count} ชิ้น${s.oversize ? " (ใหญ่เกินแผ่น)" : ""}</button>`).join("")}</div></div>` : ""}
  <div class="sec"><h3>การวางชิ้นในไฟล์</h3>
    <div class="row"><label for="sCopies">จำนวนที่ใส่ในไฟล์</label><select id="sCopies"><option value="qty" ${st.copies === "qty" ? "selected" : ""}>ตามจำนวนจริง</option><option value="one" ${st.copies === "one" ? "selected" : ""}>1 ชิ้นต่อแบบ</option></select></div>
    <div class="row"><label for="sSheet">ขนาดแผ่นเหล็ก</label><select id="sSheet">${sheetOpts.map(([v, l]) => `<option value="${v}" ${v === cur ? "selected" : ""}>${l}</option>`).join("")}</select></div>
    <div class="row"><label for="sGap">ระยะห่างชิ้น (มม.)</label><input type="number" id="sGap" min="0" step="1" value="${st.gap}"></div>
    <p class="hint">การวางนี้เป็นแค่ให้ชิ้นไม่ซ้อนกัน การจัดวางประหยัดเหล็ก (nesting) ทำต่อในโปรแกรมเครื่องเลเซอร์</p></div>
  <div class="sec"><h3>เนื้อหาในไฟล์ DXF</h3>
    <label class="switch"><input type="checkbox" id="sBend" ${st.bend_lines ? "checked" : ""}> ใส่เส้นพับ (เลเยอร์ BEND)</label>
    <label class="switch"><input type="checkbox" id="sLabel" ${st.labels ? "checked" : ""}> ใส่ชื่อชิ้น (เลเยอร์ LABEL)</label>
    <p class="hint">เส้นตัดอยู่เลเยอร์ CUT หน่วยมิลลิเมตร ถ้าเครื่องเลเซอร์ตัดทุกเลเยอร์ ให้ปิดเส้นพับและชื่อชิ้น</p></div>
  <div class="sec"><div class="dl-btns">${g.sheets.map((s) => `<button class="btn primary" data-dl="dxf/${g.id}/${s.index}">ดาวน์โหลด ${esc(s.filename)}</button>`).join("")}</div></div>`;
}
function bindGroupInspector() {
  const box = $("#inspector");
  box.querySelectorAll("[data-sheet]").forEach((b) => b.addEventListener("click", () => { S.sheetIdx = +b.dataset.sheet; renderInspector(); loadScene(); }));
  box.querySelectorAll("[data-dl]").forEach((b) => b.addEventListener("click", () => download(b.dataset.dl)));
  const upd = (patch) => patchSettings(patch);
  $("#sCopies").addEventListener("change", (e) => upd({ copies: e.target.value }));
  $("#sSheet").addEventListener("change", (e) => upd({ sheet: e.target.value ? e.target.value.split("x").map(Number) : null }));
  $("#sGap").addEventListener("change", (e) => upd({ gap: Math.max(0, +e.target.value || 0) }));
  $("#sBend").addEventListener("change", (e) => upd({ bend_lines: e.target.checked }));
  $("#sLabel").addEventListener("change", (e) => upd({ labels: e.target.checked }));
}
function partInspector(p) {
  const P = S.project, f = p.flat, c = p.checks || {}, t = p.thickness;
  const mats = P.materials.map((m) => `<option value="${esc(m.id)}" ${m.id === p.material_id ? "selected" : ""}>${esc(m.name)}</option>`).join("");
  let h = `<div class="sec"><h2 class="insp-title">${esc(p.name)}</h2><p class="insp-sub">จำนวนใน Assembly ${p.qty} ชิ้น · ผิว ${p.faces} ผิว</p></div>`;
  h += `<div class="sec"><label class="switch"><input type="checkbox" id="pInc" ${p.include ? "checked" : ""} ${p.status === "excluded" && p.reason_code === "not_sheet" ? "disabled" : ""}> นำไปตัด (ใส่ใน DXF)</label>`;
  if (!p.include && p.reason) h += `<p class="hint">${esc(p.reason)}</p>`;
  h += `</div>`;
  if (f) {
    h += `<div class="sec"><h3>แผ่นคลี่</h3><dl class="kv">
      <dt>ขนาด กว้าง × ยาว</dt><dd><b>${fmt(f.w)} × ${fmt(f.h)}</b> มม.</dd>
      <dt>พื้นที่</dt><dd>${fmt(f.area, 0)} ตร.มม.</dd>
      <dt>ความยาวเส้นตัด</dt><dd>${fmt(f.cut_len, 0)} มม.</dd>
      <dt>จำนวนรูภายใน</dt><dd>${f.holes}</dd>
      <dt>น้ำหนัก / ชิ้น</dt><dd>${fmt(f.weight_kg, 3)} กก.</dd></dl>
      <label class="switch" style="margin-top:10px"><input type="checkbox" id="pFlip" ${p.flip ? "checked" : ""}> กลับด้านแผ่นคลี่ (กระจกซ้าย-ขวา)</label></div>`;
  }
  if (t.measured) {
    h += `<div class="sec"><h3>วัสดุและความหนา</h3>
      <div class="row"><label for="pMat">ชนิดเหล็ก</label><select id="pMat">${mats}</select></div>
      <dl class="kv"><dt>ความหนาที่วัดได้</dt><dd>${fmt(t.measured, 3)} มม.</dd>
      <dt>ความหนาที่ใช้คำนวณ</dt><dd>${fmtG(t.used)} มม.</dd>
      <dt>เบอร์ / ค่ายืด 90°</dt><dd>${t.matched ? `${esc(t.gauge)} / ${fmt(t.bd, 2)} มม.` : "ไม่อยู่ในตาราง"}</dd>
      <dt>ชื่อไฟล์กลุ่ม</dt><dd>${esc(p.code)}</dd></dl>
      <div class="row"><label for="pT">ระบุความหนาเอง (มม.)</label><input type="number" id="pT" step="0.01" min="0" placeholder="${fmtG(t.used)}" value="${t.override ?? ""}"></div></div>`;
  }
  if (f) {
    h += `<div class="sec"><h3>ตรวจสอบความถูกต้อง</h3>`;
    const dev = c.volume_dev_pct;
    if (dev !== null && dev !== undefined) {
      const k = Math.abs(dev) <= 0.5 ? "ok" : Math.abs(dev) <= 1.5 ? "warn" : "bad";
      h += chk(k, `พื้นที่คลี่ × ความหนา ตรงกับปริมาตรชิ้นงาน (${dev >= 0 ? "+" : ""}${fmt(dev, 3)}%)`, "คำนวณแยกอิสระด้วย K = 0.5 เทียบกับปริมาตรจากโมเดล 3D");
    }
    h += chk(c.closed ? "ok" : "bad", c.closed ? "เส้นตัดทุกเส้นปิดสนิท" : "มีเส้นตัดไม่ปิด");
    h += chk((c.thickness_weight || 0) > 0.8 ? "ok" : "warn", `ความหนา ${fmt(t.measured, 3)} มม. ยืนยันจากผิวคู่ขนาน ${fmt((c.thickness_weight || 0) * 100, 0)}% ของพื้นที่ระนาบ`);
    h += chk(t.matched ? "ok" : "warn", t.matched ? `ความหนาตรงกับตาราง (${esc(t.gauge)})` : "ความหนาไม่อยู่ในตารางค่ายืด ใช้ K-factor กับทุกรอยพับ");
    h += `</div>`;
    h += `<div class="sec"><h3>รอยพับ ${f.bends.length ? "(" + f.bends.length + " จุด)" : ""}</h3>`;
    if (f.bends.length) {
      h += `<table class="bt"><thead><tr><th>#</th><th class="n">มุม</th><th class="n">R ใน</th><th>วิธี</th><th class="n">BA</th></tr></thead><tbody>` +
        f.bends.map((b, i) => `<tr data-bend="${i}"><td>${i + 1}</td><td class="n">${fmt(b.angle, 1)}°</td><td class="n">${fmt(b.r, 2)}</td><td><span class="tag ${b.method === "table" ? "tbl" : ""}">${b.method === "table" ? "ตาราง BD " + fmt(b.bd, 2) : "K " + b.k}</span></td><td class="n">${fmt(b.ba, 2)}</td></tr>`).join("") +
        `</tbody></table><p class="hint">มุม = องศาที่พับออกจากแนวเรียบ (พับฉาก = 90°, พับทบ = 180°) · BA = ความกว้างแถบพับในแผ่นคลี่ · ชี้เมาส์ที่แถวเพื่อดูเส้นพับในแบบ</p>`;
    } else h += `<p class="insp-sub">ชิ้นนี้เป็นแผ่นเรียบ ไม่มีรอยพับ</p>`;
    h += `</div>`;
  }
  if (p.warnings.length) h += `<div class="sec"><h3>ข้อควรตรวจ</h3><ul class="warnlist">${p.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>`;
  if (!f) h += `<div class="sec"><dl class="kv"><dt>ขนาด 3D</dt><dd>${p.bbox3.map((x) => fmt(x, 1)).join(" × ")} มม.</dd><dt>ปริมาตร</dt><dd>${fmt(p.volume, 0)} ลบ.มม.</dd></dl></div>`;
  return h;
}
function bindPartInspector(p) {
  const inc = $("#pInc"); if (inc) inc.addEventListener("change", () => patchPart(p.id, { include: inc.checked }));
  const m = $("#pMat"); if (m) m.addEventListener("change", () => patchPart(p.id, { material_id: m.value }));
  const t = $("#pT"); if (t) t.addEventListener("change", () => patchPart(p.id, { t_override: t.value === "" ? null : +t.value }));
  const fl = $("#pFlip"); if (fl) fl.addEventListener("change", () => patchPart(p.id, { flip: fl.checked }));
  document.querySelectorAll("tr[data-bend]").forEach((tr) => {
    tr.addEventListener("mouseenter", () => viewer && viewer.highlightBend(+tr.dataset.bend));
    tr.addEventListener("mouseleave", () => viewer && viewer.highlightBend(-1));
  });
}

async function patchPart(pid, body) {
  try { setProject(await sendJSON(`/api/jobs/${S.jobId}/parts/${pid}`, "PATCH", body)); } catch (e) { toast(e.message); renderInspector(); return; }
  const p = part(pid);
  if (S.sel && S.sel.kind === "part" && !p.include) { const g = S.project.groups[0]; /* keep part selected */ }
  renderAll();
}
async function patchSettings(body) {
  try { setProject(await sendJSON(`/api/jobs/${S.jobId}/settings`, "PATCH", body)); } catch (e) { toast(e.message); renderInspector(); return; }
  S.sheetIdx = 0; renderAll();
}
function download(rel) {
  if (DEMO) {
    const files = (S.demoFiles || {});
    const f = files[rel];
    if (!f) { toast(DEMO_MSG); return; }
    const a = document.createElement("a"); a.href = DEMO + f.path; a.download = f.name; document.body.appendChild(a); a.click(); a.remove();
    toast("กำลังดาวน์โหลด " + f.name); return;
  }
  location.href = `/api/jobs/${S.jobId}/export/${rel}`;
  toast(rel === "all" ? "กำลังดาวน์โหลด DXF ทั้งหมด + Excel" : rel === "xlsx" ? "กำลังดาวน์โหลดใบสรุป Excel" : "กำลังดาวน์โหลดไฟล์ DXF");
}

/* ======================================================================
   Canvas viewer
   ====================================================================== */
class Viewer {
  constructor(cv) {
    this.cv = cv; this.ctx = cv.getContext("2d");
    this.scene = null; this.s = 1; this.ox = 0; this.oy = 0;
    this.opts = { bend: true, dim: true, name: true, measure: false };
    this.hover = null; this.pt = null; this.measure = []; this.snap = null; this.hiBend = -1;
    this.drag = null; this.dpr = 1;
    new ResizeObserver(() => this.resize()).observe(cv.parentElement);
    cv.addEventListener("wheel", (e) => this.onWheel(e), { passive: false });
    cv.addEventListener("pointerdown", (e) => this.onDown(e));
    cv.addEventListener("pointermove", (e) => this.onMove(e));
    cv.addEventListener("pointerup", (e) => this.onUp(e));
    cv.addEventListener("pointerleave", () => { this.hover = null; this.pt = null; this.draw(); setStatus(); });
    window.addEventListener("keydown", (e) => this.onKey(e));
    this.colors();
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { this.colors(); this.draw(); });
  }
  colors() {
    const cs = getComputedStyle(document.documentElement), g = (n) => cs.getPropertyValue(n).trim();
    this.c = { ink: g("--ink"), bend: g("--bend"), dim: g("--dim"), paper: g("--paper"), zinc: g("--zinc-100"), grid: g("--zinc-200"), fill: g("--zinc-200"), sel: g("--sel"), selBg: g("--sel-bg"), steel: g("--steel") };
  }
  resize() {
    const r = this.cv.parentElement.getBoundingClientRect();
    this.dpr = window.devicePixelRatio || 1;
    this.cv.width = Math.max(1, Math.round(r.width * this.dpr)); this.cv.height = Math.max(1, Math.round(r.height * this.dpr));
    this.W = r.width; this.H = r.height;
    if (this.scene && !this.fitted) this.fit(); else this.draw();
  }
  setScene(scene, keepView) {
    this.scene = scene; this.measure = []; this.hover = null; this.hiBend = -1;
    this.buildIndex();
    if (!keepView) this.fit(); else this.draw();
  }
  buildIndex() {
    const sc = this.scene; if (!sc) return;
    this.paths = sc.items.map((it) => {
      const p = new Path2D(); // placeholder - real path built per frame (depends on transform)
      return p;
    });
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    sc.items.forEach((it) => { x0 = Math.min(x0, it.x); y0 = Math.min(y0, it.y); x1 = Math.max(x1, it.x + it.w); y1 = Math.max(y1, it.y + it.h); });
    this.bounds = { x0, y0, x1, y1 };
  }
  fit() {
    if (!this.scene || !this.bounds || !this.W) return;
    const b = this.bounds, L = 50, R = 90, T = 40, B = 90, bw = Math.max(1, b.x1 - b.x0), bh = Math.max(1, b.y1 - b.y0);
    this.s = Math.max(0.01, Math.min((this.W - L - R) / bw, (this.H - T - B) / bh));
    this.ox = L + ((this.W - L - R) - bw * this.s) / 2 - b.x0 * this.s;
    this.oy = T + ((this.H - T - B) + bh * this.s) / 2 + b.y0 * this.s;
    this.fitted = true; this.draw();
  }
  w2s(x, y) { return [this.ox + x * this.s, this.oy - y * this.s]; }
  s2w(px, py) { return [(px - this.ox) / this.s, (this.oy - py) / this.s]; }
  onWheel(e) {
    e.preventDefault();
    const r = this.cv.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
    const k = Math.exp(-e.deltaY * 0.0015), [wx, wy] = this.s2w(px, py);
    this.s = Math.min(Math.max(this.s * k, 0.02), 400);
    this.ox = px - wx * this.s; this.oy = py + wy * this.s; this.fitted = true; this.draw();
  }
  pos(e) { const r = this.cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
  onDown(e) {
    this.cv.setPointerCapture(e.pointerId);
    const [px, py] = this.pos(e);
    this.drag = { px, py, ox: this.ox, oy: this.oy, moved: false };
  }
  onMove(e) {
    const [px, py] = this.pos(e);
    if (this.drag) {
      const dx = px - this.drag.px, dy = py - this.drag.py;
      if (Math.abs(dx) + Math.abs(dy) > 3) { this.drag.moved = true; this.cv.classList.add("pan"); }
      if (this.drag.moved) { this.ox = this.drag.ox + dx; this.oy = this.drag.oy + dy; this.fitted = true; this.draw(); return; }
    }
    this.pt = [px, py];
    this.pick(px, py); this.draw(); setStatus(this);
  }
  onUp(e) {
    const moved = this.drag && this.drag.moved;
    this.drag = null; this.cv.classList.remove("pan");
    if (moved) return;
    const [px, py] = this.pos(e);
    if (this.opts.measure) {
      const p = this.snap ? this.snap.p : this.s2w(px, py);
      if (this.measure.length >= 2) this.measure = [];
      this.measure.push(p); this.draw(); setStatus(this); return;
    }
    const it = this.itemAt(px, py);
    if (it && this.scene.mode === "group" && it.part_id && S.sel && (S.sel.kind !== "part" || S.sel.id !== it.part_id)) selectPart(it.part_id);
  }
  onKey(e) {
    if (e.target.closest("input,select,textarea,dialog")) return;
    const k = e.key.toLowerCase();
    if (k === "f") this.fit();
    else if (k === "b") toggleOpt("bend"); else if (k === "d") toggleOpt("dim"); else if (k === "n") toggleOpt("name"); else if (k === "m") toggleOpt("measure");
    else if (e.key === "Escape") { this.measure = []; this.draw(); }
  }
  itemAt(px, py) {
    if (!this.scene) return null;
    const [wx, wy] = this.s2w(px, py);
    for (let i = this.scene.items.length - 1; i >= 0; i--) { const it = this.scene.items[i]; if (wx >= it.x && wx <= it.x + it.w && wy >= it.y && wy <= it.y + it.h) return it; }
    return null;
  }
  /* ---- picking & snapping ---- */
  pick(px, py) {
    this.hover = null; this.snap = null;
    if (!this.scene) return;
    const [wx, wy] = this.s2w(px, py), tol = 7 / this.s;
    let best = null, bd = tol;
    let sbest = null, sd = 11 / this.s;
    const consider = (kind, d, info) => { if (d < bd) { bd = d; best = { kind, info }; } };
    const snapTo = (x, y, label) => { const d = Math.hypot(x - wx, y - wy); if (d < sd) { sd = d; sbest = { p: [x, y], label }; } };
    for (const it of this.scene.items) {
      if (wx < it.x - tol * 2 || wx > it.x + it.w + tol * 2 || wy < it.y - tol * 2 || wy > it.y + it.h + tol * 2) continue;
      for (const q of it.prims) {
        if (q.t === "line") {
          const d = distSeg(wx, wy, q.a[0], q.a[1], q.b[0], q.b[1]);
          consider("line", d, q); snapTo(q.a[0], q.a[1], "ปลายเส้น"); snapTo(q.b[0], q.b[1], "ปลายเส้น");
          snapTo((q.a[0] + q.b[0]) / 2, (q.a[1] + q.b[1]) / 2, "กึ่งกลาง");
        } else if (q.t === "circle") {
          const d = Math.abs(Math.hypot(wx - q.c[0], wy - q.c[1]) - q.r);
          consider("circle", d, q); snapTo(q.c[0], q.c[1], "จุดศูนย์กลางรู");
          [[1, 0], [0, 1], [-1, 0], [0, -1]].forEach(([a, b]) => snapTo(q.c[0] + a * q.r, q.c[1] + b * q.r, "ขอบรู"));
        } else if (q.t === "arc") {
          const dd = Math.hypot(wx - q.c[0], wy - q.c[1]);
          let a = Math.atan2(wy - q.c[1], wx - q.c[0]) * 180 / Math.PI; a = (a + 360) % 360;
          if (angIn(a, q.a0, q.a1)) consider("arc", Math.abs(dd - q.r), q);
          [q.a0, q.a1].forEach((ang) => snapTo(q.c[0] + q.r * Math.cos(ang * Math.PI / 180), q.c[1] + q.r * Math.sin(ang * Math.PI / 180), "ปลายส่วนโค้ง"));
          snapTo(q.c[0], q.c[1], "จุดศูนย์กลางส่วนโค้ง");
        } else {
          let d = Infinity;
          for (let i = 0; i < q.pts.length - 1; i++) d = Math.min(d, distSeg(wx, wy, q.pts[i][0], q.pts[i][1], q.pts[i + 1][0], q.pts[i + 1][1]));
          consider("poly", d, q);
          snapTo(q.pts[0][0], q.pts[0][1], "ปลายเส้น"); snapTo(q.pts[q.pts.length - 1][0], q.pts[q.pts.length - 1][1], "ปลายเส้น");
        }
      }
      if (this.opts.bend) for (const b of it.bends) consider("bend", distSeg(wx, wy, b.a[0], b.a[1], b.b[0], b.b[1]), b);
      [[it.x, it.y], [it.x + it.w, it.y], [it.x, it.y + it.h], [it.x + it.w, it.y + it.h]].forEach(([x, y]) => snapTo(x, y, "มุมกรอบ"));
    }
    this.hover = best; this.snap = sbest;
  }
  highlightBend(i) { this.hiBend = i; this.draw(); }

  /* ---- drawing ---- */
  draw() {
    const ctx = this.ctx, c = this.c; if (!this.W) return;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.W, this.H);
    ctx.fillStyle = c.zinc; ctx.fillRect(0, 0, this.W, this.H);
    this.drawGrid();
    const sc = this.scene; if (!sc) return;
    const selPart = S.sel && S.sel.kind === "part" ? S.sel.id : null;
    // fills
    for (const it of sc.items) {
      const path = new Path2D();
      for (const l of it.loops) { l.pts.forEach(([x, y], i) => { const [sx, sy] = this.w2s(x, y); i ? path.lineTo(sx, sy) : path.moveTo(sx, sy); }); path.closePath(); }
      ctx.fillStyle = sc.mode === "group" && selPart && it.part_id === selPart ? c.selBg : c.paper;
      ctx.fill(path, "evenodd");
    }
    // cut lines
    ctx.lineWidth = 1.3; ctx.lineJoin = "round"; ctx.lineCap = "round";
    for (const it of sc.items) {
      ctx.strokeStyle = sc.mode === "group" && selPart && it.part_id === selPart ? c.sel : c.ink;
      ctx.beginPath();
      for (const q of it.prims) this.tracePrim(q);
      ctx.stroke();
    }
    // bends
    if (this.opts.bend) {
      ctx.setLineDash([7, 4]); ctx.lineWidth = 1.1; ctx.strokeStyle = c.bend;
      ctx.beginPath();
      for (const it of sc.items) for (const b of it.bends) { const [a1, a2] = this.w2s(...b.a), [b1, b2] = this.w2s(...b.b); ctx.moveTo(a1, a2); ctx.lineTo(b1, b2); }
      ctx.stroke(); ctx.setLineDash([]);
      if (this.hiBend >= 0 && sc.mode === "part") {
        const b = sc.items[0].bends[this.hiBend];
        if (b) { ctx.lineWidth = 4; ctx.strokeStyle = c.bend; ctx.globalAlpha = 0.45; ctx.beginPath(); const [a1, a2] = this.w2s(...b.a), [b1, b2] = this.w2s(...b.b); ctx.moveTo(a1, a2); ctx.lineTo(b1, b2); ctx.stroke(); ctx.globalAlpha = 1; }
      }
    }
    // hover highlight
    if (this.hover && !this.opts.measure) {
      const h = this.hover; ctx.lineWidth = 3.2; ctx.strokeStyle = c.dim; ctx.globalAlpha = 0.8; ctx.beginPath();
      if (h.kind === "bend") { const [a1, a2] = this.w2s(...h.info.a), [b1, b2] = this.w2s(...h.info.b); ctx.moveTo(a1, a2); ctx.lineTo(b1, b2); } else this.tracePrim(h.info);
      ctx.stroke(); ctx.globalAlpha = 1;
    }
    this.drawAnnotations();
    this.drawMeasure();
    if (this.opts.measure && this.snap) {
      const [sx, sy] = this.w2s(...this.snap.p); ctx.strokeStyle = c.dim; ctx.lineWidth = 1.5; ctx.strokeRect(sx - 5, sy - 5, 10, 10);
    }
    $("#stZoom").textContent = `1 px = ${fmt(1 / this.s, this.s > 4 ? 3 : 2)} มม.`;
  }
  tracePrim(q) {
    const ctx = this.ctx;
    if (q.t === "line") { const [x1, y1] = this.w2s(...q.a), [x2, y2] = this.w2s(...q.b); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); }
    else if (q.t === "circle") { const [x, y] = this.w2s(...q.c); ctx.moveTo(x + q.r * this.s, y); ctx.arc(x, y, q.r * this.s, 0, Math.PI * 2); }
    else if (q.t === "arc") {
      const [x, y] = this.w2s(...q.c), a0 = -q.a0 * Math.PI / 180, a1 = -q.a1 * Math.PI / 180;
      ctx.moveTo(x + q.r * this.s * Math.cos(a0), y + q.r * this.s * Math.sin(a0)); ctx.arc(x, y, q.r * this.s, a0, a1, true);
    } else { q.pts.forEach(([px, py], i) => { const [x, y] = this.w2s(px, py); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }); }
  }
  drawGrid() {
    const ctx = this.ctx, c = this.c;
    let step = 10; while (step * this.s < 14) step *= 10;
    const [x0, y1] = this.s2w(0, this.H), [x1, y0] = this.s2w(this.W, 0);
    ctx.strokeStyle = c.grid; ctx.lineWidth = 0.6; ctx.globalAlpha = 0.7; ctx.beginPath();
    for (let x = Math.floor(x0 / step) * step; x <= x1; x += step) { const [sx] = this.w2s(x, 0); ctx.moveTo(sx, 0); ctx.lineTo(sx, this.H); }
    for (let y = Math.floor(y1 / step) * step; y <= y0; y += step) { const [, sy] = this.w2s(0, y); ctx.moveTo(0, sy); ctx.lineTo(this.W, sy); }
    ctx.stroke(); ctx.globalAlpha = 1;
  }
  text(txt, x, y, o = {}) {
    const ctx = this.ctx; ctx.font = `${o.size || 12}px ${getComputedStyle(document.body).fontFamily}`;
    ctx.textAlign = o.align || "center"; ctx.textBaseline = o.base || "middle";
    const w = ctx.measureText(txt).width;
    if (o.bg !== false) { ctx.fillStyle = this.c.zinc; ctx.globalAlpha = 0.85; ctx.fillRect(x - (o.align === "left" ? 2 : o.align === "right" ? w + 2 : w / 2 + 3), y - 8, w + 6, 16); ctx.globalAlpha = 1; }
    ctx.fillStyle = o.color || this.c.dim; ctx.fillText(txt, x, y);
  }
  dimLine(ax, ay, bx, by, label, off, vertical) {
    // dimension line between world points A,B offset by `off` px perpendicular
    const ctx = this.ctx, c = this.c;
    let [x1, y1] = this.w2s(ax, ay), [x2, y2] = this.w2s(bx, by);
    if (vertical) { x1 += off; x2 += off; } else { y1 += off; y2 += off; }
    ctx.strokeStyle = c.dim; ctx.lineWidth = 1; ctx.beginPath();
    const [ex1, ey1] = this.w2s(ax, ay), [ex2, ey2] = this.w2s(bx, by);
    ctx.moveTo(ex1, ey1); ctx.lineTo(x1 + (vertical ? Math.sign(off) * 4 : 0), y1 + (vertical ? 0 : Math.sign(off) * 4));
    ctx.moveTo(ex2, ey2); ctx.lineTo(x2 + (vertical ? Math.sign(off) * 4 : 0), y2 + (vertical ? 0 : Math.sign(off) * 4));
    ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    const arrow = (x, y, dx, dy) => { ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + dx * 8 - dy * 3, y + dy * 8 + dx * 3); ctx.lineTo(x + dx * 8 + dy * 3, y + dy * 8 - dx * 3); ctx.closePath(); ctx.fillStyle = c.dim; ctx.fill(); };
    const L = Math.hypot(x2 - x1, y2 - y1) || 1, ux = (x2 - x1) / L, uy = (y2 - y1) / L;
    if (L > 24) { arrow(x1, y1, ux, uy); arrow(x2, y2, -ux, -uy); }
    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
    if (vertical) { ctx.save(); ctx.translate(mx + (off > 0 ? 12 : -12), my); ctx.rotate(-Math.PI / 2); this.text(label, 0, 0, { size: 12.5, color: c.dim }); ctx.restore(); }
    else this.text(label, mx, my + (off > 0 ? 12 : -12), { size: 12.5, color: c.dim });
  }
  drawAnnotations() {
    const sc = this.scene, ctx = this.ctx, c = this.c;
    if (!this.opts.dim && !this.opts.name) return;
    if (sc.mode === "part") {
      const it = sc.items[0];
      if (this.opts.dim) {
        this.dimLine(it.x, it.y, it.x + it.w, it.y, fmt(it.w), 30, false);
        this.dimLine(it.x + it.w, it.y, it.x + it.w, it.y + it.h, fmt(it.h), 30, true);
        for (const q of it.prims) if (q.t === "circle" && q.r * this.s > 6) { const [x, y] = this.w2s(...q.c); this.text("Ø" + fmt(q.r * 2, q.r * 2 >= 10 ? 1 : 2).replace(/\.00$/, ""), x, y, { size: 11, bg: false, color: c.dim }); }
        if (this.opts.bend) for (const b of it.bends) {
          const [x, y] = this.w2s((b.a[0] + b.b[0]) / 2, (b.a[1] + b.b[1]) / 2);
          this.text(`${b.dir === "up" ? "พับขึ้น" : "พับลง"} ${fmt(b.angle, b.angle % 1 ? 1 : 0)}°`, x, y, { size: 11.5, color: c.bend });
        }
      }
      return;
    }
    const b = this.bounds;
    if (this.opts.dim) {
      this.dimLine(b.x0, b.y0, b.x1, b.y0, fmt(b.x1 - b.x0, 0), 34, false);
      this.dimLine(b.x1, b.y0, b.x1, b.y1, fmt(b.y1 - b.y0, 0), 34, true);
    }
    for (const it of sc.items) {
      const pw = it.w * this.s;
      if (pw < 46) continue;
      const [cx, cy] = this.w2s(it.x + it.w / 2, it.y + it.h / 2);
      if (this.opts.name) this.text(it.name + (it.copy > 1 ? " #" + it.copy : ""), cx, cy, { size: 12.5, color: c.ink });
      if (this.opts.dim && pw > 90) this.text(`${fmt(it.w, 1)} × ${fmt(it.h, 1)}`, cx, cy + 17, { size: 11, color: c.dim });
    }
  }
  drawMeasure() {
    const m = this.measure, ctx = this.ctx, c = this.c;
    const pts = m.slice(); if (this.opts.measure && pts.length === 1) pts.push(this.snap ? this.snap.p : (this.pt ? this.s2w(...this.pt) : pts[0]));
    pts.forEach((p) => { const [x, y] = this.w2s(...p); ctx.fillStyle = c.bend; ctx.beginPath(); ctx.arc(x, y, 3.5, 0, 6.3); ctx.fill(); });
    if (pts.length === 2) {
      const [a, b] = pts, [x1, y1] = this.w2s(...a), [x2, y2] = this.w2s(...b);
      ctx.strokeStyle = c.bend; ctx.lineWidth = 1.6; ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
      const d = Math.hypot(b[0] - a[0], b[1] - a[1]);
      this.text(`${fmt(d)} มม.  (Δx ${fmt(Math.abs(b[0] - a[0]))} · Δy ${fmt(Math.abs(b[1] - a[1]))})`, (x1 + x2) / 2, (y1 + y2) / 2 - 14, { size: 13, color: c.bend });
    }
  }
}

const distSeg = (px, py, ax, ay, bx, by) => {
  const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
  let t = l2 ? ((px - ax) * dx + (py - ay) * dy) / l2 : 0; t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
};
const angIn = (a, a0, a1) => { a0 = (a0 + 360) % 360; a1 = (a1 + 360) % 360; return a0 <= a1 ? a >= a0 && a <= a1 : a >= a0 || a <= a1; };
const primLen = (q) => {
  if (q.t === "line") return Math.hypot(q.b[0] - q.a[0], q.b[1] - q.a[1]);
  if (q.t === "circle") return 2 * Math.PI * q.r;
  if (q.t === "arc") { let d = (q.a1 - q.a0 + 360) % 360; return q.r * d * Math.PI / 180; }
  let L = 0; for (let i = 0; i < q.pts.length - 1; i++) L += Math.hypot(q.pts[i + 1][0] - q.pts[i][0], q.pts[i + 1][1] - q.pts[i][1]); return L;
};

let viewer = null;
function setStatus(v) {
  const co = $("#stCoord"), info = $("#stInfo");
  if (!v || !v.pt) { co.textContent = "X – · Y –"; return; }
  const [x, y] = v.s2w(...v.pt); co.textContent = `X ${fmt(x, 1)} · Y ${fmt(y, 1)}`;
  if (v.opts.measure) {
    const n = v.measure.length;
    info.innerHTML = n === 0 ? "โหมดวัดระยะ: คลิกจุดแรก (ดูดจุดปลายเส้น/จุดศูนย์กลางรูอัตโนมัติ) · Esc ล้าง" : n === 1 ? "คลิกจุดที่สอง" : "คลิกอีกครั้งเพื่อวัดใหม่";
    return;
  }
  const h = v.hover;
  if (!h) { info.textContent = "เลื่อนเมาส์ไปที่เส้นเพื่อดูความยาว/ขนาดรู · ลากเพื่อเลื่อน · ล้อเมาส์เพื่อซูม"; return; }
  const q = h.info;
  if (h.kind === "circle") info.innerHTML = `รู <b>Ø ${fmt(q.r * 2)} มม.</b> · รัศมี ${fmt(q.r)}`;
  else if (h.kind === "arc") info.innerHTML = `ส่วนโค้ง <b>R ${fmt(q.r)} มม.</b> · ยาวตามโค้ง ${fmt(primLen(q))} มม.`;
  else if (h.kind === "line") { let a = Math.atan2(q.b[1] - q.a[1], q.b[0] - q.a[0]) * 180 / Math.PI; info.innerHTML = `เส้นตรง ยาว <b>${fmt(primLen(q))} มม.</b> · ${fmt(((a % 180) + 180) % 180, 1)}°`; }
  else if (h.kind === "bend") info.innerHTML = `เส้นพับ <b>${h.info.dir === "up" ? "พับขึ้น" : "พับลง"} ${fmt(h.info.angle, 1)}°</b>${h.info.r !== undefined ? " · R ใน " + fmt(h.info.r, 2) + " มม." : ""}${h.info.method ? " · " + (h.info.method === "table" ? "ตารางค่ายืด" : "K-factor") : ""}`;
  else info.innerHTML = `เส้นโค้งอิสระ ยาว <b>${fmt(primLen(q))} มม.</b>`;
}
function toggleOpt(k) {
  viewer.opts[k] = !viewer.opts[k];
  const id = { bend: "tBend", dim: "tDim", name: "tName", measure: "tMeasure" }[k];
  $("#" + id).setAttribute("aria-pressed", viewer.opts[k]);
  if (k === "measure") { viewer.measure = []; setStatus(viewer); }
  viewer.draw();
}

async function loadScene() {
  if (!viewer) return;
  const P = S.project, sel = S.sel;
  $("#stageEmpty").hidden = !!sel;
  if (!sel) { viewer.setScene(null); return; }
  const isOv = sel.kind === "overview";
  $("#overview").hidden = !isOv; $(".stage-bar .tools").hidden = isOv; $(".legend").hidden = isOv; $(".statusbar").hidden = isOv;
  if (isOv) { $("#stageTitle").innerHTML = `ภาพรวมงาน<small>รายการตัดแยกตามความหนา</small>`; renderOverview(); return; }
  const key = sel.kind + sel.id + ":" + S.sheetIdx + ":" + S.version;
  let sc = S.cache.get(key);
  try {
    if (!sc) {
      if (sel.kind === "group") {
        const g = P.groups.find((x) => x.id === sel.id); if (!g) { viewer.setScene(null); return; }
        const L = await getJSON(`/api/jobs/${S.jobId}/groups/${g.id}/layout/${Math.min(S.sheetIdx, g.sheets.length - 1)}`);
        sc = { mode: "group", title: L.filename, sub: `${fmt(L.w, 0)} × ${fmt(L.h, 0)} มม. · ${L.items.length} ชิ้น`, items: L.items };
      } else {
        const p = part(sel.id);
        if (!p.flat) { sc = { mode: "part", title: p.name, sub: "ไม่มีแบบคลี่ (ไม่นำไปตัด)", items: [] }; }
        else {
          const G = await getJSON(`/api/jobs/${S.jobId}/parts/${p.id}/geometry`);
          let off = 0;
          const items = G.pieces.map((pc) => { const it = { part_id: p.id, name: p.name, copy: 1, x: off, y: 0, w: pc.w, h: pc.h, prims: pc.prims, loops: pc.loops, bends: pc.bends }; off += pc.w + 20; return it; });
          // pieces are normalised at 0,0 -> shift later pieces
          items.forEach((it, i) => { if (i) { const dx = it.x; shiftItem(it, dx); } });
          sc = { mode: "part", title: p.name, sub: `${fmt(items[0].w)} × ${fmt(items[0].h)} มม. · ${p.code}`, items };
        }
      }
      S.cache.set(key, sc);
    }
  } catch (e) { toast(e.message); renderInspector(); return; }
  if (S.sel !== sel) return;
  $("#stageTitle").innerHTML = `${esc(sc.title)}<small>${esc(sc.sub)}</small>`;
  $("#stageEmpty").hidden = sc.items.length > 0;
  viewer.setScene(sc.items.length ? sc : null);
}
function shiftItem(it, dx) {
  it.prims = it.prims.map((q) => { const r = { ...q }; const f = ([x, y]) => [x + dx, y]; if (q.t === "line") { r.a = f(q.a); r.b = f(q.b); } else if (q.t === "poly") r.pts = q.pts.map(f); else r.c = f(q.c); return r; });
  it.loops = it.loops.map((l) => ({ role: l.role, pts: l.pts.map(([x, y]) => [x + dx, y]) }));
  it.bends = it.bends.map((b) => ({ ...b, a: [b.a[0] + dx, b.a[1]], b: [b.b[0] + dx, b.b[1]] }));
}

/* ======================================================================
   Profile (bend table) editor
   ====================================================================== */
let editing = null;
function openProfileDialog(curId, makeNew) {
  editing = JSON.parse(JSON.stringify(S.profilesData));
  editing.cur = (typeof curId === "string" && curId) || $("#profileSel").value || editing.active;
  if (makeNew) {
    const base = editing.profiles.find((x) => x.id === editing.cur) || editing.profiles[0];
    const n = JSON.parse(JSON.stringify(base));
    n.id = "m" + Date.now().toString(36); n.name = "โหมดใหม่ " + (editing.profiles.length + 1);
    editing.profiles.push(n); editing.cur = n.id; editing.fresh = n.id;
  }
  $("#profileMsg").textContent = "บันทึกไว้ในเครื่องนี้ ใช้ได้กับทุกงานถัดไป";
  renderProfileDialog();
  $("#profileDlg").showModal();
  if (makeNew) { const nm = $("#pfName"); nm.focus(); nm.select(); }
}
function renderProfileDialog() {
  const d = editing, p = d.profiles.find((x) => x.id === d.cur) || d.profiles[0];
  d.cur = p.id;
  const matOpts = (sel) => `<option value="" ${!sel ? "selected" : ""}>ทุกชนิด</option>` +
    d.materials.map((m) => `<option value="${esc(m.id)}" ${m.id === sel ? "selected" : ""}>${esc(m.id)}</option>`).join("");
  $("#profileBody").innerHTML = `
    <div class="row"><label for="pfSel">โหมด</label>
      <select id="pfSel" style="max-width:none;flex:1">${d.profiles.map((x) => `<option value="${esc(x.id)}" ${x.id === p.id ? "selected" : ""}>${esc(x.name)}${x.id === d.fresh ? " (ใหม่)" : ""}</option>`).join("")}</select>
      <button type="button" class="btn small" id="pfDup">สร้างโหมดใหม่จากโหมดนี้</button>
      <button type="button" class="btn small" id="pfDel" ${d.profiles.length < 2 ? "disabled" : ""}>ลบโหมดนี้</button></div>
    <div class="grid2">
      <label>ชื่อโหมด<input type="text" id="pfName" value="${esc(p.name)}"></label>
      <label>K-factor (รอยพับไม่ฉาก / โค้ง)<input type="number" id="pfK" step="0.01" min="0.05" max="0.99" value="${p.k_factor}"></label>
    </div>
    <h3 class="dlg-h3">ตารางค่ายืด สำหรับรอยพับ 90°</h3>
    <p class="hint">แผ่นก่อนพับ = ผลรวมขนาดด้านนอกทั้งสองด้าน − ค่ายืด · ถ้าเหล็กชนิดใดมีค่ายืดต่างจากปกติ เพิ่มแถวที่ความหนาเดียวกันแล้วเลือกชนิดเหล็กในช่อง “ใช้กับ” แถวนั้นจะใช้แทนแถว “ทุกชนิด”</p>
    <table class="pt"><thead><tr><th>เบอร์</th><th>ความหนา (มม.)</th><th>ค่ายืด (มม.)</th><th>ใช้กับ</th><th></th></tr></thead><tbody id="pfRows">
      ${p.table.map((r, i) => `<tr><td><input type="text" data-r="${i}" data-f="gauge" value="${esc(r.gauge)}" aria-label="เบอร์"></td><td><input type="number" step="0.01" min="0" data-r="${i}" data-f="thickness" value="${r.thickness || ""}" aria-label="ความหนา"></td><td><input type="number" step="0.01" min="0" data-r="${i}" data-f="bd" value="${r.bd || ""}" aria-label="ค่ายืด"></td><td><select class="mat-sel" data-r="${i}" data-f="material" aria-label="ใช้กับเหล็ก">${matOpts(r.material)}</select></td><td><button type="button" class="btn small ghost" data-rm="${i}" aria-label="ลบแถว">ลบ</button></td></tr>`).join("")}
    </tbody></table>
    <button type="button" class="btn small" id="pfAdd">เพิ่มแถว</button>
    <h3 class="dlg-h3">ชนิดเหล็ก</h3>
    <p class="hint">เพิ่มเหล็กชนิดใหม่ได้ที่นี่ รหัสใช้อ้างอิงในตารางค่ายืดและตารางราคา คำนำหน้าใช้ตั้งชื่อไฟล์ DXF เช่น ZN#22</p>
    <table class="pt"><thead><tr><th>รหัส</th><th>ชื่อ</th><th>คำนำหน้าชื่อไฟล์</th><th></th></tr></thead><tbody id="pfMats">
      ${d.materials.map((m, i) => `<tr><td><input type="text" data-m="${i}" data-f="id" value="${esc(m.id)}" aria-label="รหัส"></td><td><input type="text" data-m="${i}" data-f="name" value="${esc(m.name)}" aria-label="ชื่อ"></td><td><input type="text" data-m="${i}" data-f="prefix" value="${esc(m.prefix)}" aria-label="คำนำหน้า"></td><td><button type="button" class="btn small ghost" data-mrm="${i}" ${d.materials.length < 2 ? "disabled" : ""} aria-label="ลบชนิดเหล็ก">ลบ</button></td></tr>`).join("")}
    </tbody></table>
    <button type="button" class="btn small" id="pfMatAdd">เพิ่มชนิดเหล็ก</button>
    <details class="dlg-more"><summary>ตั้งค่าขั้นสูง</summary><div class="grid2">
      <label>มุมที่ถือว่าเป็น 90° (± องศา)<input type="number" step="0.1" id="pfAng" value="${p.angle_tol}"></label>
      <label>ใช้ตารางได้เมื่อ R ใน ≤ (× ความหนา)<input type="number" step="0.5" id="pfRat" value="${p.table_max_ratio}"></label>
      <label>จับคู่ความหนากับตารางได้ ± (มม.)<input type="number" step="0.01" id="pfMatch" value="${p.match_tol}"></label>
      <label class="switch" style="align-self:end"><input type="checkbox" id="pfJog" ${p.jog_use_k ? "checked" : ""}> รอยพับสลับทิศติดกัน (jog) ใช้ K-factor</label>
      <label class="switch" style="grid-column:1/3"><input type="checkbox" id="pfSimp" ${p.simplify_bends !== false ? "checked" : ""}> เส้นตัดโค้งที่พาดผ่านรอยพับ ทำเป็นเส้นตรง (เหมือน Simplify bends ของ SolidWorks)</label></div></details>`;
  const body = $("#profileBody");
  $("#pfSel").onchange = (e) => { syncProfile(); d.cur = e.target.value; renderProfileDialog(); };
  $("#pfAdd").onclick = () => { syncProfile(); p.table.push({ gauge: "", thickness: 0, bd: 0, material: "" }); renderProfileDialog(); const ins = $("#pfRows").querySelectorAll("tr:last-child input"); if (ins[0]) ins[0].focus(); };
  $("#pfDup").onclick = () => { syncProfile(); const n = JSON.parse(JSON.stringify(p)); n.id = "m" + Date.now().toString(36); n.name = p.name + " (สำเนา)"; d.profiles.push(n); d.cur = n.id; renderProfileDialog(); };
  $("#pfDel").onclick = () => { d.profiles = d.profiles.filter((x) => x.id !== p.id); d.cur = d.profiles[0].id; if (d.active === p.id) d.active = d.cur; renderProfileDialog(); };
  $("#pfMatAdd").onclick = () => { syncProfile(); d.materials.push({ id: "", name: "", prefix: "" }); renderProfileDialog(); const ins = $("#pfMats").querySelectorAll("tr:last-child input"); if (ins[0]) ins[0].focus(); };
  body.querySelectorAll("[data-rm]").forEach((b) => (b.onclick = () => { syncProfile(); p.table.splice(+b.dataset.rm, 1); renderProfileDialog(); }));
  body.querySelectorAll("[data-mrm]").forEach((b) => (b.onclick = () => { syncProfile(); d.materials.splice(+b.dataset.mrm, 1); renderProfileDialog(); }));
}
function syncProfile() {
  const d = editing, p = d.profiles.find((x) => x.id === d.cur); if (!p) return;
  p.name = $("#pfName").value.trim() || p.name; p.k_factor = +$("#pfK").value || 0.5;
  $("#pfRows").querySelectorAll("input,select").forEach((i) => { const r = p.table[+i.dataset.r]; const f = i.dataset.f; r[f] = f === "gauge" || f === "material" ? i.value.trim() : +i.value; });
  p.angle_tol = +$("#pfAng").value || 1; p.table_max_ratio = +$("#pfRat").value || 3; p.match_tol = +$("#pfMatch").value || 0.06; p.jog_use_k = $("#pfJog").checked; p.simplify_bends = $("#pfSimp").checked;
  $("#pfMats").querySelectorAll("[data-m]").forEach((i) => { d.materials[+i.dataset.m][i.dataset.f] = i.value.trim(); });
  d.materials.forEach((m) => { if (m.id && !m.prefix) m.prefix = m.id; if (m.id && !m.name) m.name = m.id; });
}
async function saveProfiles(ev) {
  ev.preventDefault();
  try {
    syncProfile();
    const d = editing; d.active = d.cur;
    const ids = d.materials.map((m) => m.id).filter(Boolean);
    if (new Set(ids).size !== ids.length) throw new Error("รหัสชนิดเหล็กซ้ำกัน");
    const out = { ...d }; delete out.cur; delete out.fresh;
    out.materials = d.materials.filter((m) => m.id);
    S.profilesData = await sendJSON("/api/profiles", "PUT", out);
    $("#profileDlg").close();
    fillProfileSelects(); updateLandHint();
    if (!S.jobId) {
      const box = $("#matRadios"), cur = chosenMaterial(); box.innerHTML = "";
      S.profilesData.materials.forEach((m, i) => box.insertAdjacentHTML("beforeend", `<label class="radio"><input type="radio" name="mat" value="${esc(m.id)}" ${(m.id === cur || (i === 0 && !S.profilesData.materials.some((x) => x.id === cur))) ? "checked" : ""}><span>${esc(m.name)}</span></label>`));
    }
    if (S.jobId) { await patchSettings({ profile: S.profilesData.active }); $("#profileSel").value = S.profilesData.active; }
    toast(S.jobId ? "บันทึกตารางค่ายืดแล้ว คำนวณแผ่นคลี่ใหม่ทั้งหมด" : "บันทึกโหมดค่ายืดไว้ในเครื่องนี้แล้ว");
  } catch (e) { $("#profileMsg").textContent = e.message; }
}

/* ======================================================================
   Pricing (คิดราคา) - same formulas as the shop's สูตรคิดราคา workbook
   ====================================================================== */
const baht = (n, d = 0) => fmt(n, d);
const UNITS = [["piece", "ต่อชิ้น"], ["bend", "ต่อจุดพับ"], ["hole", "ต่อรู"], ["kg", "ต่อ กก."], ["m2", "ต่อ ตร.ม."], ["job", "ต่องาน (ครั้งเดียว)"]];
async function openPricing() {
  if (!S.jobId) return;
  $("#priceMsg").textContent = "";
  try { S.price = await getJSON(`/api/jobs/${S.jobId}/pricing`); } catch (e) { toast(e.message); return; }
  if (!S.price.meta.date) S.price.meta.date = new Date().toISOString().slice(0, 10);
  S.priceAll = false;
  renderPriceForm(); renderPriceResult();
  $("#priceDlg").showModal();
}
/* price of a 1220 x 2440 sheet at this row's thickness - the workbook's ROUNDUP(weight x price) x thickness */
const platePrice = (r) => Math.ceil(+(r.kg_sheet * r.price_kg).toFixed(9)) * r.thickness;
function priceCode(r) { return r.gauge ? matPrefix(r.material) + r.gauge : `${matPrefix(r.material)}#${fmtG(r.thickness)}mm`; }
function renderPriceForm() {
  const P = S.price.pricing, M = S.price.meta;
  const dis = DEMO ? "disabled" : "";
  const used = new Set(S.price.groups.map((g) => g.material_id + "|" + g.thickness));
  const isUsed = (r) => [...used].some((k) => { const [m, t] = k.split("|"); return m === r.material && Math.abs(+t - r.thickness) < 0.061; });
  const shown = P.prices.map((r, i) => ({ r, i })).filter(({ r }) => S.priceAll || isUsed(r));
  let lastMat = null;
  const priceRows = shown.map(({ r, i }) => {
    const head = r.material !== lastMat ? `<tr class="mat-row"><td colspan="5">${esc(matName(r.material))}</td></tr>` : "";
    lastMat = r.material;
    return head + `<tr class="${isUsed(r) ? "used" : ""}"><td><b>${esc(priceCode(r))}</b></td><td class="n">${fmtG(r.thickness)}</td>
      <td><input type="number" step="0.5" min="0" data-p="${i}" data-f="price_kg" value="${r.price_kg}" aria-label="ราคาต่อกิโลกรัม ${esc(priceCode(r))}" ${dis}></td>
      <td><input type="number" step="0.1" min="0" data-p="${i}" data-f="kg_plate" value="${+(r.kg_sheet * r.thickness).toFixed(2)}" aria-label="น้ำหนักแผ่น 4×8 ${esc(priceCode(r))}" ${dis}></td>
      <td class="n plate-price" data-pp="${i}">${baht(platePrice(r))}</td></tr>`;
  }).join("");
  $("#priceBody").innerHTML = `
  <div class="price-grid">
    <div class="price-form">
      <h3 class="dlg-h3">รายละเอียดงาน</h3>
      <div class="grid2">
        <label>วันที่<input type="date" id="prDate" value="${esc(M.date)}" ${dis}></label>
        <label>ลูกค้า<input type="text" id="prCust" value="${esc(M.customer)}" placeholder="ชื่อลูกค้า / บริษัท" ${dis}></label>
        <label style="grid-column:1/3">หมายเหตุ<input type="text" id="prNote" value="${esc(M.note)}" ${dis}></label>
      </div>
      <h3 class="dlg-h3">ราคาเหล็ก</h3>
      <p class="hint">แยกตามชนิดเหล็กและความหนาในตารางค่ายืด · น้ำหนักและราคาเป็นของแผ่น 4×8 ฟุต (1220×2440) ที่ความหนาของแถวนั้น</p>
      <label class="switch"><input type="checkbox" id="prAll" ${S.priceAll ? "checked" : ""}> แสดงทุกความหนา (ปกติแสดงเฉพาะที่ใช้ในงานนี้)</label>
      <table class="pt price-pt"><thead><tr><th>วัตถุดิบ</th><th class="n">หนา (มม.)</th><th>บาท / กก.</th><th>น้ำหนักแผ่น 4×8 (กก.)</th><th class="n">ราคาแผ่น 4×8</th></tr></thead><tbody>${priceRows}</tbody></table>
      <h3 class="dlg-h3">ขนาดแผ่นเหล็กที่มี</h3>
      <p class="hint">โปรแกรมลองวางทุกขนาดที่ติ๊กไว้ ทั้งเรียงแบบ 1 และแบบ 2 (หมุน 90°) แล้วเลือกแบบที่ค่าโลหะต่อชิ้นถูกที่สุด</p>
      <div class="sheet-list">${P.sheets.map((s, i) => `<span class="sheet-chip"><label class="switch"><input type="checkbox" data-s="${i}" data-f="on" ${s.on ? "checked" : ""} aria-label="ใช้แผ่น ${s.w}x${s.l}" ${dis}></label>
          <input type="number" min="1" data-s="${i}" data-f="w" value="${s.w}" aria-label="กว้าง" ${dis}><span>×</span><input type="number" min="1" data-s="${i}" data-f="l" value="${s.l}" aria-label="ยาว" ${dis}>
          <button type="button" class="btn small ghost" data-srm="${i}" aria-label="ลบขนาดนี้" ${dis}>ลบ</button></span>`).join("")}
        <button type="button" class="btn small" id="prSheetAdd" ${dis}>เพิ่มขนาดแผ่น</button></div>
      <div class="grid2" style="margin-top:10px">
        <label>เผื่อที่ clamp & วางแบบ (มม./ชิ้น)<input type="number" step="1" min="0" id="prMargin" value="${P.margin}" ${dis}></label>
      </div>
      <h3 class="dlg-h3">ค่าสี ค่าชุบ</h3>
      <div class="grid2">
        <label>ค่าสี (บาท / ตร.ม.)<input type="number" step="10" min="0" id="prPaint" value="${P.paint_per_m2}" ${dis}></label>
        <label>ไม่ทาสีกับเหล็ก<input type="text" id="prNoPaint" value="${esc(P.no_paint.join(", "))}" placeholder="เช่น SUS" ${dis}></label>
        <label>ค่าชุบ (บาท / กก.)<input type="number" step="1" min="0" id="prPlate" value="${P.plating_per_kg}" ${dis}></label>
        <label class="switch" style="align-self:end"><input type="checkbox" id="prIncPlate" ${P.include_plating ? "checked" : ""} ${dis}> รวมค่าชุบในราคารวม</label>
      </div>
      <h3 class="dlg-h3">ค่าใช้จ่ายอื่น</h3>
      <p class="hint">เพิ่มรายการเองได้ เช่น ค่าพับ ค่าติ๊ก เจาะรู พ่นทราย ค่าขนส่ง · โปรแกรมนับจุดพับและรูให้เอง</p>
      <table class="pt extra-pt"><thead><tr><th>ใช้</th><th>รายการ</th><th>บาท</th><th>คิด</th><th></th></tr></thead><tbody>
        ${P.extras.map((e, i) => `<tr><td><input type="checkbox" data-e="${i}" data-f="on" ${e.on ? "checked" : ""} aria-label="ใช้ ${esc(e.name)}" ${dis}></td>
          <td><input type="text" data-e="${i}" data-f="name" value="${esc(e.name)}" aria-label="ชื่อรายการ" ${dis}></td>
          <td><input type="number" step="0.5" min="0" data-e="${i}" data-f="rate" value="${e.rate}" aria-label="ราคา ${esc(e.name)}" ${dis}></td>
          <td><select data-e="${i}" data-f="unit" aria-label="คิดต่อ" ${dis}>${UNITS.map(([v, l]) => `<option value="${v}" ${e.unit === v ? "selected" : ""}>${l}</option>`).join("")}</select></td>
          <td><button type="button" class="btn small ghost" data-erm="${i}" ${dis}>ลบ</button></td></tr>`).join("")}
      </tbody></table>
      <button type="button" class="btn small" id="prExtraAdd" ${dis}>เพิ่มรายการ</button>
      <div class="grid2" style="margin-top:14px">
        <label>บวกเพิ่ม (%)<input type="number" step="1" min="0" id="prMarkup" value="${P.markup_pct}" ${dis}></label>
      </div>
      ${DEMO ? `<p class="hint">${DEMO_MSG}</p>` : ""}
    </div>
    <div class="price-result" id="priceResult" aria-live="polite"></div>
  </div>`;
  const body = $("#priceBody");
  body.querySelectorAll("[data-p]").forEach((i) => i.addEventListener("input", syncPrice));
  body.querySelectorAll("input,select").forEach((i) => i.id !== "prAll" && i.addEventListener(i.type === "checkbox" || i.tagName === "SELECT" ? "change" : "input", schedulePrice));
  $("#prAll").onchange = (e) => { syncPrice(); S.priceAll = e.target.checked; renderPriceForm(); renderPriceResult(); };
  body.querySelectorAll("[data-srm]").forEach((b) => (b.onclick = () => { syncPrice(); if (S.price.pricing.sheets.length < 2) return; S.price.pricing.sheets.splice(+b.dataset.srm, 1); renderPriceForm(); renderPriceResult(); pushPrice(); }));
  body.querySelectorAll("[data-erm]").forEach((b) => (b.onclick = () => { syncPrice(); S.price.pricing.extras.splice(+b.dataset.erm, 1); renderPriceForm(); renderPriceResult(); pushPrice(); }));
  $("#prSheetAdd").onclick = () => { syncPrice(); S.price.pricing.sheets.push({ w: 1220, l: 2440, on: true }); renderPriceForm(); };
  $("#prExtraAdd").onclick = () => { syncPrice(); S.price.pricing.extras.push({ name: "", rate: 0, unit: "piece", on: true }); renderPriceForm(); const r = body.querySelectorAll(".extra-pt tbody tr"); r[r.length - 1].querySelector("input[type=text]").focus(); };
}
function syncPrice() {
  const P = S.price.pricing, M = S.price.meta, body = $("#priceBody");
  M.date = $("#prDate").value; M.customer = $("#prCust").value; M.note = $("#prNote").value;
  body.querySelectorAll("[data-p]").forEach((i) => {
    const r = P.prices[+i.dataset.p], v = +i.value || 0;
    if (i.dataset.f === "kg_plate") r.kg_sheet = r.thickness ? v / r.thickness : 0;   // stored per 1 mm, like the workbook
    else r[i.dataset.f] = v;
    const pp = body.querySelector(`[data-pp="${i.dataset.p}"]`); if (pp) pp.textContent = baht(platePrice(r));
  });
  body.querySelectorAll("[data-s]").forEach((i) => { const s = P.sheets[+i.dataset.s]; s[i.dataset.f] = i.dataset.f === "on" ? i.checked : +i.value || 0; });
  body.querySelectorAll("[data-e]").forEach((i) => { const e = P.extras[+i.dataset.e], f = i.dataset.f; e[f] = f === "on" ? i.checked : f === "rate" ? +i.value || 0 : i.value; });
  P.margin = +$("#prMargin").value || 0; P.paint_per_m2 = +$("#prPaint").value || 0;
  P.plating_per_kg = +$("#prPlate").value || 0; P.markup_pct = +$("#prMarkup").value || 0;
  P.include_plating = $("#prIncPlate").checked;
  P.no_paint = $("#prNoPaint").value.split(/[,\s]+/).map((x) => x.trim()).filter(Boolean);
}
function schedulePrice() { clearTimeout(schedulePrice._t); schedulePrice._t = setTimeout(pushPrice, 350); }
async function pushPrice(saveDefault) {
  if (DEMO) return;
  syncPrice();
  const P = S.price.pricing;
  const out = { ...P, sheets: P.sheets.filter((s) => s.w > 0 && s.l > 0), extras: P.extras.filter((e) => e.name.trim()) };
  try {
    const r = await sendJSON(`/api/jobs/${S.jobId}/pricing`, "PUT", { pricing: out, meta: S.price.meta, save_default: !!saveDefault });
    const keepSheets = S.price.pricing.sheets, keepExtras = S.price.pricing.extras;
    S.price = r; S.price.pricing.sheets = keepSheets; S.price.pricing.extras = keepExtras;   // keep half-typed rows in the form
    $("#priceMsg").textContent = saveDefault ? "ตั้งเป็นค่าเริ่มต้นของงานถัดไปแล้ว (บันทึกในเครื่องนี้)" : "";
    renderPriceResult();
    return true;
  } catch (e) { $("#priceMsg").textContent = e.message; return false; }
}
function renderPriceResult() {
  const R = S.price, box = $("#priceResult"); if (!box) return;
  const g = R.groups.map((x) => `<tr><td><b>${esc(x.name || x.code)}</b>${x.name && x.name !== x.code ? `<small>${esc(x.code)}</small>` : ""}</td><td class="n">${x.pieces}</td><td class="n">${fmt(x.weight, 2)}</td><td class="n">${baht(x.metal)}</td><td class="n">${baht(x.paint)}</td><td class="n">${baht(x.extra, 2)}</td><td class="n"><b>${baht(x.total, 2)}</b></td></tr>
    <tr class="sub"><td colspan="7">ใช้แผ่น ${Object.entries(x.sheets).map(([k, v]) => `${k.replace("x", " × ")} ประมาณ ${v} แผ่น`).join(", ") || "-"}</td></tr>`).join("") +
    R.job_extras.map((e) => `<tr><td colspan="6">${esc(e.name)} <small>(ต่องาน)</small></td><td class="n"><b>${baht(e.amount, 2)}</b></td></tr>`).join("");
  const rows = R.rows.map((r) => r.error ? `<tr class="err"><td>${esc(r.name)}</td><td colspan="6">${esc(r.error)}</td></tr>` :
    `<tr><td>${esc(r.name)}<small>${esc(r.code)} · ${fmt(r.x, 1)} × ${fmt(r.y, 1)} มม.</small></td>
      <td>${r.sheet.w}×${r.sheet.l}<small>เรียงแบบ ${r.sheet.arr}${r.sheet.arr === 2 ? " (หมุน 90°)" : ""} · ${r.sheet.nx}×${r.sheet.ny} = ${r.sheet.count} ชิ้น</small></td>
      <td class="n">${baht(r.metal)}</td><td class="n">${baht(r.paint)}</td><td class="n">${baht(r.extra, r.extra % 1 ? 2 : 0)}</td><td class="n">${r.qty}</td><td class="n"><b>${baht(r.total, 2)}</b></td></tr>`).join("");
  box.innerHTML = `
    <div class="price-total"><span>ราคารวม${R.markup ? " (รวมบวกเพิ่ม)" : ""}</span><b>${baht(R.total, 2)}</b><small>บาท · เหล็ก ${fmt(R.weight, 2)} กก.${R.markup ? ` · บวกเพิ่ม ${baht(R.markup, 2)}` : ""}</small></div>
    ${R.errors.length ? `<p class="err">${R.errors.length} ชิ้นคิดราคาไม่ได้ ดูแถวสีแดง</p>` : ""}
    <table class="otable ptab"><thead><tr><th>วัตถุดิบ</th><th class="n">ชิ้น</th><th class="n">กก.</th><th class="n">โลหะ</th><th class="n">สี</th><th class="n">อื่นๆ</th><th class="n">รวม</th></tr></thead><tbody>${g}</tbody></table>
    <h3 class="dlg-h3">รายชิ้น (ราคาต่อชิ้น)</h3>
    <table class="otable ptab"><thead><tr><th>ชิ้นส่วน</th><th>แผ่นที่ใช้ · การวาง</th><th class="n">โลหะ</th><th class="n">สี</th><th class="n">อื่นๆ</th><th class="n">จำนวน</th><th class="n">รวม</th></tr></thead><tbody>${rows}</tbody></table>`;
}
async function priceXlsx() {
  if (DEMO) { download("pricing"); return; }
  clearTimeout(schedulePrice._t);
  if (await pushPrice()) location.href = `/api/jobs/${S.jobId}/export/pricing`;
}

/* ---- save the whole job as one file ---- */
function saveJob() {
  if (DEMO) { toast(DEMO_MSG); return; }
  if (!S.project.can_save) { toast("งานนี้บันทึกไม่ได้ (ไม่มีไฟล์ STEP ต้นฉบับในเครื่อง)", 4000); return; }
  location.href = `/api/jobs/${S.jobId}/save`;
  toast("กำลังบันทึก " + S.project.job + ".sheetflat · เปิดต่อได้ด้วยปุ่ม “เปิดงานที่บันทึกไว้” หรือส่งไฟล์ให้คนอื่นเปิดได้เลย", 5000);
}

/* ---- colour mode: auto (follows Windows) / light / dark ---- */
const THEMES = [["auto", "อัตโนมัติ"], ["light", "สว่าง"], ["dark", "มืด"]];
function applyTheme(t) {
  const root = document.documentElement;
  if (t === "auto") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", t);
  $("#themeLabel").textContent = THEMES.find((x) => x[0] === t)[1];
  $("#btnTheme").setAttribute("aria-label", "สีหน้าจอ: " + THEMES.find((x) => x[0] === t)[1] + " (กดเพื่อเปลี่ยน)");
  if (viewer) { viewer.colors(); viewer.draw(); }
}
function initTheme() {
  let t = "auto";
  try { t = localStorage.getItem("sf-theme") || "auto"; } catch (_) { /* private mode */ }
  if (!THEMES.some((x) => x[0] === t)) t = "auto";
  applyTheme(t);
  $("#btnTheme").onclick = () => {
    const cur = document.documentElement.getAttribute("data-theme") || "auto";
    const next = THEMES[(THEMES.findIndex((x) => x[0] === cur) + 1) % THEMES.length][0];
    try { localStorage.setItem("sf-theme", next); } catch (_) { /* ignore */ }
    applyTheme(next);
  };
}

/* ======================================================================
   boot
   ====================================================================== */
window.addEventListener("DOMContentLoaded", async () => {
  viewer = new Viewer($("#cv"));
  initTheme();
  $("#btnSave").onclick = saveJob;
  $("#btnPrice").onclick = openPricing;
  $("#priceXlsx").onclick = priceXlsx;
  $("#priceDefault").onclick = () => { if (DEMO) { toast(DEMO_MSG); return; } clearTimeout(schedulePrice._t); pushPrice(true); };
  $("#priceForm").addEventListener("submit", (e) => e.preventDefault());
  $("#tFit").onclick = () => viewer.fit();
  $("#tBend").onclick = () => toggleOpt("bend"); $("#tDim").onclick = () => toggleOpt("dim");
  $("#tName").onclick = () => toggleOpt("name"); $("#tMeasure").onclick = () => toggleOpt("measure");
  $("#btnAll").onclick = () => download("all");
  $("#btnXlsx").onclick = () => download("xlsx");
  $("#btnProfile").onclick = openProfileDialog;
  $("#btnNew").onclick = () => { S.jobId = null; S.project = null; S.sel = null; S.cache.clear(); S.editGroups = false; S.picked.clear(); S.price = null; $("#topJob").hidden = true; $("#topActions").hidden = true; $("#file").value = ""; backToLanding(""); };
  $("#profileForm").addEventListener("submit", (e) => { if (e.submitter && e.submitter.id === "profileSave") saveProfiles(e); });
  $("#profileSel").addEventListener("change", (e) => patchSettings({ profile: e.target.value }));
  if (DEMO) {
    document.body.classList.add("demo");
    window.addEventListener("hashchange", route);
    route();
    try { S.demoFiles = await (await fetch(DEMO + "files.json")).json(); } catch (_) { S.demoFiles = {}; }
    const banner = document.getElementById("demoBanner"); if (banner) banner.hidden = false;
    const go = document.getElementById("demoGo");
    if (go) go.addEventListener("click", async () => { showBusy("กำลังเปิดงานตัวอย่าง"); try { await pollJob("demo"); } catch (e) { backToLanding(e.message); } });
  }
  if (!DEMO) {
    try {
      const info = await (await fetch("/api/info")).json();
      if (info.cloud) { document.querySelector(".pathbox").hidden = true; document.body.classList.add("cloud"); }
    } catch (_) { /* ignore */ }
  }
  try { await initLanding(); } catch (e) { landError("เชื่อมต่อเซิร์ฟเวอร์ไม่ได้: " + e.message); }
});
