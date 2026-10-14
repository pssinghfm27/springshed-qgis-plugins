# Claude AI Map Maker Pro

**Version 2.0.0** — a QGIS plugin that uses the Anthropic Claude API to plan,
source, style and export maps from plain-English requests, uploaded data files,
or scanned map documents.

Built as a full rewrite of the v1 plugin, with every v1 bug from the handoff
document fixed and significant new capability added.

---

## What it does

Five tabs, five workflows:

| Tab | Use it for |
|---|---|
| 💬 **Prompt** | Type a request like *"Forest cover and springs in Bageshwar, Uttarakhand"*. Claude returns a layer plan; the plugin downloads OSM + Natural Earth + optional DEM data and styles everything. |
| 📂 **Upload file** | Drop a CSV, TSV, Excel (xlsx/xls), KML or KMZ. Coordinate columns are auto-detected (lat/lon, x/y, DMS strings, or WKT geometry). |
| 🔍 **Scan map** | Upload a PDF or image of an existing map. Claude Vision identifies features, legend, location, ground control points, and — on geological maps — strike/dip observations. Choose to (a) recreate a similar map in QGIS and/or (b) georeference the scanned image as a raster overlay. |
| 🪨 **Rock beds** | Project a rock-bed plane from a spring or observation point using the strike/dip formula from the Peepalkhola paper. Generates a point grid with computed z-values inside a boundary polygon, ready for 3D visualisation with QGIS's native 3D view or the qgis2threejs plugin. |
| 📤 **Export** | Build a print layout with legend, scale bar, north arrow and attribution, then export to PDF or PNG. |

---

## Install

### macOS

```bash
cd claude_qgis_pro
chmod +x install_mac.sh
./install_mac.sh
```

### Linux

```bash
cd claude_qgis_pro
chmod +x install_linux.sh
./install_linux.sh
```

### Windows

Double-click `install_windows.bat` inside the `claude_qgis_pro` folder.

### Manually (any OS)

Copy the entire `claude_qgis_pro` folder into your QGIS profile's `python/plugins`
directory. Locations:

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/QGIS/QGIS4/profiles/default/python/plugins/` |
| Linux | `~/.local/share/QGIS/QGIS4/profiles/default/python/plugins/` |
| Windows | `%APPDATA%\QGIS\QGIS4\profiles\default\python\plugins\` |

Replace `QGIS4` with `QGIS3` if you're on QGIS 3.x.

### Enable

1. Launch QGIS (restart if it was already open).
2. **Plugins → Manage and Install Plugins → Installed** tab.
3. Tick **Claude AI Map Maker Pro**.
4. A new toolbar button appears. Click it to open the dialog.
5. Click **⚙ Settings** and paste your Anthropic API key
   (free at <https://console.anthropic.com/settings/keys>).

---

## Optional dependencies

| Feature | Install |
|---|---|
| Scan PDFs | `python -m pip install pymupdf --break-system-packages` |
| Read .xls (legacy Excel) | `python -m pip install xlrd --break-system-packages` |

`openpyxl` (for .xlsx) and `Pillow` (for image worldfile writing) ship with
modern QGIS builds and usually need no extra install.

---

## Example prompts

### Working well
- `Road network map of New Delhi, India with metro stations`
- `Country boundaries of South Asia`
- `Rivers and lakes of Uttarakhand with OpenTopoMap basemap`
- `Hospitals and schools in Mumbai, Maharashtra`
- `Forest cover in Jim Corbett National Park with terrain basemap`

### Harder (will use bbox expansion fallback)
- `Forest cover in Bageshwar district, Uttarakhand` — OSM has sparse forest
  coverage in the Indian Himalayas; the plugin falls back to OSM relations
  and (if configured) Natural Earth as a cross-check.
- `Springs of Kumaon Himalaya` — OSM point coverage for springs is uneven;
  upload a CSV of known spring locations for best results.

### DEM / elevation
- `Elevation and hillshade of Kumaon region` — uses AWS terrain tiles by default,
  or OpenTopography SRTMGL1 if you've pasted a key in Settings (free at
  <https://portal.opentopography.org/myopentopo>).

---

## Handling your own data

### CSV / Excel
Coordinate columns detected by name (case-insensitive, partial match):
- **Latitude**: `lat`, `latitude`, `y`, `north`, `northing`
- **Longitude**: `lon`, `lng`, `long`, `longitude`, `x`, `east`, `easting`

Supported formats:
- Decimal degrees: `28.6139`
- DMS strings: `28°36'50"N`, `77° 12' 32.4" E`
- WKT geometry in a `wkt`/`geometry`/`geom` column

