"""Bend allowance model.

Two ways of getting the flat length of a bend zone (both are expressed as the
width ``BA`` of the strip that replaces the bend between its two tangent
lines):

* **Bend deduction table** (shop table, 90 deg bends with a small radius).
  The shop measures *outside* dimensions of a bent part, so for a 90 deg bend
  with outside flange lengths a and b the blank is ``a + b - BD``.  With the
  tangent lines at ``r_in + t`` from the outside virtual sharp (90 deg)::

      BA = 2 * (r_in + t) - BD

  The result depends only on the outside dimensions and BD, not on r_in.

* **K-factor** (any other angle / large radius arcs)::

      BA = theta * (r_in + K * t)         (theta in radians)
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class BendModel:
    thickness: float
    k_factor: float = 0.5
    bd90: float | None = None             # table value for this thickness (None -> K-factor only)
    angle_tol_deg: float = 1.0            # |angle-90| <= tol  -> counts as "right angle"
    table_max_ratio: float = 3.0          # r_in/t above this -> treated as a curved bend (K-factor)
    jog_use_k: bool = True                # two opposite bends separated by a very short flat (jog/step) -> K-factor
    jog_max_flat_ratio: float = 2.0       # "very short flat" = distance between tangent lines <= ratio * t

    def allowance(self, theta: float, r_in: float, force_k: bool = False):
        """Return (ba, method, bd, k) for a bend of angle ``theta`` (rad) and inner radius ``r_in``."""
        t = self.thickness
        right = abs(math.degrees(theta) - 90.0) <= self.angle_tol_deg
        if self.bd90 is not None and right and not force_k and r_in <= self.table_max_ratio * t + 1e-9:
            ba = 2.0 * (r_in + t) - self.bd90
            if ba > 0:
                return ba, "table", self.bd90, None
        ba = theta * (r_in + self.k_factor * t)
        return ba, "k", None, self.k_factor
