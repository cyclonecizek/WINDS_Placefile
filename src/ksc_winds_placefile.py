#!/usr/bin/env python3
"""
NASA KSC WINDS WeatherTower archive -> three GRLevelX station-model placefiles.

Outputs:
  docs/ksc_winds_surface.txt
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
from urllib.parse import urlparse

import requests
import urllib3

USER_AGENT = "KSC-WINDS-GRLevelX/1.0"
BASE60 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz01234567"

# Verified against controlled WeatherTower searches in August 2026.
TOKEN_PREFIX = "Ba"
TOKEN_BETWEEN = "ABa"
TOKEN_AFTER_END = "AAAABaAAABaAAABaAABaAABaAABaAABaAndaBncnWnfaDnenXngaNnhnYnaaTnbnZaKaLoH"

# GitHub Pages location of the sprite sheet.
BARB_URL = os.getenv(
    "WINDBARB_URL",
    "https://cyclonecizek.github.io/KSC_WINDS_GR2_Placefile/windbarbs.png",
)

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

def fnum(v):
    try:
        x = float(str(v).strip())
        return x if math.isfinite(x) else None
    except Exception:
        return None

def enc60(n: int) -> str:
    if not 0 <= n < len(BASE60):
        raise ValueError(f"KSC token value out of range: {n}")
    return BASE60[n]

def encode_dt(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    return enc60(dt.month) + enc60(dt.day) + enc60(dt.hour) + enc60(dt.minute)

def build_token(start: datetime, end: datetime) -> str:
    if end <= start:
        raise ValueError("end must be later than start")
    if start.year != 2026 or end.year != 2026:
        raise ValueError("Automatic WeatherTower token is currently verified only for 2026")
    return TOKEN_PREFIX + encode_dt(start) + TOKEN_BETWEEN + encode_dt(end) + TOKEN_AFTER_END

def build_export_url(start: datetime, end: datetime) -> str:
    return "https://kscweather.ksc.nasa.gov/wxarchive/WeatherTower/Export/" + build_token(start, end)

def fetch_export() -> str:
    override = os.getenv("KSC_WINDS_RESULT_URL", "").strip()
    if override:
        url = override
    else:
        end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        start = end - timedelta(minutes=LOOKBACK_MINUTES)
        url = build_export_url(start, end)

    print("KSC WINDS export URL:", url)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/csv,text/plain,application/octet-stream,*/*",
    }

    retry_delays = [0, 10, 20, 40, 60]
    last_error = None

    for attempt, delay in enumerate(retry_delays, start=1):
        if delay:
            print(f"Retrying KSC WINDS request in {delay} seconds...")
            time.sleep(delay)

        try:
            try:
                r = requests.get(
                    url,
                    timeout=60,
                    headers=headers,
                )
            except requests.exceptions.SSLError:
                if urlparse(url).hostname != "kscweather.ksc.nasa.gov":
                    raise
                print(
                    "WARNING: KSC TLS certificate chain could not be validated; "
                    "retrying this exact NASA host with certificate verification disabled."
                )
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                r = requests.get(
                    url,
                    timeout=60,
                    headers=headers,
                    verify=False,
                )

            r.raise_for_status()

            if attempt > 1:
                print(f"KSC WINDS request succeeded on attempt {attempt}.")

            return r.text

        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.HTTPError,
        ) as exc:
            last_error = exc
            print(
                f"KSC WINDS request attempt {attempt}/{len(retry_delays)} failed: "
                f"{type(exc).__name__}: {exc}"
            )

            # Do not keep retrying permanent client-side HTTP errors such as 400/404.
            if isinstance(exc, requests.exceptions.HTTPError):
                response = getattr(exc, "response", None)
                status = response.status_code if response is not None else None
                if status is not None and 400 <= status < 500 and status != 429:
                    raise

    raise RuntimeError(
        "KSC WINDS archive request failed after all retry attempts. "
        f"Last error: {type(last_error).__name__}: {last_error}"
    )

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
    return max(rows, key=lambda o: (side_score(o), o.dt))

def latest_time_rows(obs, site):
    rows = [o for o in obs if o.site == site]
    if not rows:
        return []
    newest = max(o.dt for o in rows)
    return [o for o in rows if o.dt == newest]

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
            f"Average Wind: {fmt_num(wind.avg_dir,0,' deg')} @ {fmt_num(wind.avg_spd,0,' kt')}",
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

    # Wind barb at object center; base sprite points north and is rotated clockwise.
    if wind and wind.avg_dir is not None and wind.avg_spd is not None:
        lines.append("Color: 0 0 0")
        lines.append(
            f'Icon: 0, 0, {wind.avg_dir:.0f}, 1, {barb_icon(wind.avg_spd)}, "{h}"'
        )

    # Temperature upper-right (black)
    if thermo and thermo.temp_f is not None:
        lines.append("Color: 0 0 0")
        lines.append(f'Text: 18, 13, 1, "{thermo.temp_f:.0f}", "{h}"')

    # Dew point lower-left (green)
    if thermo and thermo.dew_f is not None:
        lines.append("Color: 0 210 0")
        lines.append(f'Text: -18, -13, 1, "{thermo.dew_f:.0f}", "{h}"')

    # Peak wind lower-right (red)
    if wind and wind.peak_spd is not None:
        lines.append("Color: 235 40 40")
        lines.append(f'Text: 20, -13, 1, "{wind.peak_spd:.0f}", "{h}"')

    lines.append("End:")

def header(title):
    return [
        f"Title: {title}",
        "RefreshSeconds: 60",
        "Threshold: 200",
        f'IconFile: 1, 64, 64, 32, 32, "{BARB_URL}"',
        'Font: 1, 11, 1, "Arial"',
        "; NASA KSC Spaceport Weather Archive WINDS tower data",
        "; black=temp F, green=dewpoint F, black barb=avg wind, red=peak wind kt",
        "; Hover wind barb/text for full observation.",
    ]

def generate_products(obs, coords):
    now = datetime.now(timezone.utc)
    surface = header("KSC WINDS Surface")
    ft54 = header("KSC WINDS 54 ft")
    plus200 = header("KSC WINDS 200+ ft")
    diagnostics = []

    for site, (lat, lon) in sorted(coords.items()):
        rows = latest_time_rows(obs, site)
        if not rows:
            continue

        thermo_sfc, wind_sfc = pick_surface(rows)
        emit_station(surface, lat, lon, site, "Surface", wind_sfc, thermo_sfc, now)

        o54 = pick_height(rows, 54)
        emit_station(ft54, lat, lon, site, "54 ft", o54, o54, now)

        o200 = pick_200plus(rows)
        emit_station(plus200, lat, lon, site, "200+ ft", o200, o200, now)

        diagnostics.append({
            "site": site,
            "latest_time_utc": max(r.dt for r in rows).isoformat(),
            "surface_thermo_height_ft": thermo_sfc.height_ft if thermo_sfc else None,
            "surface_wind_height_ft": wind_sfc.height_ft if wind_sfc else None,
            "has_54ft": o54 is not None,
            "selected_200plus_height_ft": o200.height_ft if o200 else None,
        })

    return "\n".join(surface)+"\n", "\n".join(ft54)+"\n", "\n".join(plus200)+"\n", diagnostics

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="local WeatherTower export CSV")
    ap.add_argument("--sites", default="docs/ksc_winds_tower_sites.csv")
    ap.add_argument("--surface", default="docs/ksc_winds_surface.txt")
    ap.add_argument("--ft54", default="docs/ksc_winds_54ft.txt")
    ap.add_argument("--plus200", default="docs/ksc_winds_200plus.txt")
    ap.add_argument("--json-output", default="docs/ksc_winds.json")
    ap.add_argument("--print-url", action="store_true")
    ap.add_argument("--start")
    ap.add_argument("--end")
    args = ap.parse_args()

    if args.print_url:
        if args.start and args.end:
            start = datetime.strptime(args.start, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
            end = datetime.strptime(args.end, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
        else:
            end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
            start = end - timedelta(minutes=LOOKBACK_MINUTES)
        print(build_export_url(start, end))
        return

    text = Path(args.input).read_text(encoding="utf-8", errors="replace") if args.input else fetch_export()
    obs = parse_csv(text)
    coords = load_sites(Path(args.sites))
    surface, ft54, plus200, diag = generate_products(obs, coords)

    Path(args.surface).write_text(surface, encoding="utf-8")
    Path(args.ft54).write_text(ft54, encoding="utf-8")
    Path(args.plus200).write_text(plus200, encoding="utf-8")
    Path(args.json_output).write_text(json.dumps({
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "records_parsed": len(obs),
        "sites_with_coordinates": len(coords),
        "sites": diag,
    }, indent=2), encoding="utf-8")

    print(f"Parsed {len(obs)} WeatherTower rows; coordinate sites={len(coords)}")
    print("Wrote:", args.surface, args.ft54, args.plus200)

if __name__ == "__main__":
    main()
