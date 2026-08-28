#!/usr/bin/env python3
"""
Generate 1-hour looping GRLevelX placefiles from KSC WINDS tower data.

This is an add-on to the existing WINDS generator.  It imports the existing
src/ksc_winds_placefile.py module so the looped tower plots use the same
selection logic, wind-barb sprite, fonts, colors, and hover formatting as the
non-looping placefiles.

It also imports src/ksc_winds_barnes_divergence.py so each 54-ft frame can be
analyzed with the same Barnes settings as the live divergence/convergence
placefile.

Outputs:
  docs/ksc_winds_surface_loop_1hr.txt
  docs/ksc_winds_lowest_loop_1hr.txt
  docs/ksc_winds_54ft_loop_1hr.txt
  docs/ksc_winds_200plus_loop_1hr.txt
  docs/ksc_winds_54ft_barnes_divergence_loop_1hr.txt
  docs/ksc_winds_loop_1hr.json

GRLevelX TimeRange requires placefile version 1.5 support.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOOP_MINUTES = int(os.getenv("WINDS_LOOP_MINUTES", "60"))
FRAME_HOLD_MINUTES = int(os.getenv("WINDS_LOOP_FRAME_HOLD_MINUTES", "5"))
CARRY_FORWARD_MINUTES = int(os.getenv("WINDS_LOOP_CARRY_FORWARD_MINUTES", "7"))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def iso_noz(dt: datetime) -> str:
    """GR placefile TimeRange timestamp, UTC without trailing Z."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def frame_times(obs, now: datetime):
    cutoff = now - timedelta(minutes=LOOP_MINUTES)
    vals = sorted({o.dt for o in obs if cutoff <= o.dt <= now})
    return vals


def rows_for_site_frame(obs_by_site, site: str, frame: datetime):
    """Use exact frame data, or carry the site's newest obs forward briefly."""
    rows = obs_by_site.get(site, [])
    eligible = [o for o in rows if o.dt <= frame]
    if not eligible:
        return []
    newest = max(o.dt for o in eligible)
    if frame - newest > timedelta(minutes=CARRY_FORWARD_MINUTES):
        return []
    return [o for o in eligible if o.dt == newest]


def pick_lowest_fallback(winds, rows):
    if hasattr(winds, "pick_lowest_available"):
        return winds.pick_lowest_available(rows)

    thermo_candidates = [
        o for o in rows
        if o.temp_f is not None or o.dew_f is not None
    ]
    wind_candidates = [
        o for o in rows
        if o.avg_spd is not None and o.avg_dir is not None
    ]

    thermo = None
    if thermo_candidates:
        h = min(o.height_ft for o in thermo_candidates)
        thermo = winds.choose_side([o for o in thermo_candidates if o.height_ft == h])

    wind = None
    if wind_candidates:
        h = min(o.height_ft for o in wind_candidates)
        wind = winds.choose_side([o for o in wind_candidates if o.height_ft == h])

    return thermo, wind


def header_from_live(winds, title: str):
    # Reuse current site's placefile header so icon/font changes automatically
    # carry into the looping products.
    lines = list(winds.header(title))
    # Avoid forcing a stale Title implementation on versions where the live
    # generator omits it; otherwise retain the live header exactly.
    return lines


def generate_tower_loop(winds, obs, coords, product_key: str, title: str):
    now = datetime.now(timezone.utc)
    frames = frame_times(obs, now)
    lines = header_from_live(winds, title)
    lines += [
        "; 1-hour looping version using GRLevelX TimeRange.",
        f"; Frames available: {len(frames)}",
    ]

    obs_by_site = defaultdict(list)
    for o in obs:
        obs_by_site[o.site].append(o)

    frame_counts = []

    for idx, frame in enumerate(frames):
        if idx + 1 < len(frames):
            end = frames[idx + 1]
        else:
            end = frame + timedelta(minutes=FRAME_HOLD_MINUTES)

        # Do not create zero/negative ranges if duplicate/odd timestamps appear.
        if end <= frame:
            continue

        lines.append("")
        lines.append(f"; Frame {frame:%Y-%m-%d %H:%MZ}")
        lines.append(f"TimeRange: {iso_noz(frame)} {iso_noz(end)}")

        plotted = 0
        for site, (lat, lon) in sorted(coords.items()):
            rows = rows_for_site_frame(obs_by_site, site, frame)
            if not rows:
                continue

            if product_key == "surface":
                thermo, wind = winds.pick_surface(rows)
                label = "Surface"
            elif product_key == "lowest":
                thermo, wind = pick_lowest_fallback(winds, rows)
                label = "Lowest Available"
            elif product_key == "54ft":
                o = winds.pick_height(rows, 54)
                thermo = wind = o
                label = "54 ft"
            elif product_key == "200plus":
                o = winds.pick_200plus(rows)
                thermo = wind = o
                label = "200+ ft"
            else:
                raise ValueError(product_key)

            before = len(lines)
            # Passing frame as "now" keeps historical hover age sensible and
            # avoids current-time stale filtering inside the live emitter.
            winds.emit_station(lines, lat, lon, site, label, wind, thermo, frame)
            if len(lines) > before:
                plotted += 1

        frame_counts.append({
            "time_utc": frame.isoformat(),
            "end_utc": end.isoformat(),
            "stations_plotted": plotted,
        })

    return "\n".join(lines) + "\n", frame_counts


def barnes_station_for_obs(barnes, site, lat, lon, o):
    if o is None or o.avg_dir is None or o.avg_spd is None:
        return None

    direction = float(o.avg_dir) % 360.0
    speed_kt = float(o.avg_spd)
    d = math.radians(direction)
    speed_mps = speed_kt * barnes.KT_TO_MPS

    return barnes.Station(
        name=site,
        lat=float(lat),
        lon=float(lon),
        direction_deg=direction,
        speed_kt=speed_kt,
        obs_text=o.dt.strftime("%Y-%m-%d %H:%MZ"),
        u=-speed_mps * math.sin(d),
        v=-speed_mps * math.cos(d),
    )


