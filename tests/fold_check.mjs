// Fold every part completely with the browser's own code (web/fold.js) and compare its size with the
// real 3D model. Input: JSON list of {name, fold, real}; prints the worst difference (mm) on the last line.
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { resolve, dirname } from "node:path";

const here = dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
const { mapPoint, anglesAt } = await import(pathToFileURL(resolve(here, "../sheetflat/web/fold.js")).href);
const data = JSON.parse(readFileSync(process.argv[2], "utf8"));
let worst = 0;
for (const part of data) {
  for (const P of part.fold.pieces.slice(0, 1)) {
    const h = part.fold.t / 2;
    const order = P.bends.map((b, i) => i).sort((a, b) => P.bends[a].n - P.bends[b].n);
    const ang = anglesAt(P.bends, P.bends.length, order);
    const lo = [1e9, 1e9, 1e9], hi = [-1e9, -1e9, -1e9];
    for (const wl of P.walls) wl.pts.forEach(([x, y], i) => {
      for (const w of [h, -h]) {
        const p = mapPoint(x, y, w, P.regions[wl.reg[i]], P.bends, ang);
        for (let k = 0; k < 3; k++) { lo[k] = Math.min(lo[k], p[k]); hi[k] = Math.max(hi[k], p[k]); }
      }
    });
    // every drawing line must follow the folded sheet: a line drawn as a chord across a bend comes out
    // shorter than its real length (measured at mid-thickness; bend zones bend, they do not shrink)
    for (const q of P.prims) {
      const pts = q.pts.map(([x, y], i) => mapPoint(x, y, 0, P.regions[q.reg[i]], P.bends, ang));
      let L = 0;
      for (let i = 1; i < pts.length; i++) L += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1], pts[i][2] - pts[i - 1][2]);
      if (q.t !== "circle" && L < q.len * 0.97) { console.log("CHORD", part.name, q.t, q.len.toFixed(2), "->", L.toFixed(2)); worst = Math.max(worst, 99); }
    }
    const got = [0, 1, 2].map((k) => hi[k] - lo[k]).sort((a, b) => a - b);
    const diff = Math.max(...got.map((v, k) => Math.abs(v - part.real[k])));
    worst = Math.max(worst, diff);
    console.log(part.name, got.map((v) => v.toFixed(2)).join(" x "), "real", part.real.join(" x "), "diff", diff.toFixed(3));
  }
}
console.log("WORST", worst.toFixed(4));
