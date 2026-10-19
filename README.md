# WINDS GRLevelX Placefiles

Live one-minute WINDS tower observations rendered for GRLevelX.

## Data source

The updater uses `https://codiapi-winds1.rad.spaceforce.mil`:

- `/winds/latest` establishes the newest available observation time.
- `/winds/range` retrieves a rolling history ending at that time for current
  station models and one-hour loops.

The API was reachable outside RAD during testing on October 16, 2026,
although the supplied user guide describes RAD-only access.
Requests validate TLS certificates and retry transient failures. There is no
KSC archive token generation or archive fallback. The old
`KSC_WINDS_RESULT_URL` secret is no longer used.

Source raw-file headers confirm sensor heights in feet, winds in knots,
and temperatures and dew points in Fahrenheit. `999` and JSON null values
are treated as missing. Numeric towers with directional suffixes share a
physical site; the side matching `prevailing` is preferred. Launch-pad tower
identifiers remain distinct. API coordinates override the fallback site CSV.
Current products use each site's newest timestamp so older valid values do
not replace missing fields in the newest observation.

## Published products

Base URL: https://cyclonecizek.github.io/WINDS_Placefile/

- `ksc_winds_surface.txt`: low thermodynamic sensor (up to 10 ft) and wind
  sensor (up to 20 ft).
- `ksc_winds_lowest.txt`: lowest available thermodynamic and wind sensors.
- `ksc_winds_54ft.txt`: exact 54-ft sensor.
- `ksc_winds_54ft_lowest_thermo.txt`: exact 54-ft winds with independently
  selected lowest valid temperature, dew point, and humidity sensors. Humidity
  appears in cyan at upper right; hover details identify each field’s height.
  Only the newest observation time at each site is used. Missing 54-ft winds
  are left blank, while available thermodynamic fields remain visible. This
  product is included in the existing WINDS update workflow and cron job.
- `ksc_winds_200plus.txt`: lowest wind sensor at or above 200 ft.
- Matching `_loop_1hr.txt` files: one hour of source observation times.
- `ksc_winds_54ft_barnes_divergence.txt` and
  `ksc_winds_54ft_barnes_divergence_loop_1hr.txt`: Barnes wind analysis.

Temperature appears in red, dew point in green, and wind barbs and
one-minute peak gusts in white. Hover text includes the one-minute average,
one-minute peak, ten-minute peak, observation time, and sensor height.

## Updates and running locally

The Update KSC WINDS Placefiles workflow requests an update every five
minutes. GitHub scheduling can be delayed. GR checks published files every
60 seconds; this does not make the five-minute publishing job run every minute.
The Deploy GitHub Pages workflow publishes `docs` after a successful update.
Existing placefile URLs are unchanged.

```sh
pip install -r requirements.txt
python -m unittest discover -s tests
python src/ksc_winds_placefile.py
python src/ksc_winds_barnes_divergence.py
LOOKBACK_MINUTES=65 python src/ksc_winds_loop_placefiles.py
```

Both observation generators accept `--input` with a saved API JSON array
or a legacy WeatherTower CSV for offline use. `WINDS_API_BASE_URL` optionally
overrides the API host. `LOOKBACK_MINUTES` must be 1 to 720 for live requests;
the default is 90. Workflow windows are 60 minutes for current products and
65 minutes for loops, below the API's 12-hour and 100,000-row limits for the
current network size. `STALE_MINUTES` controls stale-station suppression.

If fetching or parsing fails, the workflow fails before committing outputs.
Enable GitHub Pages with Source set to GitHub Actions in repository settings.

## Barnes analysis source

The current 54-ft Barnes placefile reads `/winds/latest` directly from the live
WINDS API, preserving full precision and selecting the prevailing sensor side.
It requires observations no more than five minutes old and at least five
distinct stations. Missing or stale winds are not replaced with archive data.
Explicit local JSON/CSV or legacy placefile input remains available via `--input`.
The one-hour Barnes loop already uses API observations via the WINDS collector.
Both use a 500 m grid, 10 km Barnes length, gamma 0.30, 28 km influence radius,
12 km nearest-station limit, at least three locally supporting stations, and a
12 km buffer around the tower network hull. Negative divergence is convergence
(warm colors), positive is divergence (cool colors), in units of 10^-4 s^-1.
Existing placefile URLs and the WINDS cron job remain in use.
