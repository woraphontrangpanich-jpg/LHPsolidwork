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
  sheetIdx: 0, version: 0, cache: new Map(), meshCache: new Map(), openGroups: new Set(),
  closedMats: new Set(), closedPlates: new Set(),   // folded sections of the DXF list
  editGroups: false, picked: new Set(),             // overview "edit groups" mode
  view3d: false,                                    // part view: 2D flat pattern or 3D folded part
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
  if ((m = path.match(/\/parts\/([^/]+)\/mesh$/))) return DEMO + `mesh/${m[1]}.json`;
  if ((m = path.match(/\/parts\/([^/]+)\/fold$/))) return DEMO + `fold/${m[1]}.json`;
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
/* the language of the page (th | en): files we hand out (Excel, ZIP folders, file names) follow it */
const lang = () => window.SF_LANG || "th";
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
  $("#loadJob").addEventListener("click", () => { $("#file").accept = ".sheetflat"; $("#file").click(); $("#file").accept = ".step,.stp,.sldasm,.sldprt,.dxf,.sheetflat"; });
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
  syncProfileMenu();
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
  if (!isJob) { fd.append("material", chosenMaterial()); fd.append("profile", $("#landProfile").value); fd.append("split_fused", $("#splitFused").checked ? "1" : "0"); fd.append("dxf_thickness", $("#dxfT").value || "1.2"); }
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
    const r = await sendJSON("/api/open-path", "POST", { path: p, material: chosenMaterial(), profile: $("#landProfile").value, split_fused: $("#splitFused").checked, dxf_thickness: +$("#dxfT").value || 1.2 });
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
  fillProfileSelects(); $("#profileSel").value = S.project.settings.profile || S.profilesData.active; syncProfileMenu();
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
    const when = new Date(rel.published_at || a.updated_at || a.created_at);
    if (!Number.isNaN(when.getTime())) {
      const th = when.toLocaleString(lang() === "en" ? "en-GB" : "th-TH", { dateStyle: "long", timeStyle: "short", timeZone: "Asia/Bangkok" });
      $("#dlDate").textContent = `อัปเดตล่าสุด ${th} น. (เวลาประเทศไทย)`;
      $("#dlDate").dateTime = when.toISOString();
      $("#dlDate").hidden = false;
    }
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
  <div class="sec"><div class="dl-btns"><button class="btn primary" data-dl="all">ดาวน์โหลด DXF ทั้งหมด + Excel (.zip)</button></div>
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
        <td class="n">${fmt(f.w)} × ${fmt(f.h)}</td><td class="n">${f.bends.length}</td><td class="n">${f.holes}</td><td class="qty" ${p.qty !== p.qty_asm ? `title="ใน Assembly ${p.qty_asm} ชิ้น"` : ""}>${p.qty}${p.qty_override ? '<small class="qmark">แก้แล้ว</small>' : ""}</td></tr>`;
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
  box.innerHTML = `<article class="order"><header class="order-head"><div><h2>ใบสั่งตัด ${esc(P.job)}</h2><p>${esc(P.source)} · ${new Date().toLocaleDateString(lang() === "en" ? "en-GB" : "th-TH", { day: "numeric", month: "short", year: "numeric" })} · ${esc(P.profile.name)}</p></div>
    <div class="totals"><div><b>${nfiles}</b><span>ไฟล์ DXF</span></div><div><b>${s.cut_pieces}</b><span>ชิ้นที่ตัด</span></div><div><b>${s.excluded_pieces}</b><span>ไม่ตัด</span></div></div></header>
    <div class="edit-row">${ed ? "" : `<button class="btn small" id="ovEdit">แก้ไขกลุ่ม</button><span class="hint">ย้ายชิ้นไปเหล็กชนิดหรือความหนาอื่น หรือแยกเป็นกลุ่มที่ไม่ต้องตัด เช่น ซื้อสำเร็จรูป</span>`}
      <label class="sets-box">จำนวนชุดที่ผลิต<input type="number" id="ovSets" min="1" step="1" value="${P.sets || 1}" aria-label="จำนวนชุดที่ผลิต"></label></div>
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
  const st = $("#ovSets");
  if (st) st.addEventListener("change", () => { const v = Math.round(+st.value); if (DEMO) { toast(DEMO_MSG); return; } if (v >= 1) patchSettings({ sets: v }); else toast("จำนวนชุดต้องมากกว่า 0"); });
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
  ${acc("layout", "การวางชิ้นในไฟล์", `${st.copies === "qty" ? "ตามจำนวนจริง" : "1 ชิ้นต่อแบบ"} · ${cur ? cur.replace("x", " × ") : "ไม่จำกัดแผ่น"}`)}
    <div class="row"><label for="sCopies">จำนวนที่ใส่ในไฟล์</label><select id="sCopies"><option value="qty" ${st.copies === "qty" ? "selected" : ""}>ตามจำนวนจริง</option><option value="one" ${st.copies === "one" ? "selected" : ""}>1 ชิ้นต่อแบบ</option></select></div>
    <div class="row"><label for="sSheet">ขนาดแผ่นเหล็ก</label><select id="sSheet">${sheetOpts.map(([v, l]) => `<option value="${v}" ${v === cur ? "selected" : ""}>${l}</option>`).join("")}</select></div>
    <div class="row"><label for="sGap">ระยะห่างชิ้น (มม.)</label><input type="number" id="sGap" min="0" step="1" value="${st.gap}"></div>
    <p class="hint">การวางนี้เป็นแค่ให้ชิ้นไม่ซ้อนกัน การจัดวางประหยัดเหล็ก (nesting) ทำต่อในโปรแกรมเครื่องเลเซอร์</p></details>
  ${acc("dxf", "เนื้อหาในไฟล์ DXF", [st.bend_lines ? "เส้นพับ" : "", st.labels ? "ชื่อชิ้น" : ""].filter(Boolean).join(" + ") || "เส้นตัดอย่างเดียว")}
    <label class="switch"><input type="checkbox" id="sBend" ${st.bend_lines ? "checked" : ""}> ใส่เส้นพับ (เลเยอร์ BEND)</label>
    <label class="switch"><input type="checkbox" id="sLabel" ${st.labels ? "checked" : ""}> ใส่ชื่อชิ้น (เลเยอร์ LABEL)</label>
    <p class="hint">เส้นตัดอยู่เลเยอร์ CUT หน่วยมิลลิเมตร ถ้าเครื่องเลเซอร์ตัดทุกเลเยอร์ ให้ปิดเส้นพับและชื่อชิ้น</p></details>
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
  const sets = P.sets || 1;
  let h = `<div class="sec"><h2 class="insp-title">${esc(p.name)}</h2><p class="insp-sub">ใน Assembly ${p.qty_asm} ชิ้น · ผิว ${p.faces} ผิว</p></div>`;
  h += `<div class="sec"><h3>จำนวนที่ผลิต</h3>
    <div class="row"><label for="pQty">จำนวน${sets > 1 ? " ต่อชุด" : ""}</label><input type="number" id="pQty" min="1" step="1" value="${p.qty_override ?? p.qty_asm}">
      ${p.qty_override ? `<button class="btn small ghost" id="pQtyReset" type="button">ใช้ตาม Assembly (${p.qty_asm})</button>` : ""}</div>
    <p class="hint">${sets > 1 ? `ทั้งหมด ${p.qty} ชิ้น (${sets} ชุด) · ` : ""}จำนวนนี้ใช้ทั้งใน DXF และการคิดราคา</p>
    ${f ? `<label class="switch"><input type="checkbox" id="pPaint" ${p.paint ? "checked" : ""}> ทาสี</label>
    <label class="switch"><input type="checkbox" id="pPlate" ${p.plate ? "checked" : ""}> ชุบ</label>` : ""}</div>`;
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
    const matName = (P.materials.find((m) => m.id === p.material_id) || {}).name || p.material_id;
    h += `${acc("mat", "วัสดุและความหนา", `${esc(matName)} · ${fmtG(t.used)} มม.`)}
      <div class="row"><label for="pMat">ชนิดเหล็ก</label><select id="pMat">${mats}</select></div>
      <dl class="kv"><dt>ความหนาที่วัดได้</dt><dd>${fmt(t.measured, 3)} มม.</dd>
      <dt>ความหนาที่ใช้คำนวณ</dt><dd>${fmtG(t.used)} มม.</dd>
      <dt>เบอร์ / ค่ายืด 90°</dt><dd>${t.matched ? `${esc(t.gauge)} / ${fmt(t.bd, 2)} มม.` : "ไม่อยู่ในตาราง"}</dd>
      <dt>ชื่อไฟล์กลุ่ม</dt><dd>${esc(p.code)}</dd></dl>
      <div class="row"><label for="pT">ระบุความหนาเอง (มม.)</label><input type="number" id="pT" step="0.01" min="0" placeholder="${fmtG(t.used)}" value="${t.override ?? ""}"></div></details>`;
  }
  if (f) {
    const checks = [];
    const dev = c.volume_dev_pct;
    if (dev !== null && dev !== undefined) {
      const k = Math.abs(dev) <= 0.5 ? "ok" : Math.abs(dev) <= 1.5 ? "warn" : "bad";
      checks.push([k, `พื้นที่คลี่ × ความหนา ตรงกับปริมาตรชิ้นงาน (${dev >= 0 ? "+" : ""}${fmt(dev, 3)}%)`, "คำนวณแยกอิสระด้วย K = 0.5 เทียบกับปริมาตรจากโมเดล 3D"]);
    }
    checks.push([c.closed ? "ok" : "bad", c.closed ? "เส้นตัดทุกเส้นปิดสนิท" : "มีเส้นตัดไม่ปิด"]);
    checks.push([(c.thickness_weight || 0) > 0.8 ? "ok" : "warn", `ความหนา ${fmt(t.measured, 3)} มม. ยืนยันจากผิวคู่ขนาน ${fmt((c.thickness_weight || 0) * 100, 0)}% ของพื้นที่ระนาบ`]);
    checks.push([t.matched ? "ok" : "warn", t.matched ? `ความหนาตรงกับตาราง (${esc(t.gauge)})` : "ความหนาไม่อยู่ในตารางค่ายืด ใช้ K-factor กับทุกรอยพับ"]);
    const nBad = checks.filter((x) => x[0] === "bad").length, nWarn = checks.filter((x) => x[0] === "warn").length;
    const cMeta = nBad ? `ไม่ผ่าน ${nBad} ข้อ` : nWarn ? `ควรดู ${nWarn} ข้อ` : `ผ่านทั้ง ${checks.length} ข้อ`;
    h += acc("check", "ตรวจสอบความถูกต้อง", cMeta, nBad ? "bad" : nWarn ? "warn" : "", !!(nBad || nWarn));
    h += checks.map((x) => chk(...x)).join("");
    h += `</details>`;
    h += acc("bends", "รอยพับ", f.bends.length ? `${f.bends.length} จุดพับ` : "ไม่มี");
    if (f.bends.length) {
      h += `<table class="bt"><thead><tr><th>#</th><th class="n">มุมใน</th><th class="n">R ใน</th><th>วิธี</th><th class="n">BA</th></tr></thead><tbody>` +
        f.bends.map((b, i) => `<tr data-bend="${i}"><td>${i + 1}</td><td class="n">${fmt(b.angle_in, 1)}°</td><td class="n">${fmt(b.r, 2)}</td><td><span class="tag ${b.method === "table" ? "tbl" : ""}">${b.method === "table" ? "ตาราง BD " + fmt(b.bd, 2) : "K " + b.k}</span></td><td class="n">${fmt(b.ba, 2)}</td></tr>`).join("") +
        `</tbody></table><p class="hint">มุม = มุมด้านในหลังพับ วัดแบบเครื่องพับ (พับฉาก = 90°, พับทบ = 0°) · BA = ความกว้างแถบพับในแผ่นคลี่ · ชี้เมาส์ที่แถวเพื่อดูเส้นพับในแบบ</p>`;
    } else h += `<p class="insp-sub">ชิ้นนี้เป็นแผ่นเรียบ ไม่มีรอยพับ</p>`;
    h += `</details>`;
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
  const q = $("#pQty"); if (q) q.addEventListener("change", () => { const v = Math.round(+q.value); if (v >= 1) patchPart(p.id, { qty: v }); else { toast("จำนวนต้องมากกว่า 0"); renderInspector(); } });
  const qr = $("#pQtyReset"); if (qr) qr.addEventListener("click", () => patchPart(p.id, { qty: null }));
  const pp = $("#pPaint"); if (pp) pp.addEventListener("change", () => patchPart(p.id, { paint: pp.checked }));
  const pl = $("#pPlate"); if (pl) pl.addEventListener("change", () => patchPart(p.id, { plate: pl.checked }));
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
  location.href = `/api/jobs/${S.jobId}/export/${rel}?lang=${lang()}`;
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
    cv.addEventListener("dblclick", (e) => {
      const it = this.itemAt(...this.pos(e));
      if (it && this.scene.mode === "group" && it.part_id) { this.closeCard(); selectPart(it.part_id); }
    });
    // click on a line, hole, bend or face: details in a small card (same look as the 3D card)
    this.card = document.createElement("div");
    this.card.className = "card3d"; this.card.hidden = true;
    cv.parentElement.appendChild(this.card);
    this.card.addEventListener("click", (e) => {
      if (e.target.closest("[data-close]")) this.closeCard();
      const o = e.target.closest("[data-open]"); if (o) { this.closeCard(); selectPart(o.dataset.open); }
    });
    window.addEventListener("keydown", (e) => this.onKey(e));
    this.colors();
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { this.colors(); this.draw(); if (v3) v3.setColors(colors3d()); });
  }
  colors() {
    const cs = getComputedStyle(document.documentElement), g = (n) => cs.getPropertyValue(n).trim();
    this.c = { ink: g("--ink"), bend: g("--bend"), dim: g("--dim"), paper: g("--paper"), zinc: g("--zinc-100"), grid: g("--zinc-200"), fill: g("--part-fill"), sel: g("--sel"), selBg: g("--sel-fill"), steel: g("--steel") };
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
    this.closeCard();
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
    this.closeCard();
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
      if (Math.abs(dx) + Math.abs(dy) > 3) { this.drag.moved = true; this.cv.classList.add("pan"); this.closeCard(); }
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
    this.clickCard(e, px, py);
  }
  /* ---- detail card ---- */
  async clickCard(e, px, py) {
    const h = this.hover, it = this.itemAt(px, py) || (h && this.itemNear(h));
    let info = null;
    if (h) info = this.primCard(h, it);
    else if (it) { const sel = S.sel; info = await this.faceCard(it, px, py); if (S.sel !== sel) return; }
    if (info) this.showCard(e, info); else this.closeCard();
  }
  itemNear(h) { return (this.scene && this.scene.items.find((it) => it.prims.includes(h.info) || it.bends.includes(h.info))) || null; }
  primCard(h, it) {
    const q = h.info, ox = it ? it.x : 0, oy = it ? it.y : 0, p = it && part(it.part_id);
    const t = p && p.thickness.used ? [["ความหนาแผ่น", `${fmt(p.thickness.used)} มม.`]] : [];
    const at = (x, y) => `X ${fmt(x - ox, 1)} · Y ${fmt(y - oy, 1)}`;
    const edges = (x, y) => [["ห่างขอบซ้าย", `${fmt(x - ox)} มม.`], ["ห่างขอบล่าง", `${fmt(y - oy)} มม.`]];
    if (h.kind === "circle") return { title: "รู", rows: [["เส้นผ่านศูนย์กลาง", `Ø ${fmt(q.r * 2)} มม.`], ["รัศมี", `R ${fmt(q.r)} มม.`],
      ["จุดศูนย์กลาง", at(...q.c)], ...edges(...q.c), ...t] };
    if (h.kind === "arc") return { title: "ส่วนโค้ง", rows: [["รัศมี", `R ${fmt(q.r)} มม.`], ["ความยาวตามโค้ง", `${fmt(primLen(q))} มม.`],
      ["จุดศูนย์กลาง", at(...q.c)], ...t] };
    if (h.kind === "line") {
      const a = Math.atan2(q.b[1] - q.a[1], q.b[0] - q.a[0]) * 180 / Math.PI;
      return { title: "เส้นตรง (ขอบตัด)", rows: [["ความยาว", `${fmt(primLen(q))} มม.`], ["มุมของเส้น", `${fmt(((a % 180) + 180) % 180, 1)}°`],
        ["จุดเริ่ม", at(...q.a)], ["จุดปลาย", at(...q.b)], ...t] };
    }
    if (h.kind === "bend") {
      const i = it ? it.bends.indexOf(q) : -1, len = Math.hypot(q.b[0] - q.a[0], q.b[1] - q.a[1]);
      const horiz = Math.abs(q.b[1] - q.a[1]) < Math.abs(q.b[0] - q.a[0]);
      const pos = horiz ? ["ห่างขอบล่าง", `${fmt((q.a[1] + q.b[1]) / 2 - oy)} มม.`] : ["ห่างขอบซ้าย", `${fmt((q.a[0] + q.b[0]) / 2 - ox)} มม.`];
      const rows = [["มุมด้านใน", `${fmt(q.angle_in, q.angle_in % 1 ? 1 : 0)}°`], ["ทิศ", q.dir === "up" ? "พับขึ้น" : "พับลง"]];
      if (q.r !== undefined) rows.push(["รัศมีใน (R)", `${fmt(q.r)} มม.`]);
      if (q.method) rows.push(["วิธีคิด", q.method === "table" ? `ตารางค่ายืด BD ${fmt(q.bd)}` : `K-factor ${q.k}`]);
      if (q.ba !== undefined) rows.push(["ความกว้างแถบพับ (BA)", `${fmt(q.ba)} มม.`]);
      rows.push(["ความยาวรอยพับ", `${fmt(len)} มม.`], pos, ...t);
      return { title: i >= 0 && this.scene.mode === "part" ? `รอยพับ #${i + 1}` : "รอยพับ", rows };
    }
    return { title: "เส้นโค้งอิสระ", rows: [["ความยาว", `${fmt(primLen(q))} มม.`], ...t] };
  }
  /* a click inside a part: the face (flange) under the mouse when the fold model knows it, else the whole flat piece */
  async faceCard(it, px, py) {
    const p = part(it.part_id); if (!p) return null;
    const [wx, wy] = this.s2w(px, py), lx = wx - it.x, ly = wy - it.y;
    if (this.scene.mode === "part" && p.flat) {
      try {
        const key = "fold:" + p.id + ":" + S.version;
        let m = S.cache.get(key);
        if (!m) { m = await getJSON(`/api/jobs/${S.jobId}/parts/${p.id}/fold`); S.cache.set(key, m); }
        const k = this.scene.items.indexOf(it), F = m.pieces[k];
        if (F) {
          const [x0, y0, x1] = F.bbox, rx = p.flip ? x1 - lx : lx + x0, ry = ly + y0;
          const R = F.regions.find((r) => r.polys.some((pl) => inRing(rx, ry, pl.outer) && !pl.holes.some((hh) => inRing(rx, ry, hh))));
          if (R && R.kind === "bend") {
            const b = F.bends[R.bend];
            if (b) return { title: `รอยพับ #${b.n}`, rows: [["มุมด้านใน", `${fmt(b.angle_in, b.angle_in % 1 ? 1 : 0)}°`], ["ทิศ", b.dir === "up" ? "พับขึ้น" : "พับลง"],
              ["รัศมีใน (R)", `${fmt(b.r)} มม.`], ["วิธีคิด", b.method === "table" ? `ตารางค่ายืด BD ${fmt(b.bd)}` : `K-factor ${b.k}`],
              ["ความกว้างแถบพับ (BA)", `${fmt(b.ba)} มม.`], ["ความยาวรอยพับ", `${fmt(b.len)} มม.`], ["ความหนาแผ่น", `${fmt(m.t)} มม.`]] };
          }
          if (R) {
            const holes = R.polys.reduce((n, pl) => n + pl.holes.length, 0);
            const area = R.polys.reduce((a, pl) => a + Math.abs(ringArea(pl.outer)) - pl.holes.reduce((s, hh) => s + Math.abs(ringArea(hh)), 0), 0);
            const joins = F.bends.filter((b) => R.chain[0] === b.id || b.chain.slice(1).join() === R.chain.join());
            return { title: "หน้าแผ่น (ปีก)", rows: [["ขนาดหน้านี้", `${fmt(R.size[0])} × ${fmt(R.size[1])} มม.`], ["พื้นที่หน้านี้", `${fmt(area / 100, 1)} ซม.²`],
              ["รูในหน้านี้", `${holes}`], ["ต่อกับรอยพับ", joins.length ? joins.map((b) => "#" + b.n).join(", ") : "–"], ["ความหนาแผ่น", `${fmt(m.t)} มม.`]] };
          }
        }
      } catch (_) { /* no fold model: show the piece */ }
    }
    const area = it.loops.reduce((a, l) => a + (l.role === "outer" ? 1 : -1) * Math.abs(ringArea(l.pts)), 0);
    const holes = it.loops.filter((l) => l.role !== "outer").length;
    const cut = it.prims.reduce((a, q) => a + primLen(q), 0);
    const rows = [["ขนาดแผ่นคลี่", `${fmt(it.w)} × ${fmt(it.h)} มม.`], ["พื้นที่", `${fmt(area / 100, 1)} ซม.²`], ["รู", `${holes}`],
      ["รอยพับ", `${it.bends.length}`], ["ความยาวตัดรวม", `${fmt(cut, 0)} มม.`]];
    if (p.thickness.used) rows.push(["ความหนาแผ่น", `${fmt(p.thickness.used)} มม.`]);
    rows.push(["กลุ่ม", p.code || "–"], ["จำนวน", `${p.qty}`]);
    return { title: it.name + (it.copy > 1 ? " #" + it.copy : ""), rows, open: this.scene.mode === "group" ? p.id : null };
  }
  showCard(e, info) {
    const r = this.cv.getBoundingClientRect();
    this.card.innerHTML = `<header><b>${esc(info.title)}</b><button type="button" data-close aria-label="ปิด">×</button></header>
      <dl>${info.rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
      ${info.open ? `<footer><button type="button" class="btn small" data-open="${esc(info.open)}">เปิดชิ้นนี้</button></footer>` : ""}`;
    this.card.hidden = false;
    const cw = this.card.offsetWidth, ch = this.card.offsetHeight;
    let x = e.clientX - r.left + 14, y = e.clientY - r.top + 14;
    if (x + cw > r.width - 8) x = Math.max(8, e.clientX - r.left - cw - 14);
    if (y + ch > r.height - 8) y = Math.max(8, r.height - ch - 8);
    this.card.style.left = x + "px"; this.card.style.top = y + "px";
  }
  closeCard() { if (this.card) this.card.hidden = true; }
  onKey(e) {
    if (e.target.closest("input,select,textarea,dialog") || e.ctrlKey || e.metaKey || e.altKey) return;
    const k = keyOf(e);
    if (S.view3d && S.sel && S.sel.kind === "part") {
      if (k === "f" && v3) v3.fit(); else if (k === "d") $("#t3Dim").click(); else if (k === "a") $("#t3Ang").click();
      else if (k === "l") $("#t3Len").click(); else if (k === "h") $("#t3Hole").click();
      return;
    }
    if (k === "f") this.fit();
    else if (k === "b") toggleOpt("bend"); else if (k === "d") toggleOpt("dim"); else if (k === "n") toggleOpt("name"); else if (k === "m") toggleOpt("measure");
    else if (e.key === "Escape") { this.measure = []; this.closeCard(); this.draw(); }
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
      ctx.fillStyle = sc.mode === "group" && selPart && it.part_id === selPart ? c.selBg : c.fill;
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
    if (!$("#cv").hidden) $("#stZoom").textContent = `1 px = ${fmt(1 / this.s, this.s > 4 ? 3 : 2)} มม.`;
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
          this.text(`${b.dir === "up" ? "พับขึ้น" : "พับลง"} ${fmt(b.angle_in, b.angle_in % 1 ? 1 : 0)}°`, x, y, { size: 11.5, color: c.bend });
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
const ringArea = (r) => { let a = 0; for (let i = 0, j = r.length - 1; i < r.length; j = i++) a += (r[j][0] + r[i][0]) * (r[j][1] - r[i][1]); return a / 2; };
const inRing = (x, y, r) => { let c = false; for (let i = 0, j = r.length - 1; i < r.length; j = i++) { const [xi, yi] = r[i], [xj, yj] = r[j]; if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c; } return c; };
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
  else if (h.kind === "bend") info.innerHTML = `เส้นพับ <b>${h.info.dir === "up" ? "พับขึ้น" : "พับลง"} ${fmt(h.info.angle_in, 1)}° (มุมด้านใน)</b>${h.info.r !== undefined ? " · R ใน " + fmt(h.info.r, 2) + " มม." : ""}${h.info.method ? " · " + (h.info.method === "table" ? "ตารางค่ายืด" : "K-factor") : ""}`;
  else info.innerHTML = `เส้นโค้งอิสระ ยาว <b>${fmt(primLen(q))} มม.</b>`;
}
function toggleOpt(k) {
  viewer.opts[k] = !viewer.opts[k];
  const id = { bend: "tBend", dim: "tDim", name: "tName", measure: "tMeasure" }[k];
  setOn($("#" + id), viewer.opts[k]);
  if (k === "measure") { viewer.measure = []; setStatus(viewer); }
  viewer.draw();
}

