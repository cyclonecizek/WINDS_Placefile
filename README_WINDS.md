# KSC WINDS GR2 Placefile

Standalone GitHub repository for NASA Kennedy Space Center WINDS WeatherTower
data rendered as GRLevelX station-model placefiles.

Repository target:

    cyclonecizek/KSC_WINDS_GR2_Placefile

## Live outputs

After GitHub Pages is enabled, the GR placefile URLs will be:

- https://cyclonecizek.github.io/KSC_WINDS_GR2_Placefile/ksc_winds_surface.txt
- https://cyclonecizek.github.io/KSC_WINDS_GR2_Placefile/ksc_winds_54ft.txt
- https://cyclonecizek.github.io/KSC_WINDS_GR2_Placefile/ksc_winds_200plus.txt

The wind-barb sprite sheet is published at:

- https://cyclonecizek.github.io/KSC_WINDS_GR2_Placefile/windbarbs.png

## Station model

Visible fields:

- Black upper-right: Temperature (°F)
- Green lower-left: Dew point (°F)
- Black wind barb: 5-minute average wind direction/speed
- Red lower-right: peak wind speed

Hover the station-model components for:

- tower/site
- exact sensor height
- observation time and age
- temperature
- dew point
- relative humidity
- average wind
- peak wind
- 10-minute peak wind
- direction deviation
- KSC directional sensor/upwind-side indicators

## Three products

### Surface

Combines the lowest thermodynamic sensor at or below 10 ft, commonly 6 ft,
with the lowest wind sensor at or below 20 ft, commonly 12 ft.

### 54 ft

Uses the exact 54-ft record.

### 200+ ft

Uses the lowest available wind level at or above 200 ft for each tower.

## GitHub setup

1. Create a new public repository named:

       KSC_WINDS_GR2_Placefile

2. Upload the contents of this ZIP to the repository root.

3. Confirm the repository contains:

       .github/workflows/update-winds.yml
       .github/workflows/pages.yml
       docs/
       src/
       requirements.txt

4. Go to:

       Settings -> Actions -> General

   and make sure GitHub Actions are enabled.

5. Go to:

       Settings -> Pages

   and choose:

       Source: GitHub Actions

6. Go to:

       Actions -> Update KSC WINDS Placefiles

   and run the workflow manually once.

7. When it completes successfully, the Pages workflow should run automatically.

## Update schedule

The WINDS updater is scheduled every five minutes at approximately:

    :04 :09 :14 :19 :24 :29 :34 :39 :44 :49 :54 :59

GR placefiles themselves contain:

    RefreshSeconds: 60

so GR checks the published Pages files every minute.

## KSC archive handling

The generator creates a rolling WeatherTower export URL automatically using
the KSC token structure reverse-engineered from controlled 2026 searches.

The fixed token fields are only verified for 2026. The script intentionally
refuses automatic generation in another year until the token is revalidated.

## SSL fallback

The KSC archive may present a certificate chain that GitHub-hosted Linux
runners cannot validate. The script first tries normal certificate validation.
If that exact SSL verification fails, it retries only when the hostname is
exactly:

    kscweather.ksc.nasa.gov

with certificate verification disabled.

## Source files

- src/ksc_winds_placefile.py
- docs/ksc_winds_tower_sites.csv
- docs/windbarbs.png
- .github/workflows/update-winds.yml
- .github/workflows/pages.yml

The included TXT/JSON files are sample outputs and will be replaced by the
scheduled workflow.
