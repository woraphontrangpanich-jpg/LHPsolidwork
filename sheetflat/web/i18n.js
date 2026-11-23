/* Language switch: Thai (the program's own language) <-> English.

   The program is written in Thai. In English mode every Thai phrase that appears on the page is replaced by its English
   phrase from i18n-en.js (window.SF_EN: Thai phrase -> English phrase, longest phrase first), in text, in tooltips /
   placeholders / aria labels, and in the text drawn on canvases (2D drawing, 3D labels). Numbers, names and codes are
   left as they are, so texts with numbers in them translate phrase by phrase. A phrase that is not in the dictionary
   simply stays Thai. Switching back to Thai puts the original texts back.

   window.SF_LANG = "th" | "en"; window.SF_setLang(lang); window.SF_tr(text). The choice is remembered (localStorage)
   and can be given in the address: ?lang=en                                                                     */
(() => {
  const EN = window.SF_EN || {};
  const THAI = /[฀-๿]/;
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const keys = Object.keys(EN).sort((a, b) => b.length - a.length);
  const RE = keys.length ? new RegExp(keys.map(esc).join("|"), "g") : null;
  const cache = new Map();
  const ATTRS = ["title", "placeholder", "aria-label", "alt"];
  const orig = new WeakMap();                  // text node -> its Thai text
  let lang = "th";
  let busy = false;

  function pick() {
    let l = "";
    try { l = new URLSearchParams(location.search).get("lang") || localStorage.getItem("sf_lang") || ""; } catch (_) { /* private mode */ }
    return l === "en" ? "en" : "th";
  }

  // Thai puts the counting word after the number ("พับ 3 จุด" = bend 3 points): put the English in English order
  const pl = (n, one, many) => `${n} ${n === "1" ? one : many}`;
  const FIX = [
    [/\bbends (\d+) points\b/g, (m, n) => pl(n, "bend", "bends")],
    [/\bin the assembly (\d+) parts · faces (\d+) faces\b/g, (m, a, b) => `${pl(a, "part", "parts")} in the assembly · ${b} faces`],
    [/\btotal (\d+) designs?\b/g, (m, n) => `total ${pl(n, "design", "designs")}`],
    [/\bTable (\d+) rows?\b/g, (m, n) => `Table: ${pl(n, "row", "rows")}`],
    [/\bChoose (\d+) designs?\b/g, (m, n) => `${pl(n, "design", "designs")} selected`],
    [/\bthick ([\d.,]+) mm\b/g, "$1 mm thick"],
    [/(^|[^\d.,])1 (parts|designs|bends|rows|holes|files|checks|sheets|points|sets)\b/g, (m, a, w) => `${a}1 ${w.slice(0, -1)}`],
  ];
  function tr(s) {
    if (lang !== "en" || !RE || typeof s !== "string" || !THAI.test(s)) return s;
    let r = cache.get(s);
    if (r === undefined) {
      r = s.replace(RE, (m) => EN[m]);
      for (const [re, to] of FIX) r = r.replace(re, to);
      if (cache.size > 5000) cache.clear();
      cache.set(s, r);
    }
    return r;
  }

  function textNode(n) {
    const cur = n.nodeValue;
    if (!cur || !THAI.test(cur)) return;
    const out = tr(cur);
    if (out !== cur) { orig.set(n, cur); n.nodeValue = out; }
  }
  function attrs(el) {
    for (const a of ATTRS) {
      const v = el.getAttribute(a);
      if (v && THAI.test(v)) {
        const out = tr(v);
        if (out !== v) { el.setAttribute("data-th-" + a, v); el.setAttribute(a, out); }
      }
    }
  }
  function walk(root) {
    if (root.nodeType === 3) { textNode(root); return; }
    if (root.nodeType !== 1 && root.nodeType !== 9 && root.nodeType !== 11) return;
    if (root.nodeType === 1) {
      const tag = root.tagName;
      if (tag === "SCRIPT" || tag === "STYLE") return;
      attrs(root);
    }
    const tw = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.nodeType === 1 && (n.tagName === "SCRIPT" || n.tagName === "STYLE") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
    });
    let n;
    while ((n = tw.nextNode())) { if (n.nodeType === 3) textNode(n); else attrs(n); }
  }
  function restore() {
    const tw = document.createTreeWalker(document.documentElement, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = tw.nextNode())) { const o = orig.get(n); if (o !== undefined) { n.nodeValue = o; orig.delete(n); } }
    for (const a of ATTRS) {
      document.querySelectorAll("[data-th-" + a + "]").forEach((el) => { el.setAttribute(a, el.getAttribute("data-th-" + a)); el.removeAttribute("data-th-" + a); });
    }
  }

  const mo = new MutationObserver((records) => {
    if (lang !== "en" || busy) return;
    busy = true;
    try {
      for (const r of records) {
        if (r.type === "characterData") textNode(r.target);
        else if (r.type === "attributes") { if (!r.attributeName.startsWith("data-th-")) attrs(r.target); }
        else r.addedNodes.forEach((n) => walk(n));
      }
    } finally { mo.takeRecords(); busy = false; }
  });
  function observe() {
    mo.observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  }

  function setLang(l, save = true) {
    l = l === "en" ? "en" : "th";
    if (save) { try { localStorage.setItem("sf_lang", l); } catch (_) { /* ignore */ } }
    if (l === lang && document.documentElement.lang === (l === "en" ? "en" : "th")) { label(); return; }
    busy = true;
    try {
      if (lang === "en") restore();
      lang = l;
      window.SF_LANG = l;
      document.documentElement.lang = l;
      if (l === "en") walk(document.documentElement);
    } finally { mo.takeRecords(); busy = false; }
    label();
    window.dispatchEvent(new CustomEvent("sf-lang", { detail: l }));
  }
  function label() {
    document.querySelectorAll("[data-lang-pick]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.langPick === lang)));
  }

  // text drawn on canvases (2D drawing, labels over the 3D view) and native dialogs
  const P = window.CanvasRenderingContext2D && CanvasRenderingContext2D.prototype;
  if (P) {
    for (const m of ["fillText", "strokeText", "measureText"]) {
      const o = P[m];
      P[m] = function (t, ...a) { return o.call(this, typeof t === "string" ? tr(t) : t, ...a); };
    }
  }
  for (const m of ["alert", "confirm", "prompt"]) {
    const o = window[m];
    window[m] = (msg, ...a) => o.call(window, tr(String(msg)), ...a);
  }

  window.SF_tr = tr;
  window.SF_LANG = "th";
  window.SF_setLang = setLang;
  const init = () => {
    observe();
    document.querySelectorAll("[data-lang-pick]").forEach((b) => b.addEventListener("click", () => setLang(b.dataset.langPick)));
    setLang(pick(), false);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
