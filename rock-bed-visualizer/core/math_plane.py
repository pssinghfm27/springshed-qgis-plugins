"""
Rock-bed plane mathematics.

Implements the formula derived in:
  "Visualizing Strikes and Dips of Rock Beds in QGIS with qgis2threejs"
  (CHIRAG / Dotiyalgaon, 2018 — Peepalkhola spring case study).

The paper derives the plane equation from the cross-product of the strike
and dip direction unit vectors. For any point (x', y') measured relative
to the origin at (0,0,0) on the plane, z_rel is:

        x'·cos(θ)·sin(β) + y'·sin(θ)·sin(β)
z_rel = ──────────────────────────────────────────────
        sin(α)·cos(θ)·cos(β) − sin(θ)·cos(α)·cos(β)

where:
  θ = strike direction (degrees clockwise from N)
  α = dip direction   (degrees clockwise from N)
  β = dip amount      (degrees from horizontal)

The denominator vanishes when the strike direction and dip direction are
the same (or exactly opposite) — a geometrically impossible bed. We return
None in that case and the UI layer turns it into a user-facing warning.

Coordinate convention (from paper):
  + y axis = magnetic north
  + x axis = east
  angles measured clockwise from +y (north).
"""
from __future__ import annotations

import math
from typing import Optional, Tuple


def plane_z_offset(x_rel: float, y_rel: float,
                   strike_deg: float, dip_dir_deg: float,
                   dip_amount_deg: float) -> Optional[float]:
    """
    Compute z_rel (elevation difference from origin) on the projected
    rock-bed plane at the point (x_rel, y_rel), which is measured in
    projected-CRS linear units (metres for UTM / EPSG:3857) relative
    to the origin.

    Returns None if the denominator is effectively zero.
    """
    theta = math.radians(strike_deg)
    alpha = math.radians(dip_dir_deg)
    beta  = math.radians(dip_amount_deg)

    sin_theta, cos_theta = math.sin(theta), math.cos(theta)
    sin_alpha, cos_alpha = math.sin(alpha), math.cos(alpha)
    sin_beta, cos_beta   = math.sin(beta),  math.cos(beta)

    numerator = x_rel * cos_theta * sin_beta + y_rel * sin_theta * sin_beta
    denom     = sin_alpha * cos_theta * cos_beta - sin_theta * cos_alpha * cos_beta

    if abs(denom) < 1e-12:
        return None
    return numerator / denom


def is_degenerate(strike_deg: float, dip_dir_deg: float,
                  dip_amount_deg: float) -> bool:
    """
    Return True if the angles describe a degenerate or invalid bed:
      - dip amount 0 (horizontal bed — valid geometrically but the plane
        is trivially horizontal so any grid gives z_rel=0 everywhere)
      - strike == dip direction (mod 180°) — denominator vanishes
    """
    if dip_amount_deg <= 0.0:
        return True
    diff = (strike_deg - dip_dir_deg) % 180.0
    return diff < 0.5 or diff > 179.5


def plane_normal(strike_deg: float, dip_dir_deg: float,
                 dip_amount_deg: float) -> Tuple[float, float, float]:
    """
    Return the unit normal vector (nx, ny, nz) of the plane. Mostly
    useful for diagnostic display — "the bed tilts in this direction".

    Derivation is identical to the paper: N = strike_hat × dip_hat.
    """
    theta = math.radians(strike_deg)
    alpha = math.radians(dip_dir_deg)
    beta  = math.radians(dip_amount_deg)

    # Strike unit vector (purely horizontal)
    sx, sy, sz = math.sin(theta), math.cos(theta), 0.0
    # Dip unit vector (horizontal component cos β, vertical sin β)
    dx = math.sin(alpha) * math.cos(beta)
    dy = math.cos(alpha) * math.cos(beta)
    dz = math.sin(beta)

    # Cross product
    nx = sy * dz - sz * dy
    ny = sz * dx - sx * dz
    nz = sx * dy - sy * dx

    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return (nx / length, ny / length, nz / length)


# ---------------------------------------------------------------------------
# Self-tests — run this file directly to verify math against the paper.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Reference: paper uses SD=70, DD=160, DA=40 for Peepalkhola.
    z = plane_z_offset(0, 0, 70, 160, 40)
    assert z == 0.0, f"At origin must be 0, got {z}"

    # 1000 m east, 0 m north — a concrete non-zero expected value
    z = plane_z_offset(1000, 0, 70, 160, 40)
    assert 280 < z < 290, f"expected ~287, got {z}"

    # Horizontal bed: z must be 0 everywhere
    assert abs(plane_z_offset(1000, 2000, 45, 90, 0.00001)) < 1e-3

    # Degenerate: strike == dip_dir
    assert plane_z_offset(100, 100, 45, 45, 30) is None
    assert is_degenerate(45, 45, 30)
    assert is_degenerate(0, 180, 30)  # exactly opposite also degenerate
    assert not is_degenerate(70, 160, 40)

    # Normal vector: for a near-horizontal bed, normal is ~±(0,0,1)
    nx, ny, nz = plane_normal(0, 90, 0.00001)
    assert abs(abs(nz) - 1.0) < 1e-3, f"horizontal bed normal should be ±z, got ({nx},{ny},{nz})"

    print("All math self-tests passed.")
