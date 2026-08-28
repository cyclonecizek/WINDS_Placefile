#!/usr/bin/env python3
"""
Create a GRLevelX contour placefile of low-level divergence/convergence
from the already-generated KSC 54-ft WINDS station placefile.

Input:
    docs/ksc_winds_54ft.txt

Outputs:
    docs/ksc_winds_54ft_barnes_divergence.txt
    docs/ksc_winds_54ft_barnes_divergence.json

Method:
  1. Parse each 54-ft station's average wind from the hover text.
  2. Convert meteorological direction/speed to Cartesian u/v in m/s.
  3. Perform a two-pass Barnes objective analysis of u and v.
  4. Compute horizontal divergence du/dx + dv/dy.
  5. Extract contour line segments with marching squares.
  6. Write GRLevelX Line: contours.

Negative divergence = convergence (warm colors).
Positive divergence = divergence (cool colors).

This is a visualization/analysis aid, not an official KSC/45 WS product.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

EARTH_RADIUS_M = 6371000.0
KT_TO_MPS = 0.5144444444444445

# Defaults tuned for the spatial scale of the KSC tower network.
GRID_SPACING_M = float(os.getenv("BARNES_GRID_SPACING_M", "500"))
BARNES_LENGTH_KM = float(os.getenv("BARNES_LENGTH_KM", "8"))
BARNES_GAMMA = float(os.getenv("BARNES_GAMMA", "0.30"))
MAX_INFLUENCE_KM = float(os.getenv("BARNES_MAX_INFLUENCE_KM", "20"))
NEAREST_STATION_KM = float(os.getenv("BARNES_NEAREST_STATION_KM", "9"))
MIN_NEARBY_STATIONS = int(os.getenv("BARNES_MIN_NEARBY_STATIONS", "3"))

# Contour values are displayed in 10^-4 s^-1.
# One-unit spacing creates the dense contour appearance used by the
# operational-style display. Stronger / even-numbered contours are thicker.
def contour_style(level: float):
    """Return (RGB, line_width) for a divergence contour."""
    a = abs(level)

    if level < 0:
        # Convergence: yellow/orange -> red as magnitude increases.
        if a >= 10:
            color = (205, 20, 25)
        elif a >= 8:
            color = (245, 40, 35)
        elif a >= 6:
            color = (255, 85, 25)
        elif a >= 4:
            color = (255, 135, 20)
        elif a >= 2:
            color = (255, 190, 20)
        else:
            color = (255, 220, 75)
    elif level > 0:
        # Divergence: pale cyan -> deep blue as magnitude increases.
        if a >= 10:
            color = (40, 65, 210)
        elif a >= 8:
            color = (35, 105, 245)
        elif a >= 6:
            color = (25, 155, 255)
        elif a >= 4:
            color = (25, 205, 245)
        elif a >= 2:
            color = (75, 225, 235)
        else:
            color = (155, 240, 235)
    else:
        color = (245, 245, 245)

    if level == 0:
        width = 2
    elif a >= 8:
        width = 3
    elif int(round(a)) % 2 == 0:
        width = 2
    else:
        width = 1

    return color, width


CONTOUR_LEVELS = [float(v) for v in range(-12, 13)]


@dataclass
class Station:
    name: str
    lat: float
    lon: float
    direction_deg: float
    speed_kt: float
    obs_text: str | None = None
    x: float = 0.0
    y: float = 0.0
    u: float = 0.0
    v: float = 0.0


def parse_54ft_placefile(text: str) -> list[Station]:
    """Parse current KSC WINDS Object/Icon station plots."""
    stations: list[Station] = []
    current_lat = current_lon = None

    for raw in text.splitlines():
        line = raw.strip()

        if line.startswith("Object:"):
            m = re.match(
                r"Object:\s*([-+]?\d+(?:\.\d+)?),\s*([-+]?\d+(?:\.\d+)?)",
                line,
            )
            if m:
                current_lat = float(m.group(1))
                current_lon = float(m.group(2))
            continue

        if current_lat is None or current_lon is None:
            continue

        if line.startswith("Icon:"):
            # Exact average wind comes from hover text.
            mw = re.search(
                r"Average Wind:\s*([0-9]{1,3}(?:\.\d+)?)\s*deg\s*@\s*"
                r"([0-9]+(?:\.\d+)?)\s*kt",
                line,
                re.IGNORECASE,
            )
            if not mw:
                continue

            md = re.search(r"Tower\s+(\d{4})", line, re.IGNORECASE)
            name = md.group(1) if md else f"{current_lat:.4f},{current_lon:.4f}"

            mo = re.search(
                r"Observation:\s*([0-9]{4}-[0-9]{2}-[0-9]{2}\s+"
                r"[0-9]{2}:[0-9]{2}Z)",
                line,
            )
            obs_text = mo.group(1) if mo else None

            direction = float(mw.group(1)) % 360.0
            speed_kt = float(mw.group(2))

            # Meteorological FROM direction -> Cartesian vector TO direction.
            d = math.radians(direction)
            speed_mps = speed_kt * KT_TO_MPS
            u = -speed_mps * math.sin(d)  # eastward
            v = -speed_mps * math.cos(d)  # northward

            stations.append(
                Station(
                    name=name,
                    lat=current_lat,
                    lon=current_lon,
                    direction_deg=direction,
                    speed_kt=speed_kt,
                    obs_text=obs_text,
                    u=u,
                    v=v,
                )
            )

    # De-duplicate repeated station blocks, retaining the last occurrence.
    dedup = {}
    for s in stations:
        dedup[(round(s.lat, 6), round(s.lon, 6))] = s
    return list(dedup.values())


def project_stations(stations: list[Station]):
    lat0 = sum(s.lat for s in stations) / len(stations)
    lon0 = sum(s.lon for s in stations) / len(stations)
    coslat = math.cos(math.radians(lat0))

    for s in stations:
        s.x = EARTH_RADIUS_M * math.radians(s.lon - lon0) * coslat
        s.y = EARTH_RADIUS_M * math.radians(s.lat - lat0)

    return lat0, lon0, coslat


def xy_to_latlon(x: float, y: float, lat0: float, lon0: float, coslat: float):
    lat = lat0 + math.degrees(y / EARTH_RADIUS_M)
    lon = lon0 + math.degrees(x / (EARTH_RADIUS_M * coslat))
    return lat, lon


def convex_hull(points):
    """Monotonic-chain convex hull for (x, y) points."""
    pts = sorted(set((float(x), float(y)) for x, y in points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    return lower[:-1] + upper[:-1]


def point_in_polygon(x, y, poly):
    if len(poly) < 3:
        return False
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)):
            xcross = (xj-xi) * (y-yi) / ((yj-yi) or 1e-12) + xi
            if x < xcross:
                inside = not inside
        j = i
    return inside


def weighted_analysis(x, y, stations, attr, kappa, cutoff_m):
    num = den = 0.0
    nearby = 0
    nearest = float("inf")

    for s in stations:
        dx = x - s.x
        dy = y - s.y
        r2 = dx*dx + dy*dy
        r = math.sqrt(r2)
        nearest = min(nearest, r)
        if r <= cutoff_m:
            nearby += 1
            w = math.exp(-r2 / kappa)
            num += w * getattr(s, attr)
            den += w

    return (num / den if den > 0 else None), nearby, nearest


def two_pass_barnes(stations, xs, ys):
    kappa = (BARNES_LENGTH_KM * 1000.0) ** 2
    cutoff = MAX_INFLUENCE_KM * 1000.0
    hull = convex_hull([(s.x, s.y) for s in stations])

    # First-pass values at station positions, used to form residuals.
    residual_u = {}
    residual_v = {}
    for idx, s in enumerate(stations):
        gu, _, _ = weighted_analysis(s.x, s.y, stations, "u", kappa, cutoff)
        gv, _, _ = weighted_analysis(s.x, s.y, stations, "v", kappa, cutoff)
        residual_u[idx] = s.u - (gu if gu is not None else s.u)
        residual_v[idx] = s.v - (gv if gv is not None else s.v)

    # Temporary pseudo-attributes for residual correction.
    for idx, s in enumerate(stations):
        s._res_u = residual_u[idx]
        s._res_v = residual_v[idx]

    kappa2 = max(kappa * BARNES_GAMMA, 1.0)

    ugrid = [[None for _ in xs] for _ in ys]
    vgrid = [[None for _ in xs] for _ in ys]
    mask = [[False for _ in xs] for _ in ys]

    for j, y in enumerate(ys):
        for i, x in enumerate(xs):
            if not point_in_polygon(x, y, hull):
                continue

            first_u, nearby, nearest = weighted_analysis(
                x, y, stations, "u", kappa, cutoff
            )
            first_v, _, _ = weighted_analysis(
                x, y, stations, "v", kappa, cutoff
            )

            if (
                first_u is None
                or first_v is None
                or nearby < MIN_NEARBY_STATIONS
                or nearest > NEAREST_STATION_KM * 1000.0
            ):
                continue

            corr_u, _, _ = weighted_analysis(
                x, y, stations, "_res_u", kappa2, cutoff
            )
            corr_v, _, _ = weighted_analysis(
                x, y, stations, "_res_v", kappa2, cutoff
            )

            ugrid[j][i] = first_u + (corr_u or 0.0)
            vgrid[j][i] = first_v + (corr_v or 0.0)
            mask[j][i] = True

    return ugrid, vgrid, mask


def compute_divergence(ugrid, vgrid, mask, dx, dy):
    ny = len(ugrid)
    nx = len(ugrid[0])
    div = [[None for _ in range(nx)] for _ in range(ny)]

    # Central differences only; requiring all four neighbors suppresses
    # one-cell edge artifacts around the analysis mask.
    for j in range(1, ny-1):
        for i in range(1, nx-1):
            if not (
                mask[j][i]
                and mask[j][i-1]
                and mask[j][i+1]
                and mask[j-1][i]
                and mask[j+1][i]
            ):
                continue

            dudx = (ugrid[j][i+1] - ugrid[j][i-1]) / (2.0 * dx)
            dvdy = (vgrid[j+1][i] - vgrid[j-1][i]) / (2.0 * dy)
            div[j][i] = (dudx + dvdy) * 1.0e4  # display units 10^-4 s^-1

    return div


# Marching-squares edge IDs:
# 0 bottom, 1 right, 2 top, 3 left.
CASE_SEGMENTS = {
    0:  [],
    1:  [(3, 0)],
    2:  [(0, 1)],
    3:  [(3, 1)],
    4:  [(1, 2)],
    5:  [(3, 2), (0, 1)],
    6:  [(0, 2)],
    7:  [(3, 2)],
    8:  [(2, 3)],
    9:  [(0, 2)],
    10: [(0, 3), (1, 2)],
    11: [(1, 2)],
    12: [(1, 3)],
    13: [(0, 1)],
    14: [(3, 0)],
    15: [],
}


def interp_edge(edge, x0, x1, y0, y1, vals, level):
    # vals = bottom-left, bottom-right, top-right, top-left
    bl, br, tr, tl = vals

    def frac(a, b):
        if b == a:
            return 0.5
        return max(0.0, min(1.0, (level - a) / (b - a)))

    if edge == 0:  # bottom
        t = frac(bl, br)
        return x0 + t*(x1-x0), y0
    if edge == 1:  # right
        t = frac(br, tr)
        return x1, y0 + t*(y1-y0)
    if edge == 2:  # top
        t = frac(tl, tr)
        return x0 + t*(x1-x0), y1
    # left
    t = frac(bl, tl)
    return x0, y0 + t*(y1-y0)


def contour_segments(div, xs, ys, level):
    segments = []
    for j in range(len(ys)-1):
        for i in range(len(xs)-1):
            # bottom-left, bottom-right, top-right, top-left
            vals = (
                div[j][i],
                div[j][i+1],
                div[j+1][i+1],
                div[j+1][i],
            )
            if any(v is None for v in vals):
                continue

            case = 0
            if vals[0] >= level: case |= 1
            if vals[1] >= level: case |= 2
            if vals[2] >= level: case |= 4
            if vals[3] >= level: case |= 8

            # Resolve saddle ambiguity (cases 5/10) using cell-center value.
            segdef = CASE_SEGMENTS[case]
            if case in (5, 10):
                center = sum(vals) / 4.0
                if case == 5:
                    segdef = [(3,0),(1,2)] if center >= level else [(3,2),(0,1)]
                else:
                    segdef = [(0,1),(2,3)] if center >= level else [(0,3),(1,2)]

            for ea, eb in segdef:
                a = interp_edge(ea, xs[i], xs[i+1], ys[j], ys[j+1], vals, level)
                b = interp_edge(eb, xs[i], xs[i+1], ys[j], ys[j+1], vals, level)
                segments.append((a, b))
    return segments



def _point_key(p, tolerance_m=1.0):
    """Quantized key used to join marching-squares segments."""
    return (
        int(round(p[0] / tolerance_m)),
        int(round(p[1] / tolerance_m)),
    )


def stitch_segments(segments):
    """
    Join two-point marching-squares segments into continuous polylines.

    GR draws these much more cleanly than hundreds of separate 2-point Line
    objects, and the result resembles traditional analyzed weather contours.
    """
    if not segments:
        return []

    adjacency = {}
    points = {}

    for idx, (a, b) in enumerate(segments):
        ka = _point_key(a)
        kb = _point_key(b)
        points.setdefault(ka, a)
        points.setdefault(kb, b)
        adjacency.setdefault(ka, []).append((idx, kb))
        adjacency.setdefault(kb, []).append((idx, ka))

    used = set()
    polylines = []

    # Start open contours at degree-1 endpoints first, then handle loops.
    starts = [k for k, links in adjacency.items() if len(links) == 1]
    starts += [k for k in adjacency if k not in starts]

    for start in starts:
        available = [item for item in adjacency[start] if item[0] not in used]
        while available:
            line = [points[start]]
            current = start
            previous = None

            while True:
                choices = [
                    item for item in adjacency[current]
                    if item[0] not in used
                ]
                if not choices:
                    break

                # Prefer not to immediately reverse direction at junctions.
                chosen = choices[0]
                if previous is not None and len(choices) > 1:
                    for item in choices:
                        if item[1] != previous:
                            chosen = item
                            break

                seg_idx, nxt = chosen
                used.add(seg_idx)
                line.append(points[nxt])
                previous, current = current, nxt

                if current == start:
                    break

            if len(line) >= 2:
                polylines.append(line)

            available = [item for item in adjacency[start] if item[0] not in used]

    return polylines


def chaikin_smooth(points, iterations=1):
    """
    Light contour smoothing. Endpoints are preserved for open lines.
    Closed contours remain closed.
    """
    if len(points) < 4 or iterations <= 0:
        return points

    closed = _point_key(points[0]) == _point_key(points[-1])
    pts = points[:]

    for _ in range(iterations):
        if len(pts) < 4:
            break

        out = []
        if not closed:
            out.append(pts[0])

        pairs = list(zip(pts[:-1], pts[1:]))
        for p, q in pairs:
            q1 = (0.75*p[0] + 0.25*q[0], 0.75*p[1] + 0.25*q[1])
            q2 = (0.25*p[0] + 0.75*q[0], 0.25*p[1] + 0.75*q[1])
            out.extend([q1, q2])

        if not closed:
            out.append(pts[-1])
        elif out:
            out.append(out[0])

        pts = out

    return pts

def contour_hover(level):
    if level < 0:
        return (
            "KSC 54-ft Barnes Wind Analysis\\n"
            f"Convergence: {abs(level):.1f} x 10^-4 s^-1\\n"
            "Warm colors = convergence\\n"
            "Derived from KSC 54-ft tower winds"
        )
    if level > 0:
        return (
            "KSC 54-ft Barnes Wind Analysis\\n"
            f"Divergence: {level:.1f} x 10^-4 s^-1\\n"
            "Cool colors = divergence\\n"
            "Derived from KSC 54-ft tower winds"
        )
    return (
        "KSC 54-ft Barnes Wind Analysis\\n"
        "Zero divergence contour\\n"
        "Derived from KSC 54-ft tower winds"
    )


def build_placefile(stations):
    if len(stations) < 5:
        return (
            "; KSC 54-ft Barnes Divergence / Convergence\n"
            "RefreshSeconds: 60\n"
            "Threshold: 200\n"
            f"; Insufficient 54-ft stations for Barnes analysis: {len(stations)}\n"
        ), {
            "stations_used": len(stations),
            "status": "insufficient_stations",
        }

    lat0, lon0, coslat = project_stations(stations)

    minx = min(s.x for s in stations)
    maxx = max(s.x for s in stations)
    miny = min(s.y for s in stations)
    maxy = max(s.y for s in stations)

    # Grid spans the tower-network bounding box. The objective field is later
    # clipped to the station convex hull and local-support criteria.
    nx = max(3, int(math.ceil((maxx-minx)/GRID_SPACING_M)) + 1)
    ny = max(3, int(math.ceil((maxy-miny)/GRID_SPACING_M)) + 1)
    xs = [minx + i*GRID_SPACING_M for i in range(nx)]
    ys = [miny + j*GRID_SPACING_M for j in range(ny)]

    ugrid, vgrid, mask = two_pass_barnes(stations, xs, ys)
    div = compute_divergence(
        ugrid, vgrid, mask, GRID_SPACING_M, GRID_SPACING_M
    )

    obs_times = sorted(set(s.obs_text for s in stations if s.obs_text))
    obs_summary = obs_times[-1] if obs_times else "unknown"

    lines = [
        "; KSC 54-ft Barnes Divergence / Convergence",
        "RefreshSeconds: 60",
        "Threshold: 200",
        "; Derived from KSC WINDS 54-ft average winds.",
        "; Negative divergence = convergence (warm colors).",
        "; Positive divergence = divergence (cool colors).",
        "; Contour interval: 1 x 10^-4 s^-1.",
        "; Warm contours = convergence; cool contours = divergence; white = zero.",
        "; This is an objective-analysis visualization aid, not an official KSC/45 WS product.",
        f"; Stations used: {len(stations)}",
        f"; Latest source observation: {obs_summary}",
        f"; Barnes length scale: {BARNES_LENGTH_KM:g} km; gamma={BARNES_GAMMA:g}",
        f"; Grid spacing: {GRID_SPACING_M/1000:g} km",
    ]

    counts = {}
    polyline_counts = {}

    for level in CONTOUR_LEVELS:
        raw_segments = contour_segments(div, xs, ys, level)
        polylines = stitch_segments(raw_segments)
        color, width = contour_style(level)
        r, g, b = color
        hover = contour_hover(level)

        counts[str(level)] = len(raw_segments)
        polyline_counts[str(level)] = len(polylines)

        for poly in polylines:
            # One light smoothing pass removes the blocky marching-squares
            # appearance while retaining the analyzed pattern.
            smooth = chaikin_smooth(poly, iterations=1)
            if len(smooth) < 2:
                continue

            lines.append(f"Color: {r} {g} {b}")
            lines.append(f'Line: {width}, 0, "{hover}"')
            for x, y in smooth:
                lat, lon = xy_to_latlon(x, y, lat0, lon0, coslat)
                lines.append(f"{lat:.7f}, {lon:.7f}")
            lines.append("End:")

    valid_values = [
        v for row in div for v in row if v is not None and math.isfinite(v)
    ]
    diag = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "stations_used": len(stations),
        "latest_source_observation": obs_summary,
        "grid_spacing_m": GRID_SPACING_M,
        "barnes_length_km": BARNES_LENGTH_KM,
        "barnes_gamma": BARNES_GAMMA,
        "max_influence_km": MAX_INFLUENCE_KM,
        "nearest_station_km": NEAREST_STATION_KM,
        "min_nearby_stations": MIN_NEARBY_STATIONS,
        "grid_nx": nx,
        "grid_ny": ny,
        "valid_divergence_gridpoints": len(valid_values),
        "min_divergence_x1e4_s-1": min(valid_values) if valid_values else None,
        "max_divergence_x1e4_s-1": max(valid_values) if valid_values else None,
        "contour_segment_counts": counts,
        "contour_polyline_counts": polyline_counts,
        "status": "ok" if valid_values else "no_valid_gridpoints",
    }

    return "\n".join(lines) + "\n", diag


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--input",
        default="docs/ksc_winds_54ft.txt",
        help="generated 54-ft KSC WINDS placefile",
    )
    ap.add_argument(
        "--output",
        default="docs/ksc_winds_54ft_barnes_divergence.txt",
    )
    ap.add_argument(
        "--json-output",
        default="docs/ksc_winds_54ft_barnes_divergence.json",
    )
    args = ap.parse_args()

    text = Path(args.input).read_text(encoding="utf-8", errors="replace")
    stations = parse_54ft_placefile(text)
    print(f"Barnes analysis: parsed {len(stations)} 54-ft wind stations.")

    placefile, diag = build_placefile(stations)
    Path(args.output).write_text(placefile, encoding="utf-8")
    Path(args.json_output).write_text(
        json.dumps(diag, indent=2),
        encoding="utf-8",
    )

    print(
        "Barnes analysis status:",
        diag.get("status"),
        "valid gridpoints=",
        diag.get("valid_divergence_gridpoints"),
    )
    print("Wrote:", args.output, args.json_output)


if __name__ == "__main__":
    main()