async function loadScene() {
  if (!viewer) return;
  const P = S.project, sel = S.sel;
  $("#stageEmpty").hidden = !!sel;
  if (!sel) { viewer.setScene(null); return; }
  const isOv = sel.kind === "overview";
  const want3d = !isOv && sel.kind === "part" && S.view3d;
  $("#viewSeg").hidden = !(sel.kind === "part");
  $("#v2d").setAttribute("aria-pressed", String(!want3d)); $("#v3d").setAttribute("aria-pressed", String(want3d));
  $("#overview").hidden = !isOv; $("#tools2d").hidden = isOv || want3d; $("#tools3d").hidden = !want3d;
  $(".legend").hidden = isOv || want3d; $(".statusbar").hidden = isOv;
  const cvWasHidden = $("#cv").hidden;
  $("#cv").hidden = want3d; $("#wrap3d").hidden = !want3d;
  if (cvWasHidden && !want3d) viewer.resize();
  if (want3d) { await show3d(part(sel.id)); return; }
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
/* ---- 3D view of the selected part (three.js, loaded on first use) ---- */
let v3 = null;
function colors3d() {
  const cs = getComputedStyle(document.documentElement), g = (n) => cs.getPropertyValue(n).trim();
  return { zinc: g("--zinc-100"), ink: g("--ink"), dim: g("--dim"), paper: g("--paper"), steel: g("--steel"),
           steelFill: g("--steel-fill"), bend: g("--bend"), bendFill: g("--bend-fill"), font: g("--font") };
}
async function ensure3d() {
  if (v3) return v3;
  const mod = await import("./viewer3d.js");
  v3 = new mod.Viewer3D($("#wrap3d"), (e) => {
    const info = $("#stInfo");
    if (!e) { info.textContent = "ลากเพื่อหมุน · คลิกขวาลากเพื่อเลื่อน · ล้อเมาส์เพื่อซูม · ชี้ที่ขอบเพื่อดูความยาว"; return; }
    info.innerHTML = e.kind === "hole" ? `รู <b>Ø ${fmt(e.r * 2)} มม.</b> · คลิกเพื่อดูรายละเอียด`
      : e.kind === "arc" ? `ส่วนโค้ง <b>R ${fmt(e.r)} มม.</b> · ยาว ${fmt(e.len)} มม. · คลิกเพื่อดูรายละเอียด`
      : `ขอบ ยาว <b>${fmt(e.len)} มม.</b>${e.kind === "line" ? "" : " (เส้นโค้ง)"} · คลิกเพื่อดูรายละเอียด`;
  }, (st) => {
    $("#foldCtl").hidden = !!st.solid;
    if (st.solid) return;
    $("#t3Fold").textContent = st.folded ? "คลี่" : "พับ";
    $("#t3Fold").title = st.folded ? "คลี่กลับเป็นแผ่นเรียบ" : v3.pressOn ? "พับบนเครื่องพับตามลำดับพับ" : "พับตามลำดับพับทีละรอย";
    $("#t3Prog").value = st.n ? Math.round((st.T / st.n) * 1000) : 0;
    const sub = $("#stageTitle small");
    if (sub && S.sel && S.sel.kind === "part") {
      const p = part(S.sel.id);
      const state = st.press ? `เครื่องพับ ขั้นที่ ${st.press.k + 1}/${st.press.total} · รอย #${st.current.n}`
        : st.flat ? "3D แบบคลี่ (แผ่นเรียบ)" : st.folded ? "3D พับแล้ว" : st.current ? `กำลังพับ #${st.current.n}` : "3D พับบางส่วน";
      sub.textContent = `${state} · ${st.n} รอยพับ · หนา ${fmt(p.thickness.used, 2)} มม.`;
    }
  });
  v3.setColors(colors3d());
  try { v3.animOn = localStorage.getItem("sf3dAnim") === "1"; } catch (_) { /* private mode */ }
  setOn($("#t3Anim"), v3.animOn);
  return v3;
}
async function show3d(p) {
  const sel = S.sel;
  $("#stageEmpty").hidden = true;
  $("#stCoord").textContent = "3D"; $("#stZoom").textContent = "";
  $("#stInfo").textContent = "กำลังโหลดโมเดล 3D…";
  try {
    const viewer = await ensure3d();
    if (p.flat) {
      // sheet part: built from the flat pattern, shown folded; "คลี่" / "พับ" switch
      const key = "fold:" + p.id + ":" + S.version;
      let m = S.cache.get(key);
      if (!m) { m = await getJSON(`/api/jobs/${S.jobId}/parts/${p.id}/fold`); S.cache.set(key, m); }
      if (S.sel !== sel) return;
      $("#stageTitle").innerHTML = `${esc(p.name)}<small></small>`;
      viewer.resize(); viewer.loadFold(m);
    } else {
      // no flat pattern (bolt, tube ...): the solid as modelled
      const key = "mesh:" + p.id;
      let m = S.meshCache.get(key);
      if (!m) { m = await getJSON(`/api/jobs/${S.jobId}/parts/${p.id}/mesh`); S.meshCache.set(key, m); }
      if (S.sel !== sel) return;
      const s = m.bbox[1].map((v, i) => v - m.bbox[0][i]);
      $("#stageTitle").innerHTML = `${esc(p.name)}<small>3D ตามโมเดล · ${fmt(s[0], 1)} × ${fmt(s[1], 1)} × ${fmt(s[2], 1)} มม.</small>`;
      viewer.resize(); viewer.load(m);
    }
    $("#stZoom").textContent = "";
    $("#stInfo").textContent = "ลากเพื่อหมุน · คลิกขวาลากเพื่อเลื่อน · ล้อเมาส์เพื่อซูม · ชี้ที่ขอบเพื่อดูความยาว";
  } catch (e) {
    $("#stInfo").textContent = "เปิด 3D ไม่ได้: " + e.message;
    toast("เปิด 3D ไม่ได้: " + e.message, 4000);
  }
}
function setView3d(on) {
  if (S.view3d === on) return;
  S.view3d = on;
  loadScene();
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
async function openProfileDialog(curId, makeNew) {
  try { S.profilesData = await getJSON("/api/profiles"); } catch (_) { /* keep what we have */ }   // picks up edits made in Excel
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
  const bf = d.bend_file;
  $("#profileBody").innerHTML = `
    ${bf && !DEMO ? `<div class="bendfile"><p class="hint" style="margin:0 0 6px">ตารางค่ายืดเก็บเป็นไฟล์ Excel (1 ชีต = 1 โหมด) โปรแกรมอ่านค่าจากไฟล์นี้ทุกครั้งที่คำนวณ จึงแก้ใน Excel แล้วส่งต่อให้คนอื่นใช้ได้เลย<br>ไฟล์: <code>${esc(bf.path)}</code></p>
      ${bf.error ? `<p class="hint" style="color:var(--bad,#c0392b)">อ่านไฟล์ Excel ไม่ได้ ใช้ค่าล่าสุดที่อ่านได้ไปก่อน: ${esc(bf.error)}</p>` : ""}
      <div class="row"><a class="btn small" href="/api/profiles/xlsx?lang=${lang()}" download>ดาวน์โหลดไฟล์ Excel ค่ายืด</a>
        <button type="button" class="btn small" id="pfImport">นำเข้าจากไฟล์ Excel</button>
        <input type="file" id="pfImportFile" accept=".xlsx" hidden></div></div>` : ""}
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
      <label>มุมที่ถือว่าเป็น 90° (± องศา, 0 = 90° พอดี)<input type="number" step="0.1" min="0" id="pfAng" value="${p.angle_tol}"></label>
      <label>ใช้ตารางได้เมื่อ R ใน ≤ (มม.)<input type="number" step="0.05" min="0" id="pfRat" value="${p.table_max_r ?? 0.2}"></label>
      <label>จับคู่ความหนากับตารางได้ ± (มม.)<input type="number" step="0.01" id="pfMatch" value="${p.match_tol}"></label>
      <label class="switch" style="align-self:end"><input type="checkbox" id="pfJog" ${p.jog_use_k ? "checked" : ""}> รอยพับสลับทิศติดกัน (jog) ใช้ K-factor</label>
      <label class="switch" style="grid-column:1/3"><input type="checkbox" id="pfSimp" ${p.simplify_bends !== false ? "checked" : ""}> เส้นตัดโค้งที่พาดผ่านรอยพับ ทำเป็นเส้นตรง (เหมือน Simplify bends ของ SolidWorks)</label></div></details>`;
  const body = $("#profileBody");
  if ($("#pfImport")) {
    $("#pfImport").onclick = () => $("#pfImportFile").click();
    $("#pfImportFile").onchange = importBendXlsx;
  }
  $("#pfSel").onchange = (e) => { syncProfile(); d.cur = e.target.value; renderProfileDialog(); };
  $("#pfAdd").onclick = () => { syncProfile(); p.table.push({ gauge: "", thickness: 0, bd: 0, material: "" }); renderProfileDialog(); const ins = $("#pfRows").querySelectorAll("tr:last-child input"); if (ins[0]) ins[0].focus(); };
  $("#pfDup").onclick = () => { syncProfile(); const n = JSON.parse(JSON.stringify(p)); n.id = "m" + Date.now().toString(36); n.name = p.name + " (สำเนา)"; d.profiles.push(n); d.cur = n.id; renderProfileDialog(); };
  $("#pfDel").onclick = () => { d.profiles = d.profiles.filter((x) => x.id !== p.id); d.cur = d.profiles[0].id; if (d.active === p.id) d.active = d.cur; renderProfileDialog(); };
  $("#pfMatAdd").onclick = () => { syncProfile(); d.materials.push({ id: "", name: "", prefix: "" }); renderProfileDialog(); const ins = $("#pfMats").querySelectorAll("tr:last-child input"); if (ins[0]) ins[0].focus(); };
  body.querySelectorAll("[data-rm]").forEach((b) => (b.onclick = () => { syncProfile(); p.table.splice(+b.dataset.rm, 1); renderProfileDialog(); }));
  body.querySelectorAll("[data-mrm]").forEach((b) => (b.onclick = () => { syncProfile(); d.materials.splice(+b.dataset.mrm, 1); renderProfileDialog(); }));
}
async function importBendXlsx(ev) {
  const f = ev.target.files[0]; ev.target.value = ""; if (!f) return;
  try {
    const fd = new FormData(); fd.append("file", f);
    S.profilesData = await (await api("/api/profiles/xlsx", { method: "POST", body: fd })).json();
    const keep = editing.cur;
    editing = JSON.parse(JSON.stringify(S.profilesData));
    editing.cur = editing.profiles.some((x) => x.id === keep) ? keep : editing.active;
    fillProfileSelects(); updateLandHint();
    if (S.jobId) { await patchSettings({ profile: S.profilesData.active }); $("#profileSel").value = S.profilesData.active; syncProfileMenu(); }
    renderProfileDialog();
    $("#profileMsg").textContent = `นำเข้า ${S.profilesData.profiles.length} โหมดจาก Excel แล้ว`;
  } catch (e) { $("#profileMsg").textContent = e.message; }
}
function syncProfile() {
  const d = editing, p = d.profiles.find((x) => x.id === d.cur); if (!p) return;
  p.name = $("#pfName").value.trim() || p.name; p.k_factor = +$("#pfK").value || 0.5;
  $("#pfRows").querySelectorAll("input,select").forEach((i) => { const r = p.table[+i.dataset.r]; const f = i.dataset.f; r[f] = f === "gauge" || f === "material" ? i.value.trim() : +i.value; });
  p.angle_tol = Math.max(0, +$("#pfAng").value || 0); p.table_max_r = Math.max(0, +$("#pfRat").value || 0); delete p.table_max_ratio; p.match_tol = +$("#pfMatch").value || 0.06; p.jog_use_k = $("#pfJog").checked; p.simplify_bends = $("#pfSimp").checked;
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
    if (S.jobId) { await patchSettings({ profile: S.profilesData.active }); $("#profileSel").value = S.profilesData.active; syncProfileMenu(); }
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
/* price of a W x L sheet at this row's thickness: ROUNDUP(weight of that sheet x price per kg) */
const REF_AREA = 1220 * 2440;
const platePrice = (r, s) => Math.ceil(+(r.kg_plate / REF_AREA * s.w * s.l * r.price_kg).toFixed(9));
function priceCode(r) { return r.gauge ? matPrefix(r.material) + r.gauge : `${matPrefix(r.material)}#${fmtG(r.thickness)}mm`; }
function renderPriceForm() {
  const P = S.price.pricing, M = S.price.meta;
  const dis = DEMO ? "disabled" : "";
  const used = new Set(S.price.groups.map((g) => g.material_id + "|" + g.thickness));
  const isUsed = (r) => [...used].some((k) => { const [m, t] = k.split("|"); return m === r.material && Math.abs(+t - r.thickness) < 0.061; });
  const shown = P.prices.map((r, i) => ({ r, i })).filter(({ r }) => S.priceAll || isUsed(r));
  const onSheets = P.sheets.filter((s) => s.on && s.w > 0 && s.l > 0);
  let lastMat = null;
  const priceRows = shown.map(({ r, i }) => {
    const head = r.material !== lastMat ? `<tr class="mat-row"><td colspan="${4 + onSheets.length}">${esc(matName(r.material))}</td></tr>` : "";
    lastMat = r.material;
    return head + `<tr class="${isUsed(r) ? "used" : ""}"><td><b>${esc(priceCode(r))}</b></td><td class="n">${fmtG(r.thickness)}</td>
      <td><input type="number" step="0.5" min="0" data-p="${i}" data-f="price_kg" value="${r.price_kg}" aria-label="ราคาต่อกิโลกรัม ${esc(priceCode(r))}" ${dis}></td>
      <td><input type="number" step="0.1" min="0" data-p="${i}" data-f="kg_plate" value="${+r.kg_plate.toFixed(2)}" aria-label="น้ำหนักแผ่น 1220×2440 ${esc(priceCode(r))}" ${dis}></td>
      ${onSheets.map((s) => `<td class="n plate-price" data-pp="${i}" data-sw="${s.w}" data-sl="${s.l}">${baht(platePrice(r, s))}</td>`).join("")}</tr>`;
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
      <p class="hint">แต่ละแถวคือเหล็กชนิดนั้นที่ความหนานั้น ใส่ราคาต่อ กก. และน้ำหนักแผ่น 1220×2440 ที่ความหนานั้น ราคาต่อแผ่นคำนวณให้</p>
      <label class="switch"><input type="checkbox" id="prAll" ${S.priceAll ? "checked" : ""}> แสดงทุกความหนา (ปกติแสดงเฉพาะที่ใช้ในงานนี้)</label>
      <table class="pt price-pt"><thead><tr><th>วัตถุดิบ</th><th class="n">หนา (มม.)</th><th>ราคา / กก.</th><th>น้ำหนักแผ่น 1220×2440 (กก.)</th>${onSheets.map((s) => `<th class="n">ราคาต่อแผ่น ${s.w}×${s.l}</th>`).join("")}</tr></thead><tbody>${priceRows}</tbody></table>
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
        <label>เหล็กที่ปกติไม่ทาสี<input type="text" id="prNoPaint" value="${esc(P.no_paint.join(", "))}" placeholder="เช่น SUS" ${dis}></label>
        <label>ค่าชุบ (บาท / กก.)<input type="number" step="1" min="0" id="prPlate" value="${P.plating_per_kg}" ${dis}></label>
        <p class="hint" style="grid-column:1/3;margin:0">เลือกชิ้นที่ทาสี / ชุบ ได้ในตารางรายชิ้นทางขวา · ค่าสีคิดจากแผ่นคลี่สี่เหลี่ยมเต็ม (กว้าง × ยาว) ไม่หักรู</p>
      </div>
      <h3 class="dlg-h3">ค่าใช้จ่ายอื่น</h3>
      <p class="hint">เพิ่มรายการเองได้ เช่น ค่าพับ ค่าติ๊ก เจาะรู พ่นทราย ค่าขนส่ง · โปรแกรมนับจุดพับและรูให้เอง</p>
      <table class="pt extra-pt"><thead><tr><th>เปิดใช้</th><th>รายการ</th><th>บาท</th><th>คิดต่อ</th><th></th></tr></thead><tbody>
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
    const r = P.prices[+i.dataset.p];
    r[i.dataset.f] = +i.value || 0;
    body.querySelectorAll(`[data-pp="${i.dataset.p}"]`).forEach((pp) => { pp.textContent = baht(platePrice(r, { w: +pp.dataset.sw, l: +pp.dataset.sl })); });
  });
  body.querySelectorAll("[data-s]").forEach((i) => { const s = P.sheets[+i.dataset.s]; s[i.dataset.f] = i.dataset.f === "on" ? i.checked : +i.value || 0; });
  body.querySelectorAll("[data-e]").forEach((i) => { const e = P.extras[+i.dataset.e], f = i.dataset.f; e[f] = f === "on" ? i.checked : f === "rate" ? +i.value || 0 : i.value; });
  P.margin = +$("#prMargin").value || 0; P.paint_per_m2 = +$("#prPaint").value || 0;
  P.plating_per_kg = +$("#prPlate").value || 0; P.markup_pct = +$("#prMarkup").value || 0;
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
    <tr class="sub"><td colspan="7">ใช้แผ่น ${Object.entries(x.sheets).map(([k, v]) => `${k.replace("x", " × ")} ${v} แผ่น`).join(", ") || "-"}${x.sheet_area ? ` · ใช้เนื้อแผ่น ${fmt(+(x.use * 100).toFixed(1), 1)}% เศษเหลือ ${fmt(100 - +(x.use * 100).toFixed(1), 1)}%` : ""}</td></tr>`).join("") +
    R.job_extras.map((e) => `<tr><td colspan="6">${esc(e.name)} <small>(ต่องาน)</small></td><td class="n"><b>${baht(e.amount, 2)}</b></td></tr>`).join("");
  const rows = R.rows.map((r) => r.error ? `<tr class="err"><td>${esc(r.name)}</td><td colspan="9">${esc(r.error)}</td></tr>` :
    `<tr><td>${esc(r.name)}<small>${esc(r.code)} · ${fmt(r.x, 1)} × ${fmt(r.y, 1)} มม.</small></td>
      <td>${r.sheet.w}×${r.sheet.l}<small>เรียงแบบ ${r.sheet.arr}${r.sheet.arr === 2 ? " (หมุน 90°)" : ""} · ${r.sheet.nx}×${r.sheet.ny} = ${r.sheet.count} ชิ้น · ใช้ ${fmt(+(r.sheet.use * 100).toFixed(1), 1)}% เศษ ${fmt(100 - +(r.sheet.use * 100).toFixed(1), 1)}%</small></td>
      <td class="tick"><input type="checkbox" data-tp="${r.part_id}" ${r.paint_on ? "checked" : ""} aria-label="ทาสี ${esc(r.name)}" ${DEMO ? "disabled" : ""}></td>
      <td class="tick"><input type="checkbox" data-tl="${r.part_id}" ${r.plate_on ? "checked" : ""} aria-label="ชุบ ${esc(r.name)}" ${DEMO ? "disabled" : ""}></td>
      <td class="n">${baht(r.metal)}</td><td class="n">${baht(r.paint)}${r.paint_on ? `<small>จริง ${baht(r.paint_real)}</small>` : ""}</td>
      <td class="n">${baht(r.plating, 2)}</td><td class="n">${baht(r.extra, r.extra % 1 ? 2 : 0)}</td><td class="n">${r.qty}</td><td class="n"><b>${baht(r.total, 2)}</b></td></tr>`).join("");
  box.innerHTML = `
    <div class="price-total"><span>ราคารวม${R.markup ? " (รวมบวกเพิ่ม)" : ""}</span><b>${baht(R.total, 2)}</b><small>บาท · เหล็ก ${fmt(R.weight, 2)} กก.${R.markup ? ` · บวกเพิ่ม ${baht(R.markup, 2)}` : ""}</small></div>
    ${R.errors.length ? `<p class="err">${R.errors.length} ชิ้นคิดราคาไม่ได้ ดูแถวสีแดง</p>` : ""}
    <table class="otable ptab"><thead><tr><th>วัตถุดิบ</th><th class="n">ชิ้น</th><th class="n">กก.</th><th class="n">โลหะ</th><th class="n">สี</th><th class="n">อื่นๆ</th><th class="n">รวม</th></tr></thead><tbody>${g}</tbody></table>
    <h3 class="dlg-h3">รายชิ้น (ราคาต่อชิ้น)</h3>
    <table class="otable ptab"><thead><tr><th>ชิ้นส่วน</th><th>แผ่นที่ใช้ · การวาง</th><th class="tick">ทาสี</th><th class="tick">ชุบ</th><th class="n">โลหะ/ชิ้น</th><th class="n">สี/ชิ้น</th><th class="n">ชุบรวม</th><th class="n">อื่นๆ/ชิ้น</th><th class="n">จำนวน</th><th class="n">รวม</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="hint">ค่าสีใช้ค่าคลี่ (แผ่นสี่เหลี่ยมเต็ม ไม่หักรู) · “จริง” = ตามขอบชิ้นจริง ไม่หักรู แสดงไว้เทียบ</p>`;
  box.querySelectorAll("[data-tp],[data-tl]").forEach((c) => c.addEventListener("change", async () => {
    const body = c.dataset.tp ? { paint: c.checked } : { plate: c.checked };
    try {
      setProject(await sendJSON(`/api/jobs/${S.jobId}/parts/${c.dataset.tp || c.dataset.tl}`, "PATCH", body));
      S.price = Object.assign(await getJSON(`/api/jobs/${S.jobId}/pricing`), { pricing: S.price.pricing });
      renderPriceResult();
    } catch (e) { toast(e.message); }
  }));
}
async function priceXlsx() {
  if (DEMO) { download("pricing"); return; }
  clearTimeout(schedulePrice._t);
  if (await pushPrice()) location.href = `/api/jobs/${S.jobId}/export/pricing?lang=${lang()}`;
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
  document.querySelectorAll("[data-theme-pick]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.themePick === t)));
  if (viewer) { viewer.colors(); viewer.draw(); }
  if (v3) v3.setColors(colors3d());
}
function pickTheme(t) {
  try { localStorage.setItem("sf-theme", t); } catch (_) { /* ignore */ }
  applyTheme(t);
}
function initTheme() {
  let t = "auto";
  try { t = localStorage.getItem("sf-theme") || "auto"; } catch (_) { /* private mode */ }
  if (!THEMES.some((x) => x[0] === t)) t = "auto";
  applyTheme(t);
  document.querySelectorAll("[data-theme-pick]").forEach((b) => (b.onclick = () => pickTheme(b.dataset.themePick)));
}

/* ======================================================================
   menus, shortcuts, command palette
   ====================================================================== */
function setOn(el, on) {
  if (el) el.setAttribute(el.getAttribute("role") === "menuitemcheckbox" ? "aria-checked" : "aria-pressed", String(!!on));
}
/* the key that was pressed, by its place on the keyboard: shortcuts work with the Thai layout too */
function keyOf(e) {
  if (/^Key[A-Z]$/.test(e.code)) return e.code.slice(3).toLowerCase();
  if (/^Digit\d$/.test(e.code)) return e.code.slice(5);
  return e.key.length === 1 ? e.key.toLowerCase() : e.key;
}
const menuItems = (pop) => [...pop.querySelectorAll('[role^="menuitem"]')].filter((x) => !x.disabled && x.offsetParent !== null);
function closeMenus(except) {
  document.querySelectorAll("[data-menu]").forEach((m) => {
    if (m === except) return;
    const pop = m.querySelector(".menu-pop");
    if (!pop.hidden) { pop.hidden = true; m.querySelector(".menu-btn").setAttribute("aria-expanded", "false"); }
  });
}
function openMenu(m, focusFirst) {
  closeMenus(m);
  if (m.dataset.menu === "profile") buildProfileItems();
  const pop = m.querySelector(".menu-pop");
  pop.style.left = pop.style.right = "";
  pop.hidden = false;
  m.querySelector(".menu-btn").setAttribute("aria-expanded", "true");
  const r = pop.getBoundingClientRect();
  if (r.right > window.innerWidth - 8) { pop.style.left = "auto"; pop.style.right = "0"; }
  else if (r.left < 8) { pop.style.left = "0"; pop.style.right = "auto"; }
  if (focusFirst) { const it = menuItems(pop)[0]; if (it) it.focus(); }
}
function initMenus() {
  document.querySelectorAll("[data-menu]").forEach((m) => {
    const btn = m.querySelector(".menu-btn"), pop = m.querySelector(".menu-pop");
    btn.setAttribute("aria-haspopup", "menu"); btn.setAttribute("aria-expanded", "false");
    btn.addEventListener("click", (e) => { e.stopPropagation(); if (pop.hidden) openMenu(m, e.detail === 0); else closeMenus(); });
    btn.addEventListener("keydown", (e) => { if (e.key === "ArrowDown") { e.preventDefault(); openMenu(m, true); } });
    pop.addEventListener("click", (e) => {
      e.stopPropagation();
      const it = e.target.closest('[role^="menuitem"]');
      if (!it || it.disabled) return;
      if (it.getAttribute("role") !== "menuitemcheckbox") { closeMenus(); btn.focus({ preventScroll: true }); }
    });
    pop.addEventListener("keydown", (e) => {
      const list = menuItems(pop), i = list.indexOf(document.activeElement);
      if (e.key === "ArrowDown") { e.preventDefault(); list[(i + 1) % list.length].focus(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); list[(i - 1 + list.length) % list.length].focus(); }
      else if (e.key === "Home") { e.preventDefault(); list[0].focus(); }
      else if (e.key === "End") { e.preventDefault(); list[list.length - 1].focus(); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeMenus(); btn.focus(); }
      else if (e.key === "Tab") closeMenus();
    });
  });
  document.addEventListener("click", () => closeMenus());
}
function buildProfileItems() {
  const sel = $("#profileSel"), box = $("#profileItems");
  box.innerHTML = [...sel.options].map((o) => `<button type="button" role="menuitemradio" aria-checked="${o.value === sel.value}" data-prof="${esc(o.value)}"><span>${esc(o.textContent)}</span></button>`).join("");
  box.querySelectorAll("[data-prof]").forEach((b) => (b.onclick = () => {
    if (sel.value !== b.dataset.prof) { sel.value = b.dataset.prof; sel.dispatchEvent(new Event("change")); }
    syncProfileMenu();
  }));
}
function syncProfileMenu() {
  const sel = $("#profileSel"), o = sel.options[sel.selectedIndex];
  $("#profileName").textContent = o ? o.textContent : "–";
}
function newJob() {
  S.jobId = null; S.project = null; S.sel = null; S.cache.clear(); S.meshCache.clear(); S.editGroups = false; S.picked.clear(); S.price = null;
  $("#topJob").hidden = true; $("#topActions").hidden = true; $("#file").value = ""; backToLanding("");
}
function exportPricing() {
  if (!S.project) return;
  if (S.price) priceXlsx();
  else if (DEMO) download("pricing");
  else { location.href = `/api/jobs/${S.jobId}/export/pricing?lang=${lang()}`; toast("กำลังดาวน์โหลดใบคิดราคา (Excel)"); }
}
/* previous / next part in the order of the list on the left */
function stepPart(dir) {
  const ids = [...new Set([...document.querySelectorAll("#rail [data-part]")].map((el) => el.dataset.part))];
  if (!ids.length) return;
  const i = S.sel && S.sel.kind === "part" ? ids.indexOf(S.sel.id) : -1;
  const next = ids[(i + dir + ids.length) % ids.length] ?? ids[0];
  selectPart(next);
  const el = document.querySelector(`#rail [data-part="${CSS.escape(next)}"]`);
  if (el) el.scrollIntoView({ block: "nearest" });
}
const onPart = () => S.project && S.sel && S.sel.kind === "part";
const in3d = () => onPart() && S.view3d && v3;

const KEYS = [
  ["ทั่วไป", [
    ["ค้นหาคำสั่งหรือชิ้นส่วน", ["Ctrl", "K"]], ["คีย์ลัดทั้งหมด", ["?"]], ["เปิดไฟล์ใหม่", ["Ctrl", "O"]], ["บันทึกงาน", ["Ctrl", "S"]],
    ["ดาวน์โหลด DXF ทั้งหมด + Excel", ["Ctrl", "E"]], ["คิดราคา", ["P"]], ["ใบสั่งตัด (ภาพรวม)", ["O"]], ["ชิ้นก่อนหน้า / ถัดไป", ["[", "]"]],
  ]],
  ["ดูชิ้นงาน", [["สลับ 2D / 3D", ["V"]], ["ดูทั้งหมด", ["F"]]]],
  ["แบบคลี่ 2D", [["เส้นพับ", ["B"]], ["ขนาด", ["D"]], ["ชื่อชิ้น", ["N"]], ["วัดระยะ", ["M"]], ["ยกเลิกการวัด", ["Esc"]]]],
  ["3D", [["พับ / คลี่", ["Space"]], ["จำลองเครื่องพับ เปิด/ปิด", ["G"]], ["ขนาดรวม", ["D"]], ["มุมพับ", ["A"]], ["ความยาวขอบ", ["L"]],
    ["รูและรัศมีโค้ง", ["H"]], ["มุมเอียง / หน้า / บน / ข้าง", ["1", "2", "3", "4"]]]],
];
function openKeys() {
  $("#keysBody").innerHTML = KEYS.map(([h, rows]) => `<h3>${esc(h)}</h3>` + rows.map(([t, ks]) =>
    `<div class="kr"><span>${esc(t)}</span><span>${ks.map((k) => `<kbd>${esc(k)}</kbd>`).join("")}</span></div>`).join("")).join("");
  closeMenus();
  $("#keysDlg").showModal();
}

/* command palette: every command and every part, found by typing */
function paletteItems() {
  const P = S.project, A = [];
  const act = (t, run, k, sub, ok = true) => { if (ok) A.push({ sec: "คำสั่ง", t, run, k, sub }); };
  if (P) {
    act("ดาวน์โหลด DXF ทั้งหมด + Excel", () => download("all"), "Ctrl E", "ไฟล์ .zip พร้อมส่งเครื่องเลเซอร์", P.groups.length > 0);
    act("ดาวน์โหลดใบสรุปงาน (Excel)", () => download("xlsx"));
    act("ดาวน์โหลดใบคิดราคา (Excel)", exportPricing);
    act("ดาวน์โหลด STEP แยกชิ้น (.zip)", () => $("#btnSteps").click());
    act("คิดราคา", openPricing, "P");
    act("บันทึกงาน (.sheetflat)", saveJob, "Ctrl S");
    act("ใบสั่งตัด (ภาพรวม)", () => $("#ovLink").click(), "O");
    act("สลับ 2D / 3D", () => setView3d(!S.view3d), "V", "", onPart());
    act("พับ / คลี่", () => $("#t3Fold").click(), "Space", "ชิ้นงาน 3D", in3d());
    act("จำลองเครื่องพับ เปิด/ปิด", () => $("#t3Press").click(), "G", "", in3d());
    act("เปิดไฟล์ใหม่", newJob, "Ctrl O");
  } else act("เปิดไฟล์ STEP", () => $("#file").click(), "Ctrl O");
  act("แก้ตารางค่ายืด", openProfileDialog);
  for (const [v, l] of THEMES) act(`สีหน้าจอ: ${l}`, () => pickTheme(v));
  act("คีย์ลัดทั้งหมด", openKeys, "?");
  if (P) {
    for (const g of P.groups) A.push({ sec: "ไฟล์ DXF", t: g.name || g.code, sub: `${g.filename} · ${g.pieces} ชิ้น`, run: () => selectGroup(g.id) });
    for (const p of P.parts) A.push({ sec: "ชิ้นส่วน", t: p.name, sub: `${p.code || ""}${p.flat ? ` · ${fmt(p.flat.w, 0)} × ${fmt(p.flat.h, 0)} มม.` : " · ไม่นำไปตัด"} · ${p.qty} ชิ้น`, run: () => selectPart(p.id) });
  }
  return A;
}
const PAL = { items: [], shown: [], i: 0 };
function openPalette() {
  closeMenus();
  PAL.items = paletteItems();
  $("#palInput").value = "";
  renderPalette();
  $("#palette").showModal();
  $("#palInput").focus();
}
function renderPalette() {
  const q = $("#palInput").value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const hit = (x) => q.every((w) => `${x.t} ${x.sub || ""} ${x.sec}`.toLowerCase().includes(w));
  const bySec = {};
  for (const x of PAL.items) if (hit(x)) (bySec[x.sec] = bySec[x.sec] || []).push(x);
  PAL.shown = [];
  let html = "";
  for (const sec of ["คำสั่ง", "ไฟล์ DXF", "ชิ้นส่วน"]) {
    // names that start with what was typed come first
    const rank = (x) => { const t = x.t.toLowerCase(), w = q.join(" "); return !w ? 0 : t === w ? 0 : t.startsWith(w) ? 1 : 2; };
    const list = (bySec[sec] || []).map((x, i) => [rank(x), i, x]).sort((a, b) => a[0] - b[0] || a[1] - b[1]).map((r) => r[2])
      .slice(0, q.length ? 30 : sec === "ชิ้นส่วน" ? 8 : 12);
    if (!list.length) continue;
    html += `<li class="pal-sec" role="presentation">${sec}</li>`;
    for (const x of list) {
      const i = PAL.shown.push(x) - 1;
      html += `<li role="option" id="pal${i}" data-i="${i}" aria-selected="false"><span>${esc(x.t)}</span>${x.sub ? `<small>${esc(x.sub)}</small>` : ""}<span class="pal-kind">${x.k ? `<kbd>${esc(x.k)}</kbd>` : ""}</span></li>`;
    }
  }
  $("#palList").innerHTML = html || `<li class="pal-empty" role="presentation">ไม่พบ ลองพิมพ์คำอื่น</li>`;
  PAL.i = 0; markPalette();
}
function markPalette() {
  $("#palList").querySelectorAll("[data-i]").forEach((li) => li.setAttribute("aria-selected", String(+li.dataset.i === PAL.i)));
  const cur = $("#pal" + PAL.i);
  $("#palInput").setAttribute("aria-activedescendant", cur ? cur.id : "");
  if (cur) cur.scrollIntoView({ block: "nearest" });
}
function runPalette(i) {
  const x = PAL.shown[i];
  if (!x) return;
  $("#palette").close();
  x.run();
}
function initPalette() {
  $("#palInput").addEventListener("input", renderPalette);
  $("#palInput").addEventListener("keydown", (e) => {
    const n = PAL.shown.length;
    if (e.key === "ArrowDown") { e.preventDefault(); if (n) { PAL.i = (PAL.i + 1) % n; markPalette(); } }
    else if (e.key === "ArrowUp") { e.preventDefault(); if (n) { PAL.i = (PAL.i - 1 + n) % n; markPalette(); } }
    else if (e.key === "Enter") { e.preventDefault(); runPalette(PAL.i); }
  });
  $("#palList").addEventListener("mousemove", (e) => { const li = e.target.closest("[data-i]"); if (li && +li.dataset.i !== PAL.i) { PAL.i = +li.dataset.i; markPalette(); } });
  $("#palList").addEventListener("click", (e) => { const li = e.target.closest("[data-i]"); if (li) runPalette(+li.dataset.i); });
  $("#palette").addEventListener("click", (e) => { if (e.target === $("#palette")) $("#palette").close(); });
}
function initShortcuts() {
  window.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey, k = keyOf(e);
    if (mod && k === "k") { e.preventDefault(); if ($("#palette").open) $("#palette").close(); else openPalette(); return; }
    if (document.querySelector("dialog[open]")) return;
    if (mod && !e.altKey) {
      if (k === "s" && S.project) { e.preventDefault(); saveJob(); }
      else if (k === "o") { e.preventDefault(); if (S.project) newJob(); else $("#file").click(); }
      else if (k === "e" && S.project && S.project.groups.length) { e.preventDefault(); download("all"); }
      return;
    }
    if (e.altKey || e.target.closest("input,select,textarea,[contenteditable]")) return;
    if (e.key === "?" || (e.code === "Slash" && e.shiftKey)) { e.preventDefault(); openKeys(); return; }
    if (!S.project || $("#bench").hidden) return;
    if (k === "p") { e.preventDefault(); openPricing(); }
    else if (k === "o") $("#ovLink").click();
    else if (e.code === "BracketLeft" || e.code === "BracketRight") { e.preventDefault(); stepPart(e.code === "BracketRight" ? 1 : -1); }
    else if (onPart() && k === "v") setView3d(!S.view3d);
    else if (in3d()) {
      if (e.code === "Space" && !e.target.closest("button")) { e.preventDefault(); $("#t3Fold").click(); }
      else if (k === "g") $("#t3Press").click();
      else if ("1234".includes(k) && k.length === 1) v3.view(["iso", "front", "top", "right"][+k - 1]);
    }
  });
}

/* inspector sections that fold away; which ones are open is remembered */
function acc(key, title, meta, cls = "", forceOpen = false) {
  const open = forceOpen || S.acc.has(key);
  return `<details class="sec acc" data-acc="${key}" ${open ? "open" : ""}><summary><h3>${esc(title)}</h3><span class="acc-meta ${cls}">${meta}</span></summary>`;
}
function initAcc() {
  try { S.acc = new Set(JSON.parse(localStorage.getItem("sf-acc") || "[]")); } catch (_) { S.acc = new Set(); }
  $("#inspector").addEventListener("toggle", (e) => {
    const d = e.target;
    if (!d.matches || !d.matches("details.acc")) return;
    if (d.open) S.acc.add(d.dataset.acc); else S.acc.delete(d.dataset.acc);
    try { localStorage.setItem("sf-acc", JSON.stringify([...S.acc])); } catch (_) { /* ignore */ }
  }, true);
}

/* ======================================================================
   boot
   ====================================================================== */
window.addEventListener("DOMContentLoaded", async () => {
  viewer = new Viewer($("#cv"));
  initTheme(); initMenus(); initPalette(); initShortcuts(); initAcc();
  // texts built with the language in them (dates) are rebuilt when the language changes
  window.addEventListener("sf-lang", () => { if (S.project && !$("#bench").hidden) { renderInspector(); if (S.sel && S.sel.kind === "overview") renderOverview(); } });
  $("#btnKeys").onclick = openKeys; $("#stKeys").onclick = openKeys; $("#btnPalette").onclick = openPalette;
  $("#btnPriceXlsx").onclick = exportPricing;
  $("#btnSave").onclick = saveJob;
  $("#btnPrice").onclick = openPricing;
  $("#priceXlsx").onclick = priceXlsx;
  $("#priceDefault").onclick = () => { if (DEMO) { toast(DEMO_MSG); return; } clearTimeout(schedulePrice._t); pushPrice(true); };
  $("#priceForm").addEventListener("submit", (e) => e.preventDefault());
  $("#tFit").onclick = () => viewer.fit();
  $("#v2d").onclick = () => setView3d(false);
  $("#v3d").onclick = () => setView3d(true);
  $("#t3Fit").onclick = () => v3 && v3.fit();
  const tog3 = (id, key) => { $(id).onclick = () => { if (!v3) return; const on = !v3.show[key]; v3.setShow(key, on); setOn($(id), on); }; };
  tog3("#t3Dim", "size"); tog3("#t3Ang", "angle"); tog3("#t3Len", "len"); tog3("#t3Hole", "hole");
  $("#t3Fold").onclick = () => { if (!v3 || !v3.F) return; if (v3.F.T >= v3.F.n - 1e-9) v3.unfold(); else v3.fold(); };
  $("#t3Prog").addEventListener("input", (e) => v3 && v3.scrub(+e.target.value / 1000));
  $("#t3Anim").onclick = () => { if (!v3) return; v3.animOn = !v3.animOn; setOn($("#t3Anim"), v3.animOn); try { localStorage.setItem("sf3dAnim", v3.animOn ? "1" : "0"); } catch (_) { /* private mode */ } };
  $("#t3Press").onclick = () => { if (!v3) return; const on = !v3.pressOn; v3.setPress(on); setOn($("#t3Press"), on); };
  document.querySelectorAll("[data-view3]").forEach((b) => (b.onclick = () => v3 && v3.view(b.dataset.view3)));
  $("#tBend").onclick = () => toggleOpt("bend"); $("#tDim").onclick = () => toggleOpt("dim");
  $("#tName").onclick = () => toggleOpt("name"); $("#tMeasure").onclick = () => toggleOpt("measure");
  $("#btnAll").onclick = () => download("all");
  $("#btnXlsx").onclick = () => download("xlsx");
  $("#btnSteps").onclick = () => (DEMO ? toast(DEMO_MSG) : download("steps"));
  $("#btnProfile").onclick = openProfileDialog;
  $("#btnNew").onclick = newJob;
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
  const showVer = (v) => { if (v) { const e = $("#appVer"); e.textContent = "v" + v.replace(/^v/, ""); e.hidden = false; } };
  if (DEMO) showVer(window.SHEETFLAT_VERSION);
  if (!DEMO) {
    try {
      const info = await (await fetch("/api/info")).json();
      showVer(info.version);
      if (info.cloud) { document.querySelector(".pathbox").hidden = true; document.body.classList.add("cloud"); }
    } catch (_) { /* ignore */ }
  }
  try { await initLanding(); } catch (e) { landError("เชื่อมต่อเซิร์ฟเวอร์ไม่ได้: " + e.message); }
});