def barnes_body(placefile_text: str):
    """Keep drawing commands only; the loop file owns the global header."""
    raw = placefile_text.splitlines()
    start = None
    for i, line in enumerate(raw):
        if line.startswith("Color:") or line.startswith("Line:"):
            start = i
            break
    if start is None:
        return []
    return raw[start:]


def generate_barnes_loop(winds, barnes, obs, coords):
    now = datetime.now(timezone.utc)
    frames = frame_times(obs, now)

    lines = [
        "; KSC WINDS 54-ft Barnes Divergence / Convergence - 1 Hour Loop",
        "RefreshSeconds: 60",
        "Threshold: 200",
        "; Uses GRLevelX TimeRange (placefile version 1.5).",
        "; Each frame is independently analyzed from the 54-ft tower winds.",
        "; Negative divergence = convergence (warm colors).",
        "; Positive divergence = divergence (cool colors).",
        f"; Grid spacing: {barnes.GRID_SPACING_M/1000:g} km",
        f"; Barnes length scale: {barnes.BARNES_LENGTH_KM:g} km",
        f"; Hull buffer: {getattr(barnes, 'HULL_BUFFER_KM', 0):g} km",
    ]

    obs_by_site = defaultdict(list)
    for o in obs:
        obs_by_site[o.site].append(o)

    frame_diag = []
    for idx, frame in enumerate(frames):
        end = frames[idx + 1] if idx + 1 < len(frames) else frame + timedelta(minutes=FRAME_HOLD_MINUTES)
        if end <= frame:
            continue

        stations = []
        for site, (lat, lon) in sorted(coords.items()):
            rows = rows_for_site_frame(obs_by_site, site, frame)
            if not rows:
                continue
            o54 = winds.pick_height(rows, 54)
            s = barnes_station_for_obs(barnes, site, lat, lon, o54)
            if s is not None:
                stations.append(s)

        frame_text, diag = barnes.build_placefile(stations)
        body = barnes_body(frame_text)

        lines.append("")
        lines.append(f"; Barnes frame {frame:%Y-%m-%d %H:%MZ}; stations={len(stations)}")
        lines.append(f"TimeRange: {iso_noz(frame)} {iso_noz(end)}")
        lines.extend(body)

        frame_diag.append({
            "time_utc": frame.isoformat(),
            "end_utc": end.isoformat(),
            "stations_used": len(stations),
            "analysis": diag,
        })

    return "\n".join(lines) + "\n", frame_diag


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--winds-module", default="src/ksc_winds_placefile.py")
    ap.add_argument("--barnes-module", default="src/ksc_winds_barnes_divergence.py")
    ap.add_argument("--sites", default="docs/ksc_winds_tower_sites.csv")
    ap.add_argument("--input", help="Optional local merged WeatherTower CSV for testing")
    ap.add_argument("--outdir", default="docs")
    args = ap.parse_args()

    winds = load_module("ksc_winds_live", Path(args.winds_module))
    barnes = load_module("ksc_winds_barnes_live", Path(args.barnes_module))

    if args.input:
        raw = Path(args.input).read_text(encoding="utf-8", errors="replace")
        obs = winds.parse_csv(raw)
    else:
        # Current production WINDS generator downloads the full tower network
        # as four KSC archive groups, then merges/de-duplicates those groups.
        # Reuse that exact production path for the 1-hour loop.
        if hasattr(winds, "fetch_exports") and hasattr(winds, "merge_group_exports"):
            obs = winds.merge_group_exports(winds.fetch_exports())
        elif hasattr(winds, "fetch_export"):
            # Backward compatibility with older single-export generator.
            obs = winds.parse_csv(winds.fetch_export())
        else:
            raise RuntimeError(
                "ksc_winds_placefile.py exposes neither the current "
                "fetch_exports()/merge_group_exports() API nor legacy fetch_export()."
            )

    coords = winds.load_sites(Path(args.sites))

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    products = [
        ("surface", "KSC WINDS Surface - 1 Hour Loop", "ksc_winds_surface_loop_1hr.txt"),
        ("lowest", "KSC WINDS Lowest Available - 1 Hour Loop", "ksc_winds_lowest_loop_1hr.txt"),
        ("54ft", "KSC WINDS 54 ft - 1 Hour Loop", "ksc_winds_54ft_loop_1hr.txt"),
        ("200plus", "KSC WINDS 200+ ft - 1 Hour Loop", "ksc_winds_200plus_loop_1hr.txt"),
    ]

    diagnostics = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "loop_minutes": LOOP_MINUTES,
        "carry_forward_minutes": CARRY_FORWARD_MINUTES,
        "records_parsed": len(obs),
        "products": {},
    }

    for key, title, filename in products:
        text, diag = generate_tower_loop(winds, obs, coords, key, title)
        (outdir / filename).write_text(text, encoding="utf-8")
        diagnostics["products"][key] = diag
        print("Wrote:", outdir / filename)

    text, diag = generate_barnes_loop(winds, barnes, obs, coords)
    barnes_name = "ksc_winds_54ft_barnes_divergence_loop_1hr.txt"
    (outdir / barnes_name).write_text(text, encoding="utf-8")
    diagnostics["products"]["barnes_54ft"] = diag
    print("Wrote:", outdir / barnes_name)

    diag_name = "ksc_winds_loop_1hr.json"
    (outdir / diag_name).write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
    print("Wrote:", outdir / diag_name)


if __name__ == "__main__":
    main()