### KML / KMZ
Loaded via QGIS's native OGR driver. Styles embedded in the KML are preserved;
icons referenced from inside a KMZ are extracted automatically.

### PDF / image maps
- Claude Vision examines the image and returns a JSON object with:
  map type, legend, visible features, approximate bbox, ground control points,
  and (for geological maps) **strike/dip observations**.
- If bbox confidence is **high** or **medium**, a world file is written alongside
  the image and it's loaded directly as a georeferenced raster.
- If confidence is low or you want manual control, a `.points` file is written
  in QGIS Georeferencer format — open **Raster → Georeferencer**, load the image,
  then **File → Load GCP Points** to review before applying.

### Rock bed projection (🪨 Rock beds tab)

Projects a single idealised rock-bed plane through a spring or observation
point using the strike/dip formula from the Peepalkhola paper
("Visualizing Strikes and Dips of Rock Beds in QGIS with qgis2threejs",
CHIRAG / Dotiyalgaon, 2018).

The math: given strike direction θ, dip direction α and dip amount β, the
bed's normal vector is computed and the plane equation is solved for z:

```
z_rel = (x'·cosθ·sinβ + y'·sinθ·sinβ)
        ─────────────────────────────────────────
        (sinα·cosθ·cosβ − sinθ·cosα·cosβ)

z_abs = z_origin + z_rel
```

Workflow:
1. Load a **spring/origin point layer** (click once on the feature in QGIS
   so it's selected, or leave nothing selected to use the layer's first feature).
2. Load a **boundary polygon** layer that contains the area where the projected
   plane should be evaluated. Start the polygon from the spring and extend
   in the direction you want to study.
3. Load a **DEM raster** (use the `aws_terrain` DEM source on the Prompt tab,
   or import your own ASTER / SRTM tile).
4. In the 🪨 Rock beds tab, pick those three layers, enter the strike, dip
   direction and dip amount (degrees clockwise from north), set the grid
   spacing (metres; 3 m matches the Peepalkhola paper), and click
   **Build rock-bed plane**.
5. The output point layer has a `z_abs` field with the absolute elevation
   on the projected bed. Open **View → 3D Map Views** with the DEM as
   the terrain, or use the **qgis2threejs** plugin and set `z_abs` as the
   absolute Z value for the layer.

**Caveats** (inherited from the paper): this is an idealised single-plane
projection. It does not account for folds, faults or any internal structural
variability. Use it as an indicative aid for answering questions like
*"if this is the bed orientation, how far upslope could the recharge area
extend?"* — not as a definitive geological model.

**Convention note**: strike and dip direction are both measured clockwise from
north. If the resulting plane tilts the wrong way, add 180° to the dip
direction. The original paper treats dip direction as the up-dip azimuth.

---

## Model selection & API cost

Pick in the top bar of the dialog. Prompt caching is on by default so the
~2 KB system prompts are cached server-side for 5 minutes — the second
planning call costs ~10% of the first.

| Model | When to use |
|---|---|
| Haiku 4.5 | Simple, small maps (e.g. road network of one city). Fastest, cheapest. |
| Sonnet 4.6 | **Default.** Balanced quality and cost. Good for most plans. |
| Opus 4.7 | Complex plans with many interacting layers, or when you want the best possible Vision analysis. Highest cost but highest-quality layer selection. |

Vision (map scanning) has its own model dropdown in Settings; Opus 4.7 is the
recommended default since bbox inference from an image is the hardest task.

Rough cost estimate (April 2026 pricing): a typical map plan uses ~2 KB input +
~1 KB output tokens. With Sonnet 4.6 and caching on, that's a fraction of a
US cent per map after the first call of a session.

---

## Architecture

