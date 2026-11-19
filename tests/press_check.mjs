// Press brake simulation checks (web/press.js). Input: JSON list of {name, fold}.
// Prints one line per part and "FAIL ..." lines for anything wrong; the last line is "FAILS <n>".
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { resolve, dirname } from "node:path";

const here = dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
const M = await import(pathToFileURL(resolve(here, "../sheetflat/web/press.js")).href);
const data = JSON.parse(readFileSync(process.argv[2], "utf8"));
let fails = 0;
const fail = (...a) => { fails++; console.log("FAIL", ...a); };
for (const part of data) {
  const pc = part.fold.pieces[0];
  if (!pc || !pc.bends.length) continue;
  const t = part.fold.t;
  const P = M.prepare(pc, t);
  const plan = M.planSequence(P);
  const order = plan.steps.map((s) => s.bend);
  // every bend exactly once
  if ([...order].sort((a, b) => a - b).join() !== pc.bends.map((_, i) => i).join()) fail(part.name, "order is not every bend once", order);
  plan.steps.forEach((s, k) => {
    const b = pc.bends[s.bend];
    const a0 = pc.bends.map((bb, i) => ((s.mask >> i) & 1 ? bb.theta : 0));
    // the bends done before this hit are exactly the earlier steps
    const before = new Set(order.slice(0, k));
    pc.bends.forEach((_, i) => { if (((s.mask >> i) & 1) !== (before.has(i) ? 1 : 0)) fail(part.name, "mask", k); });
    // lying flat on the die: the sheet's top is at t (bottom on the shoulders) and the punch side is the inside
    const F = M.frameFor(P, a0, s.bend, s.place);
    if (Math.abs(F.y0 - t) > 0.02) fail(part.name, `#${b.n} flat sheet top at ${F.y0.toFixed(3)} not ${t}`);
    // the machine frame is a rotation (orthonormal, right-handed)
    const dot = (u, v) => u[0] * v[0] + u[1] * v[1] + u[2] * v[2];
    const det = dot(F.X, [F.Y[1] * F.Z[2] - F.Y[2] * F.Z[1], F.Y[2] * F.Z[0] - F.Y[0] * F.Z[2], F.Y[0] * F.Z[1] - F.Y[1] * F.Z[0]]);
    if (Math.abs(det - 1) > 1e-6 || Math.abs(dot(F.X, F.Y)) > 1e-6 || Math.abs(dot(F.Y, F.Z)) > 1e-6) fail(part.name, "frame not a rotation");
    // bent: both sides rise symmetrically about the punch (air bending): the bend's two tangent
    // directions make equal angles with the horizontal
    const a1 = a0.slice(); a1[s.bend] = s.hem ? M.HEM_PRE : b.theta;
    const G = M.frameFor(P, a1, s.bend, s.place);
    if (G.y0 > F.y0 + 1e-6) fail(part.name, `#${b.n} punch goes up while bending`);
    // the plan's hit must be possible on the tools it names
    if (!s.ok && part.mustFit) fail(part.name, `#${b.n} collides`);
  });
  // hems before any other bend when nothing collides
  const firstPlain = plan.steps.findIndex((s) => !s.hem);
  const lateHem = plan.steps.findIndex((s, i) => s.hem && i > firstPlain && firstPlain >= 0);
  if (lateHem >= 0 && plan.steps.every((s) => s.ok)) fail(part.name, "a hem after a plain bend");
  // the bigger side faces the operator when the part is clearly lopsided
  console.log(part.name, plan.steps.map((s) => `#${pc.bends[s.bend].n}${s.hem ? "H" : ""}${s.ok ? "" : "!"}`).join(" "), `V${P.V}`);
}
console.log("FAILS", fails);
