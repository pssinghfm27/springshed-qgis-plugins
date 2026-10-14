# Springshed QGIS Plugins

**Three QGIS plugins that turn hydrogeological field surveys into recharge-zone maps for spring restoration in the Himalayas.**

Built during a springshed management programme with the **Central Himalayan Rural Action Group (CHIRAG)**, Uttarakhand (Apr - Jun 2026), to cut the manual GIS work between a field survey and an intervention plan (where to dig trenches and recharge pits above a drying spring).

![QGIS](https://img.shields.io/badge/QGIS-3.22%2B-589632)
![Python](https://img.shields.io/badge/Python-PyQGIS-3776ab)
![Domain](https://img.shields.io/badge/domain-hydrogeology%20%7C%20springsheds-0b7285)

---

## The plugins

| Plugin | What it does | Size |
|---|---|---|
| [**CHIRAG-Springs**](chirag-springs/) | Reads survey data from the Springshed Survey Android app (CSV / Excel), plots geological strike/dip **T-symbols**, maps interventions (CCT, trenches, gabions, recharge pits), **cross-checks field readings against regional GSI structural trends** (flags deviations > 15°), and draws **predictive recharge-zone polygons** from up-dip vectors | ~2,500 lines Python |
| [**Rock Bed Visualizer**](rock-bed-visualizer/) | Projects planar rock beds from strike / dip-direction / dip-amount observations onto a DEM and outputs point grids or 3D polygons for qgis2threejs. Single-bed or batch mode. Fully offline, no API keys | ~2,000 lines Python |
| [**AI Map Maker Pro**](ai-map-maker-pro/) | Builds styled maps from plain-English requests, uploaded CSV / Excel / KML, or scanned map images (vision model reads features, legends and strike/dip). Pulls OSM, Natural Earth and DEM data and exports print layouts to PDF/PNG. User supplies their own API key | ~4,750 lines Python |

## Why it matters

Finding a spring's recharge zone is a structural-geology problem: survey teams measure rock bedding and fractures around each spring, then work out which slope feeds it. Doing this by hand in GIS took hours per spring. These plugins make it a few clicks, with a built-in sanity check against Geological Survey of India regional trends, so NGO field teams can plan interventions faster and with fewer mistakes.

## Install

Download a ready-to-install zip from [`dist/`](dist/) or the Releases page, then in QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**.

## Workflow

```
Springshed Survey app (Android)  ──CSV/Excel──►  CHIRAG-Springs  ──►  T-symbols, GSI check, recharge polygons
                                                         │
                         Rock Bed Visualizer  ◄──────────┘  3D bed planes on DEM
```
Companion app: [chirag-springshed-survey](https://github.com/pssinghfm27/chirag-springshed-survey)

## Author

**Prahlad Pratap Singh** · MBA (Forestry Management), Indian Institute of Forest Management, Bhopal

Developed for CHIRAG's springshed programme. MIT licence.