```
claude_qgis_pro/
├── __init__.py, plugin.py, metadata.txt   — QGIS plugin registration
├── compat.py                              — PyQt5/6 enum shims (QGIS 3/4 both work)
├── settings_store.py                      — QSettings wrapper + model catalogue
│
├── claude/
│   ├── client.py                          — API client: retries, caching, vision
│   ├── prompts.py                         — system prompts (plan / scan / refine)
│   └── schemas.py                         — JSON validation + normalisation
│
├── data/
│   ├── geocoder.py                        — Nominatim with on-disk cache
│   ├── osm_converter.py                   — OSM → GeoJSON, handles RELATIONS
│   ├── overpass.py                        — way + relation queries, bbox fallback
│   ├── natural_earth.py                   — shapefile downloader + bbox clipping
│   ├── elevation.py                       — AWS terrain tiles + OpenTopography DEM
│   └── basemap.py                         — XYZ providers (OSM, sat, topo, terrain)
│
├── io_loaders/
│   ├── __init__.py                        — file-type sniffer + dispatcher
│   ├── csv_loader.py                      — decimal + DMS + WKT
│   ├── excel_loader.py                    — openpyxl / xlrd backends
│   ├── kml_loader.py                      — KML pass-through + KMZ extraction
│   └── document_scanner.py                — PDF → image → Claude Vision
│
├── styling/
│   ├── symbology.py                       — single/categorized/graduated renderers
│   ├── labels.py                          — PAL labels with buffer
│   ├── layout.py                          — print layout with legend/scale/arrow
│   └── georeferencer.py                   — world-file + .points for scanned maps
│
├── workers/
│   └── thread_workers.py                  — PlanWorker, DownloadWorker, VisionWorker
│                                            (all QGIS ops fire on main thread)
│
├── dialogs/
│   ├── main_dialog.py                     — 4-tab main UI
│   └── settings_dialog.py                 — API keys, model, behaviour toggles
│
├── utils/
│   ├── logging.py                         — QgsMessageLog wrapper
│   ├── bbox.py                            — normalise, expand, subset SQL
│   └── cache.py                           — filesystem cache under temp
│
└── icons/icon.svg                         — toolbar icon
```

---

## Bugs fixed since v1

All eight bugs from the v1 handoff document are fixed:

| # | v1 bug | Fix location |
|---|---|---|
| 3.1 | PyQt5→PyQt6 enum crashes | `compat.py` — single import-time shim |
| 3.2 | `QgsWkbTypes.geometryType()` removed in QGIS 4 | `compat.detect_geometry_type()` |
| 3.3 | Thread-unsafe QGIS layer ops | `workers/thread_workers.py` — signals-only |
| 3.4 | Wrong Claude model name | `settings_store.AVAILABLE_MODELS` |
| 3.5 | OSM rural 0-features | `data/overpass.py` — way + relation + bbox expansion |
| 3.6 | Natural Earth loads globally | `data/natural_earth.bbox_subset_sql()` |
| 3.7 | Basemap layer ordering | `DownloadWorker` emits in z-order |
| 3.8 | Converter misses relations | `data/osm_converter._relation_to_feature()` + ring stitching |

---

## Troubleshooting

**"API key missing" on every click** — open ⚙ Settings, paste key, click OK.
The key is saved in QSettings at `ClaudeMapperPro/api_key`.

**"OSM returned 0 features" for a rural area** — the plugin already retries with
a 50% larger bbox (up to twice) and queries relations. If it still fails, the
area genuinely has no OSM data for that feature type. Fall back to Natural Earth
or upload your own CSV/KML.

**"OpenTopography failed"** — the API requires a free key. Register at
<https://portal.opentopography.org/myopentopo> and paste it into ⚙ Settings.
Without a key the plugin falls back to AWS terrain tiles for elevation.

**PDF scan: "Cannot rasterize PDF"** — install PyMuPDF:
`python -m pip install pymupdf --break-system-packages`
(on macOS you may need to use the QGIS-bundled Python; run the command from
the QGIS Python console).

**Plugin icon doesn't appear** — check the QGIS Python log (**View → Panels →
Log Messages → Python Console** tab) for import errors. Restart QGIS after
install.

---

## Licence

MIT. Data sources retain their own licences:
- OpenStreetMap: ODbL
- Natural Earth: public domain
- AWS terrain tiles: free for non-commercial and commercial use
- OpenTopography: see per-dataset terms at opentopography.org

Built by **Prahlad Pratap Singh** — IIFM Bhopal, MBA-FM 2025–27.
