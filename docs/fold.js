/* Fold kinematics of a flat piece (no three.js here, so it can be tested on its own).

   Every bend has a frame in the flat: start = the tangent line on its parent's side, d = across the bend
   zone (0..ba), ax = along the bend, n = +Z. Folding by angle a (0..theta):
     point in the zone at u   -> on an arc of radius ba/a, at angle u/(ba/a)
     point beyond the zone    -> carried rigidly, turned by a
   A point is mapped by the bends between it and the root flange, deepest first.
   s = +1 folds towards +Z ("พับขึ้น", the side the 2D drawing is seen from), -1 away. */

export function applyBend(p, b, a, strip) {
  if (a < 1e-9) return p;
  // w is measured from the bend's neutral layer (height wn in the flat: a table bend does not bend
  // around the middle of the sheet)
  const wn = b.wn || 0;
  const rx = p[0] - b.start[0], ry = p[1] - b.start[1], w = p[2] - wn;
  const u = rx * b.d[0] + ry * b.d[1], v = rx * b.ax[0] + ry * b.ax[1];
  const rho = b.ba / a, s = b.s;
  let beta, extra;
  if (strip) { const uc = Math.min(Math.max(u, 0), b.ba); beta = uc / rho; extra = u - uc; }
  else { beta = a; extra = u - b.ba; }
  const sb = Math.sin(beta), cb = Math.cos(beta);
  // arc point + extra along the tangent T = cb*d + s*sb*n, + w along the normal N = -s*sb*d + cb*n
  const alongD = rho * sb + extra * cb - w * s * sb;
  const alongN = s * rho * (1 - cb) + extra * s * sb + w * cb;
  return [b.start[0] + v * b.ax[0] + alongD * b.d[0], b.start[1] + v * b.ax[1] + alongD * b.d[1], alongN + wn];
}

/* angles[i] = current fold angle (rad) of bend i; region = {kind, bend, chain} */
export function mapPoint(x, y, w, region, bends, angles) {
  let p = [x, y, w];
  const chain = region.chain;
  for (let i = 0; i < chain.length; i++) {
    const j = chain[i];
    p = applyBend(p, bends[j], angles[j], i === 0 && region.kind === "bend" && region.bend === j);
  }
  return p;
}

/* fold angles for an animation progress T in [0, bends.length]: bend k (in table order) folds during [k, k+1] */
export function anglesAt(bends, T, order) {
  const out = new Array(bends.length).fill(0);
  order.forEach((j, k) => { out[j] = bends[j].theta * Math.min(1, Math.max(0, T - k)); });
  return out;
}
