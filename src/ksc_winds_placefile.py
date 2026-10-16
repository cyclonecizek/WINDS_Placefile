#!/usr/bin/env python3
"""
Live WINDS one-minute API -> GRLevelX station-model placefiles.

Outputs:
  docs/ksc_winds_surface.txt
  docs/ksc_winds_lowest.txt
  docs/ksc_winds_54ft.txt
  docs/ksc_winds_200plus.txt
  docs/ksc_winds.json

Visible station model:
  black = temperature F
  green = dew point F
  black wind barb = average wind direction/speed
  red = peak wind speed kt

Hovering the wind barb shows the full observation.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

USER_AGENT = "KSC-WINDS-GRLevelX/1.0"
API_BASE = os.getenv("WINDS_API_BASE_URL", "https://codiapi-winds1.rad.spaceforce.mil").rstrip("/")

# GitHub Pages location of the sprite sheet.
def default_windbarb_url():
    explicit = os.getenv("WINDBARB_URL", "").strip()
    if explicit:
        return explicit

    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"http://{owner}.github.io/{name}/wind_barb.png"

    return "http://cyclonecizek.github.io/WINDS_Placefile/wind_barb.png"

BARB_URL = default_windbarb_url()

LOOKBACK_MINUTES = int(os.getenv("LOOKBACK_MINUTES", "90"))
STALE_MINUTES = int(os.getenv("STALE_MINUTES", "120"))

COMPASS = {"N","NE","E","SE","S","SW","W","NW"}

@dataclass
class Obs:
    dt: datetime
    raw_site: str
    site: str
    side_measured: str | None
    side_upwind: str | None
    height_ft: int
    avg_dir: float | None
    avg_spd: float | None
    peak_dir: float | None
    peak_spd: float | None
    peak10_dir: float | None
    peak10_spd: float | None
    deviation: float | None
    temp_f: float | None
    dew_f: float | None
    rh: float | None
    pressure: float | None
    latitude: float | None = None
    longitude: float | None = None

def fnum(v):
    try:
        x = float(str(v).strip())
        return x if math.isfinite(x) else None
    except Exception:
        return None

def api_get(path: str):
    """Fetch JSON with TLS verification and bounded retries for transient errors."""
    for attempt in range(3):
        try:
            r = requests.get(API_BASE + path, timeout=60,
                             headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as exc:
            status = exc.response.status_code if exc.response is not None else None
            if attempt == 2 or (status is not None and status < 500 and status != 429):
                raise
            time.sleep(2 ** attempt)


def parse_api(payload) -> list[Obs]:
    if not isinstance(payload, list):
        raise ValueError("WINDS API response must be a JSON array")
    out = []
    for row in payload:
        if not isinstance(row, dict):
            raise ValueError("WINDS API records must be objects")
        height = fnum(row.get("hgt"))
        if height is None or height <= 0 or height == 999:
            continue  # Rain-only sensors have no height or station-model fields.
        dt = datetime.fromisoformat(row["reading_datetime"].replace("Z", "+00:00"))
        dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
        raw_site = str(row["tower"]).strip().upper()
        match = re.fullmatch(r"(\d{1,4})(N|NE|E|SE|S|SW|W|NW)?", raw_site)
        site = match[1].zfill(4) if match else raw_site
        side = match[2] if match else None
        def value(key):
            v = fnum(row.get(key))
            return None if v == 999 else v
        out.append(Obs(
            dt=dt, raw_site=raw_site, site=site, side_measured=side,
            side_upwind=row.get("prevailing"), height_ft=int(height),
            avg_dir=value("avg_dir_1min"), avg_spd=value("avg_spd_1min"),
            peak_dir=value("peak_dir_1min"), peak_spd=value("peak_spd_1min"),
            peak10_dir=value("peak_dir_10min"), peak10_spd=value("peak_spd_10min"),
            deviation=value("dev"), temp_f=value("tmp"), dew_f=value("dp"),
            rh=value("rh"), pressure=value("pre"),
            latitude=value("lat"), longitude=value("lon"),
        ))
    if not out:
        raise ValueError("No WINDS API observations parsed")
    return out


def fetch_observations() -> list[Obs]:
    # Anchor to the latest available observation, rather than the runner clock.
    latest = parse_api(api_get("/winds/latest"))
    end = max(o.dt for o in latest)
    if not 0 < LOOKBACK_MINUTES <= 720:
        raise ValueError("LOOKBACK_MINUTES must be between 1 and 720")
    start = end - timedelta(minutes=LOOKBACK_MINUTES)
    fmt = "%Y-%m-%dT%H:%M:%S"
    path = ("/winds/range?date>=" + quote(start.strftime(fmt))
            + "&date<=" + quote(end.strftime(fmt)) + "&sort=asc")
    obs = parse_api(api_get(path))
    print(f"Live API: {len(obs)} sensor records, latest {end.isoformat()}")
    return obs


def read_observations(path: str) -> list[Obs]:
    text = Path(path).read_text(encoding="utf-8-sig")
    return parse_api(json.loads(text)) if text.lstrip().startswith("[") else parse_csv(text)


def observation_coords(obs, fallback):
    coords = dict(fallback)
    for o in sorted(obs, key=lambda x: x.dt):
        if (o.latitude is not None and o.longitude is not None
                and -90 <= o.latitude <= 90 and -180 <= o.longitude <= 180):
            coords[o.site] = (o.latitude, o.longitude)
    return coords


def normalize_site(raw: str):
    raw = raw.strip()
    parts = raw.split()
    m = re.match(r"^(\d{1,4})", parts[0]) if parts else None
    if m:
        site = m.group(1).zfill(4)
    else:
        site = parts[0].upper() if parts else raw.upper()

    directions = [p.upper() for p in parts[1:] if p.upper() in COMPASS]
    measured = directions[0] if len(directions) >= 1 else None
    upwind = directions[1] if len(directions) >= 2 else None
    return site, measured, upwind

def parse_dt(date_s, time_s):
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%y %H:%M:%S"):
        try:
            return datetime.strptime(f"{date_s} {time_s}", fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None

def parse_csv(text: str) -> list[Obs]:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    needed = {
        "Date","Time","SiteName","Height","Average Wind Direction","Average Wind Speed",
        "Peak Wind Direction","Peak Wind Speed","Peak Wind Direction 10 Min",
        "Peak Wind Speed 10 Min","Deviation","Temperature","Dew Point",
        "Relative Humidity","Barometric Pressure"
    }
    missing = needed.difference(reader.fieldnames or [])
    if missing:
        raise ValueError("WeatherTower CSV is missing columns: " + ", ".join(sorted(missing)))

    out = []
    for r in reader:
        dt = parse_dt(r["Date"], r["Time"])
        try:
            height = int(float(r["Height"]))
        except Exception:
            continue
        site, measured, upwind = normalize_site(r["SiteName"])
        if dt is None:
            continue
        out.append(Obs(
            dt=dt,
            raw_site=r["SiteName"].strip(),
            site=site,
            side_measured=measured,
            side_upwind=upwind,
            height_ft=height,
            avg_dir=fnum(r["Average Wind Direction"]),
            avg_spd=fnum(r["Average Wind Speed"]),
            peak_dir=fnum(r["Peak Wind Direction"]),
            peak_spd=fnum(r["Peak Wind Speed"]),
            peak10_dir=fnum(r["Peak Wind Direction 10 Min"]),
            peak10_spd=fnum(r["Peak Wind Speed 10 Min"]),
            deviation=fnum(r["Deviation"]),
            temp_f=fnum(r["Temperature"]),
            dew_f=fnum(r["Dew Point"]),
            rh=fnum(r["Relative Humidity"]),
            pressure=fnum(r["Barometric Pressure"]),
        ))
    if not out:
        raise ValueError("No WeatherTower observations parsed")
    return out

def load_sites(path: Path):
    out = {}
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["site"].upper()] = (float(r["latitude"]), float(r["longitude"]))
    return out

def side_score(o: Obs):
    # KSC help recommends the record where measurement side == upwind side.
    if o.side_measured and o.side_upwind:
        return 2 if o.side_measured == o.side_upwind else 0
    return 1

def choose_side(rows):
    if not rows:
        return None
    return max(rows, key=lambda o: (o.dt, side_score(o)))

def latest_time_rows(obs, site):
    rows = [o for o in obs if o.site == site]
    latest = max((o.dt for o in rows), default=None)
    return [o for o in rows if o.dt == latest]

def pick_height(rows, height):
    return choose_side([o for o in rows if o.height_ft == height])

def pick_surface(rows):
    thermo_candidates = [o for o in rows if o.height_ft <= 10 and (o.temp_f is not None or o.dew_f is not None)]
    thermo = choose_side(thermo_candidates)

    wind_candidates = [
        o for o in rows
        if o.avg_spd is not None and o.avg_dir is not None and o.height_ft <= 20
    ]
    wind = min(wind_candidates, key=lambda o: (o.height_ft, -side_score(o))) if wind_candidates else None
    # if same height has alternate side, reselect matching side
    if wind:
        wind = choose_side([o for o in wind_candidates if o.height_ft == wind.height_ft])
    return thermo, wind


def pick_lowest_available(rows):
    """
    Select the lowest available thermodynamic and wind observations independently.

    Thermodynamics:
      lowest height with temperature or dew point

    Wind:
      lowest height with average wind direction and speed

    Directional rows at the chosen height still use KSC's preferred-side logic.
    """
    thermo_candidates = [
        o for o in rows
        if o.temp_f is not None or o.dew_f is not None
    ]
    thermo = None
    if thermo_candidates:
        thermo_h = min(o.height_ft for o in thermo_candidates)
        thermo = choose_side([o for o in thermo_candidates if o.height_ft == thermo_h])

    wind_candidates = [
        o for o in rows
        if o.avg_spd is not None and o.avg_dir is not None
    ]
    wind = None
    if wind_candidates:
        wind_h = min(o.height_ft for o in wind_candidates)
        wind = choose_side([o for o in wind_candidates if o.height_ft == wind_h])

    return thermo, wind

def pick_200plus(rows):
    candidates = [
        o for o in rows
        if o.height_ft >= 200 and o.avg_spd is not None and o.avg_dir is not None
    ]
    if not candidates:
        return None
    target_h = min(o.height_ft for o in candidates)
    return choose_side([o for o in candidates if o.height_ft == target_h])

def barb_icon(speed):
    if speed is None:
        return 1
    binned = int(round(speed / 5.0) * 5)
    binned = max(0, min(60, binned))
    return (binned // 5) + 1

def esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

def fmt_num(v, digits=0, suffix=""):
    if v is None:
        return "N/A"
    return f"{v:.{digits}f}{suffix}"

def hover(site, product, wind, thermo, now):
    base = wind or thermo
    age = int(max(0, (now - base.dt).total_seconds() / 60)) if base else -1
    lines = [
        f"KSC WINDS Tower {site}",
        f"Layer: {product}",
        f"Observation: {base.dt:%Y-%m-%d %H:%MZ}" if base else "Observation: N/A",
        f"Age: {age} min" if age >= 0 else "Age: N/A",
        "",
    ]
    if thermo:
        lines += [
            f"Thermo height: {thermo.height_ft} ft",
            f"Temperature: {fmt_num(thermo.temp_f,1,' F')}",
            f"Dew Point: {fmt_num(thermo.dew_f,1,' F')}",
            f"Relative Humidity: {fmt_num(thermo.rh,0,'%')}",
        ]
    if wind:
        lines += [
            "",
            f"Wind height: {wind.height_ft} ft",
            f"1-min Average Wind: {fmt_num(wind.avg_dir,0,' deg')} @ {fmt_num(wind.avg_spd,0,' kt')}",
            f"Peak Wind: {fmt_num(wind.peak_dir,0,' deg')} @ {fmt_num(wind.peak_spd,0,' kt')}",
            f"10-min Peak: {fmt_num(wind.peak10_dir,0,' deg')} @ {fmt_num(wind.peak10_spd,0,' kt')}",
            f"Direction Deviation: {fmt_num(wind.deviation,0,' deg')}",
            f"Sensor side: {wind.side_measured or 'N/A'}",
            f"Upwind side: {wind.side_upwind or 'N/A'}",
        ]
    return "\n".join(lines)

def emit_station(lines, lat, lon, site, product, wind, thermo, now):
    base = wind or thermo
    if not base:
        return
    age = int(max(0, (now - base.dt).total_seconds() / 60))
    if age > STALE_MINUTES:
        return

    h = esc(hover(site, product, wind, thermo, now))
    lines.append(f"Object: {lat:.8f}, {lon:.8f}")

    # Larger WHITE wind barb centered on the station.
    if wind and wind.avg_dir is not None and wind.avg_spd is not None:
        lines.append("Color: 255 255 255")
        lines.append(
            f'Icon: 0, 0, {wind.avg_dir:.0f}, 1, {barb_icon(wind.avg_spd)}, "{h}"'
        )

    # Temperature: upper-left, RED.
    if thermo and thermo.temp_f is not None:
        lines.append("Color: 255 55 55")
        lines.append(f'Text: -30, 18, 1, "{thermo.temp_f:.0f}", "{h}"')

    # Dew point: lower-left, GREEN.
    if thermo and thermo.dew_f is not None:
        lines.append("Color: 50 255 80")
        lines.append(f'Text: -30, -10, 1, "{thermo.dew_f:.0f}", "{h}"')

    # Peak/gust wind: lower-right, WHITE.
    if wind and wind.peak_spd is not None:
        lines.append("Color: 255 255 255")
        lines.append(f'Text: 31, -10, 1, "{wind.peak_spd:.0f}", "{h}"')

    lines.append("End:")

def header(title):
    return [
        f"; {title}",
        "RefreshSeconds: 60",
        "Threshold: 200",
        f'IconFile: 1, 96, 96, 48, 48, "{BARB_URL}"',
        'Font: 1, 13, 1, "Arial"',
        "; Live WINDS one-minute API tower data",
        "; red=temp F, green=dewpoint F, white barb=avg wind, white=peak/gust kt",
        "; Hover wind barb/text for full observation.",
    ]

def generate_products(obs, coords):
    now = datetime.now(timezone.utc)
    surface = header("KSC WINDS Surface")
    lowest = header("KSC WINDS Lowest Available")
    ft54 = header("KSC WINDS 54 ft")
    plus200 = header("KSC WINDS 200+ ft")
    diagnostics = []

    for site, (lat, lon) in sorted(coords.items()):
        rows = latest_time_rows(obs, site)
        if not rows:
            continue

        # Existing surface product: low-level constrained pairing.
        thermo_sfc, wind_sfc = pick_surface(rows)
        emit_station(surface, lat, lon, site, "Surface", wind_sfc, thermo_sfc, now)

        # New product: absolute lowest available thermo + wind levels at each tower.
        thermo_low, wind_low = pick_lowest_available(rows)
        emit_station(lowest, lat, lon, site, "Lowest Available", wind_low, thermo_low, now)

        o54 = pick_height(rows, 54)
        emit_station(ft54, lat, lon, site, "54 ft", o54, o54, now)

        o200 = pick_200plus(rows)
        emit_station(plus200, lat, lon, site, "200+ ft", o200, o200, now)

        diagnostics.append({
            "site": site,
            "latest_time_utc": max(r.dt for r in rows).isoformat(),
            "surface_thermo_height_ft": thermo_sfc.height_ft if thermo_sfc else None,
            "surface_wind_height_ft": wind_sfc.height_ft if wind_sfc else None,
            "lowest_thermo_height_ft": thermo_low.height_ft if thermo_low else None,
            "lowest_wind_height_ft": wind_low.height_ft if wind_low else None,
            "has_54ft": o54 is not None,
            "selected_200plus_height_ft": o200.height_ft if o200 else None,
        })

    return (
        "\n".join(surface) + "\n",
        "\n".join(lowest) + "\n",
        "\n".join(ft54) + "\n",
        "\n".join(plus200) + "\n",
        diagnostics,
    )

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="local WINDS API JSON or legacy WeatherTower CSV")
    ap.add_argument("--sites", default="docs/ksc_winds_tower_sites.csv")
    ap.add_argument("--surface", default="docs/ksc_winds_surface.txt")
    ap.add_argument("--lowest", default="docs/ksc_winds_lowest.txt")
    ap.add_argument("--ft54", default="docs/ksc_winds_54ft.txt")
    ap.add_argument("--plus200", default="docs/ksc_winds_200plus.txt")
    ap.add_argument("--json-output", default="docs/ksc_winds.json")
    args = ap.parse_args()

    obs = read_observations(args.input) if args.input else fetch_observations()
    coords = observation_coords(obs, load_sites(Path(args.sites)))
    surface, lowest, ft54, plus200, diag = generate_products(obs, coords)

    Path(args.surface).write_text(surface, encoding="utf-8")
    Path(args.lowest).write_text(lowest, encoding="utf-8")
    Path(args.ft54).write_text(ft54, encoding="utf-8")
    Path(args.plus200).write_text(plus200, encoding="utf-8")
    Path(args.json_output).write_text(json.dumps({
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "records_parsed": len(obs),
        "physical_sites_in_exports": len({o.site for o in obs}),
        "sites_with_coordinates": len(coords),
        "sites": diag,
    }, indent=2), encoding="utf-8")

    print(f"Parsed {len(obs)} WINDS rows; coordinate sites={len(coords)}")
    print("Wrote:", args.surface, args.lowest, args.ft54, args.plus200)

if __name__ == "__main__":
    main()
