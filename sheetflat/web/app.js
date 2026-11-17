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
  $("#landProfileHint").textContent = `ตาราง ${p.table.length} แถว · K-factor ${p.k_factor} (ใช้กับรอยพับไม่ฉากและโค้ง)`;
}
function landError(msg) { const e = $("#landErr"); e.textContent = msg; e.hidden = !msg; }
const chosenMaterial = () => (document.querySelector("input[name=mat]:checked") || {}).value || "ZN";

async function startUpload(file) {
  landError("");
  if (DEMO) { landError("เวอร์ชันออนไลน์ยังคลี่ไฟล์ของคุณไม่ได้ (ต้องใช้ OpenCASCADE ที่รันบนเครื่อง) กด “เปิดงานตัวอย่าง” เพื่อลองดู หรือดาวน์โหลดโปรแกรมไปรันในเครื่อง"); return; }
  const fd = new FormData();
  fd.append("file", file); fd.append("material", chosenMaterial()); fd.append("profile", $("#landProfile").value);
  showBusy("กำลังอัปโหลด " + file.name);
  try {
    const r = await (await api("/api/upload", { method: "POST", body: fd })).json();
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
  S.project = p; S.version++; S.cache.clear();
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
  else box.innerHTML = P.groups.map(groupHTML).join("");
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
  return `<div class="plate ${selG ? "sel" : ""}">
    <button class="plate-head" data-group="${g.id}" aria-label="ดูไฟล์ ${esc(g.filename)}">
      <span class="plate-code">${esc(g.code)}</span>
      <span class="plate-t"><i class="tbar" style="height:${tpx}px"></i><b>${fmtG(g.thickness)} มม.</b></span>
      <span class="plate-file">${esc(g.filename)}</span>
      <span class="plate-sum">${g.pieces} ชิ้น · ${g.part_ids.length} แบบ · ${g.sheets.length > 1 ? g.sheets.length + " แผ่น" : fmt(g.sheets[0].w, 0) + " × " + fmt(g.sheets[0].h, 0) + " มม."}</span>
    </button>
    <div class="plate-body">${rows}</div>
    <div class="plate-foot">${sheets}</div></div>`;
}
function exclHTML(p) {
  const sel = S.sel && S.sel.kind === "part" && S.sel.id === p.id;
  const can = p.reason_code !== "not_sheet" || p.thickness.measured;
  return `<div class="xrow ${sel ? "sel" : ""}"><div class="xt" data-xpart="${p.id}"><b>${esc(p.name)}</b><span>× ${p.qty}</span></div>
    <p class="why">${esc(p.reason || "ผู้ใช้เลือกไม่ตัด")}</p>
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
  } else if (S.project) {
    enterBench();
  } else {
    $("#landing").hidden = false;
  }
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
  if (S.sel.kind === "overview") { box.innerHTML = overviewInspector(); box.querySelectorAll("[data-dl]").forEach((b) => b.addEventListener("click", () => download(b.dataset.dl))); return; }
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
  <div class="sec"><div class="dl-btns"><button class="btn primary" data-dl="all">ดาวน์โหลด DXF ทั้งหมด + Excel (.zip)</button><button class="btn" data-dl="xlsx">ดาวน์โหลดใบสรุป Excel</button></div>
  <p class="hint">คลิกแถวในใบสั่งตัดเพื่อดูแบบคลี่ของชิ้นนั้น คลิกชื่อความหนาเพื่อดูไฟล์ DXF ทั้งแผ่น</p></div>`;
}
function renderOverview() {
  const P = S.project, s = P.summary;
  const groups = P.groups.map((g) => {
    const rows = g.part_ids.map((pid) => {
      const p = part(pid), f = p.flat;
      return `<tr class="click" data-part="${pid}"><td>${thumbSVG(p.thumb)}</td><td>${esc(p.name)}${p.warnings.length ? ' <span class="badge">ตรวจสอบ</span>' : ""}</td>
        <td class="n">${fmt(f.w)} × ${fmt(f.h)}</td><td class="n">${f.bends.length}</td><td class="n">${f.holes}</td><td class="qty">${p.qty}</td></tr>`;
    }).join("");
    const tpx = Math.max(3, Math.round(g.thickness * 5));
    return `<section class="ogroup"><div class="ogroup-head">
        <h3><button class="linkish" data-group="${g.id}">${esc(g.code)}</button></h3>
        <span class="tb"><i class="tbar" style="height:${tpx}px"></i>หนา ${fmtG(g.thickness)} มม.</span>
        <button class="btn small" ${g.sheets.length > 1 ? `data-group="${g.id}">ดู ${g.sheets.length} แผ่น` : `data-dl="dxf/${g.id}/0">ดาวน์โหลด DXF`}</button>
        <span class="meta">${esc(g.sheets.map((x) => x.filename).join(", "))} · ${g.part_ids.length} แบบ · ${g.pieces} ชิ้น · ${fmt(g.weight_kg, 2)} กก.</span></div>
      <table class="otable"><thead><tr><th></th><th>ชิ้นส่วน</th><th class="n">ขนาดแผ่นคลี่ (มม.)</th><th class="n">รอยพับ</th><th class="n">รู</th><th class="n">จำนวน</th></tr></thead>
      <tbody>${rows}</tbody><tfoot><tr><td></td><td>รวม ${g.part_ids.length} แบบ</td><td></td><td></td><td></td><td class="qty">${g.pieces}</td></tr></tfoot></table></section>`;
  }).join("");
  const ex = P.parts.filter((p) => !p.include);
  const exHTML = ex.length ? `<section class="ogroup"><div class="ogroup-head"><h3>ไม่นำไปตัด</h3><span class="tb">ท่อ ชิ้นส่วนซื้อ หรือชิ้นที่คลี่ไม่ได้</span><span></span></div>
    <table class="otable"><thead><tr><th></th><th>ชิ้นส่วน</th><th colspan="3">เหตุผล</th><th class="n">จำนวน</th></tr></thead><tbody>
    ${ex.map((p) => `<tr class="click" data-part="${p.id}"><td></td><td>${esc(p.name)}</td><td class="why" colspan="3">${esc(p.reason || "ผู้ใช้เลือกไม่ตัด")}</td><td class="qty">${p.qty}</td></tr>`).join("")}
    </tbody><tfoot><tr><td></td><td>รวม</td><td colspan="3"></td><td class="qty">${s.excluded_pieces}</td></tr></tfoot></table></section>` : "";
  const nfiles = P.groups.reduce((n, g) => n + g.sheets.length, 0);
  const box = $("#overview");
  box.innerHTML = `<article class="order"><header class="order-head"><div><h2>ใบสั่งตัด ${esc(P.job)}</h2><p>${esc(P.source)} · ${new Date().toLocaleDateString("th-TH")} · ${esc(P.profile.name)}</p></div>
    <div class="totals"><div><b>${nfiles}</b><span>ไฟล์ DXF</span></div><div><b>${s.cut_pieces}</b><span>ชิ้นที่ตัด</span></div><div><b>${s.excluded_pieces}</b><span>ไม่ตัด</span></div></div></header>
    ${groups || '<p class="hint">ยังไม่มีชิ้นส่วนที่จะตัด</p>'}${exHTML}</article>`;
  box.querySelectorAll("tr[data-part]").forEach((tr) => tr.addEventListener("click", () => selectPart(tr.dataset.part)));
  box.querySelectorAll("[data-group]").forEach((b) => b.addEventListener("click", () => selectGroup(b.dataset.group)));
  box.querySelectorAll("[data-dl]").forEach((b) => b.addEventListener("click", () => download(b.dataset.dl)));
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
function openProfileDialog() {
  editing = JSON.parse(JSON.stringify(S.profilesData));
  editing.cur = $("#profileSel").value || editing.active;
  renderProfileDialog();
  $("#profileDlg").showModal();
}
function renderProfileDialog() {
  const d = editing, p = d.profiles.find((x) => x.id === d.cur) || d.profiles[0];
  d.cur = p.id;
  $("#profileBody").innerHTML = `
    <div class="row"><label for="pfSel">โหมด</label>
      <select id="pfSel" style="max-width:none;flex:1">${d.profiles.map((x) => `<option value="${esc(x.id)}" ${x.id === p.id ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select>
      <button type="button" class="btn small" id="pfDup">สร้างโหมดใหม่จากโหมดนี้</button>
      <button type="button" class="btn small" id="pfDel" ${d.profiles.length < 2 ? "disabled" : ""}>ลบ</button></div>
    <div class="grid2">
      <label>ชื่อโหมด<input type="text" id="pfName" value="${esc(p.name)}"></label>
      <label>K-factor (รอยพับไม่ฉาก / โค้ง)<input type="number" id="pfK" step="0.01" min="0.05" max="0.99" value="${p.k_factor}"></label>
    </div>
    <h3 style="margin:16px 0 4px">ตารางค่ายืด สำหรับรอยพับ 90°</h3>
    <p class="hint" style="margin:0 0 4px">แผ่นก่อนพับ = ผลรวมขนาดด้านนอกทั้งสองด้าน − ค่ายืด</p>
    <table class="pt"><thead><tr><th>เบอร์</th><th>ความหนา (มม.)</th><th>ค่ายืด (มม.)</th><th></th></tr></thead><tbody id="pfRows">
      ${p.table.map((r, i) => `<tr><td><input type="text" data-r="${i}" data-f="gauge" value="${esc(r.gauge)}"></td><td><input type="number" step="0.01" data-r="${i}" data-f="thickness" value="${r.thickness}"></td><td><input type="number" step="0.01" data-r="${i}" data-f="bd" value="${r.bd}"></td><td><button type="button" class="btn small ghost" data-rm="${i}" aria-label="ลบแถว">ลบ</button></td></tr>`).join("")}
    </tbody></table>
    <button type="button" class="btn small" id="pfAdd">เพิ่มแถว</button>
    <details style="margin-top:16px"><summary>ตั้งค่าขั้นสูง</summary><div class="grid2" style="margin-top:10px">
      <label>มุมที่ถือว่าเป็น 90° (± องศา)<input type="number" step="0.1" id="pfAng" value="${p.angle_tol}"></label>
      <label>ใช้ตารางได้เมื่อ R ใน ≤ (× ความหนา)<input type="number" step="0.5" id="pfRat" value="${p.table_max_ratio}"></label>
      <label>จับคู่ความหนากับตารางได้ ± (มม.)<input type="number" step="0.01" id="pfMatch" value="${p.match_tol}"></label>
      <label class="switch" style="align-self:end"><input type="checkbox" id="pfJog" ${p.jog_use_k ? "checked" : ""}> รอยพับสลับทิศติดกัน (jog) ใช้ K-factor</label>
      <label class="switch" style="grid-column:1/3"><input type="checkbox" id="pfSimp" ${p.simplify_bends !== false ? "checked" : ""}> เส้นตัดโค้งที่พาดผ่านรอยพับ ทำเป็นเส้นตรง (เหมือน Simplify bends ของ SolidWorks)</label></div></details>
    <details style="margin-top:12px"><summary>ชนิดเหล็ก</summary><table class="pt"><thead><tr><th>รหัส</th><th>ชื่อ</th><th>คำนำหน้าชื่อไฟล์</th></tr></thead><tbody>
      ${d.materials.map((m, i) => `<tr><td><input type="text" data-m="${i}" data-f="id" value="${esc(m.id)}"></td><td><input type="text" data-m="${i}" data-f="name" value="${esc(m.name)}"></td><td><input type="text" data-m="${i}" data-f="prefix" value="${esc(m.prefix)}"></td></tr>`).join("")}
    </tbody></table></details>`;
  const body = $("#profileBody");
  $("#pfSel").onchange = (e) => { syncProfile(); d.cur = e.target.value; renderProfileDialog(); };
  $("#pfAdd").onclick = () => { syncProfile(); p.table.push({ gauge: "", thickness: 0, bd: 0 }); renderProfileDialog(); };
  $("#pfDup").onclick = () => { syncProfile(); const n = JSON.parse(JSON.stringify(p)); n.id = "m" + Date.now().toString(36); n.name = p.name + " (สำเนา)"; d.profiles.push(n); d.cur = n.id; renderProfileDialog(); };
  $("#pfDel").onclick = () => { d.profiles = d.profiles.filter((x) => x.id !== p.id); d.cur = d.profiles[0].id; if (d.active === p.id) d.active = d.cur; renderProfileDialog(); };
  body.querySelectorAll("[data-rm]").forEach((b) => (b.onclick = () => { syncProfile(); p.table.splice(+b.dataset.rm, 1); renderProfileDialog(); }));
}
function syncProfile() {
  const d = editing, p = d.profiles.find((x) => x.id === d.cur); if (!p) return;
  p.name = $("#pfName").value.trim() || p.name; p.k_factor = +$("#pfK").value || 0.5;
  $("#pfRows").querySelectorAll("input").forEach((i) => { const r = p.table[+i.dataset.r]; r[i.dataset.f] = i.dataset.f === "gauge" ? i.value.trim() : +i.value; });
  p.angle_tol = +$("#pfAng").value || 1; p.table_max_ratio = +$("#pfRat").value || 3; p.match_tol = +$("#pfMatch").value || 0.06; p.jog_use_k = $("#pfJog").checked; p.simplify_bends = $("#pfSimp").checked;
  $("#profileBody").querySelectorAll("[data-m]").forEach((i) => { d.materials[+i.dataset.m][i.dataset.f] = i.value.trim(); });
}
async function saveProfiles(ev) {
  ev.preventDefault();
  try {
    syncProfile();
    const d = editing; d.active = d.cur;
    const out = { ...d }; delete out.cur;
    S.profilesData = await sendJSON("/api/profiles", "PUT", out);
    $("#profileDlg").close();
    fillProfileSelects();
    if (S.jobId) { await patchSettings({ profile: S.profilesData.active }); $("#profileSel").value = S.profilesData.active; }
    toast("บันทึกตารางค่ายืดแล้ว คำนวณแผ่นคลี่ใหม่ทั้งหมด");
  } catch (e) { $("#profileMsg").textContent = e.message; }
}

/* ======================================================================
   boot
   ====================================================================== */
window.addEventListener("DOMContentLoaded", async () => {
  viewer = new Viewer($("#cv"));
  $("#tFit").onclick = () => viewer.fit();
  $("#tBend").onclick = () => toggleOpt("bend"); $("#tDim").onclick = () => toggleOpt("dim");
  $("#tName").onclick = () => toggleOpt("name"); $("#tMeasure").onclick = () => toggleOpt("measure");
  $("#btnAll").onclick = () => download("all");
  $("#btnXlsx").onclick = () => download("xlsx");
  $("#btnProfile").onclick = openProfileDialog;
  $("#btnNew").onclick = () => { S.jobId = null; S.project = null; S.sel = null; S.cache.clear(); $("#topJob").hidden = true; $("#topActions").hidden = true; $("#file").value = ""; backToLanding(""); };
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
