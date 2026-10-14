# Rock Bed Visualizer — QGIS plugin

**Version 1.0.0** · Works offline · QGIS 3.34+ and QGIS 4.0+ · No API keys, no internet

A focused QGIS plugin that implements the method described in **"Visualizing
Strikes and Dips of Rock Beds in QGIS with qgis2threejs"** (CHIRAG, Dotiyalgaon,
Peepalkhola spring case study). Given a rock observation point and its bed
orientation angles, the plugin projects an idealised planar bed through
the origin and produces either a point grid with computed z-values or a
3D polygon — or both.

---

## What it does

Two modes from one single-window dialog:

### Single-bed mode
Type three angles (strike, dip direction, dip amount), pick an origin point,
pick a boundary polygon, pick a DEM, click **Build planes**. Output: one
point-grid layer and/or one 3D-plane polygon with a `z_abs` field giving
absolute elevation on the projected bed.

### Batch mode
Point to a rock-observation layer whose attribute table has columns for
strike, dip direction and dip amount — the plugin produces one output
plane per row, named with the row's label field. Great for processing a
whole springshed survey at once.

### Origin options
- **From a point-layer feature** (the layer's first selected feature, else its first feature)
- **Pick on the canvas** with a one-click map tool
- **Type X / Y** manually — useful when you have the coordinates from another source

### Output options
- **Point grid with z attribute** (matches the Peepalkhola paper's method — perfect for qgis2threejs)
- **3D polygon plane** (single rectangle aligned to the boundary bbox — nicer in native QGIS 3D view)
- **Both**

---

## Installation

Unpack the plugin folder anywhere, then run the installer for your OS:

### macOS
```bash
cd rock_bed_visualizer
chmod +x install_mac.sh
./install_mac.sh
```

### Linux
```bash
cd rock_bed_visualizer
chmod +x install_linux.sh
./install_linux.sh
```

### Windows
Double-click `install_windows.bat` inside the `rock_bed_visualizer` folder.

### Manually (any OS)
Copy the entire `rock_bed_visualizer` folder into your QGIS profile's
`python/plugins` directory:

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/QGIS/QGIS4/profiles/default/python/plugins/` |
| Linux | `~/.local/share/QGIS/QGIS4/profiles/default/python/plugins/` |
| Windows | `%APPDATA%\QGIS\QGIS4\profiles\default\python\plugins\` |

Replace `QGIS4` with `QGIS3` if you're on QGIS 3.x.

### Enable in QGIS
1. Launch (or restart) QGIS.
2. **Plugins → Manage and Install Plugins → Installed** tab.
3. Tick **Rock Bed Visualizer**.
4. Click the new toolbar icon (orange/purple rock bed shape).

---

## Quickstart — reproducing the Peepalkhola example

1. Load the ASTER DEM of your study area into QGIS.
2. Load a point layer containing the spring (**set its CRS to EPSG:4326**).
3. Load or digitise a boundary polygon around the spring
   (**reproject it to EPSG:3857 or a UTM zone so spacing is in metres**).
4. Open the Rock Bed Visualizer toolbar button.
5. Mode = **Single bed**. Origin = *From point layer feature*; pick the spring layer.
6. Strike **70°**, Dip direction **160°**, Dip amount **40°** (Peepalkhola values).
7. Boundary polygon = your polygon; grid spacing **3** (metres).
8. DEM raster = your ASTER layer.
9. Output geometry = **Both**.
10. Click **Validate inputs**. Then **Build planes**.

Two layers appear in your project: a coloured point grid and a translucent
3D polygon. Open **View → 3D Map Views → New 3D Map View**, set the DEM
as the terrain, and the plane shows cutting through the hillside.

For qgis2threejs: open the plugin, in the DEM section enable *Build Frame*,
in the Points section add the point grid layer, set the Z coordinate to
*absolute value "z_abs"*, and Preview.

---

## Batch-mode attribute table template

If you have a field survey of multiple rock observations, create a CSV or
point layer with at least these columns:

| x | y | strike | dip_dir | dip_amount | rock_id |
|---|---|--------|---------|------------|---------|
| 8871840.87 | 3468304.71 | 70 | 160 | 40 | Peepalkhola-01 |
| 8871920.40 | 3468300.11 | 72 | 158 | 42 | Peepalkhola-02 |
| 8872010.12 | 3468290.09 | 68 | 162 | 38 | Peepalkhola-03 |

Import it in QGIS (Layer → Add Layer → Add Delimited Text Layer) with
the point layer CRS set to EPSG:3857 (or whatever your x/y are in).

In the plugin, switch to **Batch** mode, pick that layer, map the fields
(the plugin auto-detects common names like *strike*, *dip_dir*, *dip_amount*,
*rock_id*), tick "Use each row's point geometry as that row's origin",
and click **Build planes**.

One output plane is created per valid row. Rows with invalid angles are
skipped and listed in a summary dialog.

---

## Validation — what the plugin checks

Before building, the Validate button runs these checks and reports ALL
problems at once:

| Check | Error / Warning |
|---|---|
| Boundary CRS is geographic (degrees) | ✗ error — reproject to UTM or 3857 |
| Grid spacing too fine (>500k candidate points) | ✗ error — increase spacing |
| Grid spacing too coarse (<10 points) | ✗ error — decrease spacing |
| Strike = dip direction (or opposite) | ✗ error — degenerate bed |
| Dip amount = 0 | ✗ error — bed is flat, plane is trivial |
| Dip amount > 90 | ✗ error — out of range |
| DEM doesn't cover origin | ✗ error — load a wider DEM |
| Origin outside boundary polygon | ⚠ warning — extrapolation may be extreme |
| (Batch) Every row fails validation | ✗ error — field mapping likely wrong |

---

## The math (for reference)

From the paper, the plane equation is derived from the cross-product of
the strike and dip unit vectors:

```
        x'·cos(θ)·sin(β) + y'·sin(θ)·sin(β)
z_rel = ──────────────────────────────────────────────
        sin(α)·cos(θ)·cos(β) − sin(θ)·cos(α)·cos(β)

z_abs = z_origin + z_rel
```

where θ = strike direction, α = dip direction, β = dip amount (all in
radians in the formula, degrees in the UI), and x'/y' are metres measured
from the origin.

The plugin computes this in pure Python — no external libraries, no
network — for every grid cell inside the boundary polygon.

---

## Caveats (from the paper)

This projects a single idealised plane through one observation. It does
not model folds, faults, or internal structural variability within the
rock. Use it as an indicative tool for questions like *"if this is the
bed orientation, how far along the terrain can the recharge area extend?"*
— not as a definitive geological model.

The convention: strike and dip direction are compass bearings clockwise
from north; dip direction is the up-dip azimuth. If the plane tilts the
wrong way in 3D view, add 180° to the dip direction.

---

## Troubleshooting

**Plugin doesn't appear after install** — restart QGIS (not just close
the project). Check **View → Panels → Log Messages → Python Console** tab
for import errors.

**"Grid would contain N points (> max)"** — the spacing you picked is too
fine for your boundary polygon's size. Increase spacing or shrink the
boundary. 3 m works for a 300 m × 300 m polygon; try 10 m for a 1 km × 1 km
polygon.

**"Boundary polygon's CRS is geographic"** — you must reproject the polygon
to EPSG:3857 or a UTM zone before using it. Right-click the layer →
Export → Save Features As, choose the projected CRS, and use the new
file as your boundary.

**"Could not read origin elevation from the DEM"** — the DEM's extent
doesn't include the origin point, or the DEM is invalid. Try loading a
wider DEM.

**3D polygon looks flat** — toggle **View → 3D Map Views** and set the
DEM as the terrain source. The polygon's Z values only render in 3D.

**qgis2threejs doesn't use the z_abs field** — in the Points section of
qgis2threejs, explicitly choose *Z coordinate = Absolute value* and pick
the `z_abs` field.

---

## Architecture

```
rock_bed_visualizer/
├── metadata.txt, __init__.py, plugin.py   - QGIS plugin registration
├── compat.py                              - PyQt5/6 enum shim (QGIS 3/4 compat)
│
├── core/
│   ├── math_plane.py                      - The paper's formula, self-tested
│   ├── grid_builder.py                    - Regular grid inside polygon
│   ├── dem_sampler.py                     - QgsRaster.identify wrapper
│   ├── layer_writer.py                    - GeoJSON output files
│   ├── validators.py                      - All user-error message logic
│   └── pick_tool.py                       - Canvas point-picker tool
│
├── dialogs/main_dialog.py                 - Single-window UI
├── utils/logging.py                       - QgsMessageLog wrapper
├── icons/icon.svg                         - Toolbar icon
│
├── install_mac.sh, install_linux.sh, install_windows.bat
└── README.md                              - This file
```

---

## Licence

MIT. The mathematical method is described in a CHIRAG document (Dotiyalgaon,
Peepalkhola spring, circa 2018) — this plugin is an implementation of that
method, not a derivative of any copyrighted code.

Built by **Prahlad Pratap Singh** — IIFM Bhopal, MBA-FM 2025–27, during
a CHIRAG springshed internship.
