"""Pack CorrDiff ensemble predictions into an iCHARM-ready Zarr store.

This is the CorrDiff analogue of ``earth2_to_zarr.py``. It keeps the **same
front-end format** iCHARM already consumes — a consolidated Zarr where each
variable is a ``(time, lat, lon)`` field animated over time, plus **one** extra
(non time/lat/lon) dimension that iCHARM turns into a dropdown selector.

For Earth-2 that extra dimension was pressure ``level``. CorrDiff is all surface
variables but carries an **ensemble** instead, so here the extra dimension is
``stat`` — the ensemble statistics we already save per variable/hour
(``mean``, ``std``, ``min``, ``max``, and any percentiles ``p10``…). The viewer
then shows a "statistic" dropdown exactly where it used to show a level dropdown.

Input
-----
The loose ``.npy`` files written by ``corrdiff_predict.run_prediction`` for one
initial-condition date, e.g. ``<pred_dir>/2025-04-04_tp_H003_ensemble_mean.npy``,
plus the 2-D grid files ``corrdiff_output_lat.npy`` / ``corrdiff_output_lon.npy``
(longitude stored 0-360; converted here to -180..180). Raw per-sample files
(``_sample##.npy``) are ignored — only the statistics are stacked.

Output
------
A consolidated Zarr ``corrdiff_<date>_map.zarr`` with:
  * dims  ``(time, stat, y, x)``
  * coords ``time`` (valid = init + forecast hour), ``stat`` (ordered
    mean, std, min, max, then percentiles ascending), and 2-D ``lat(y,x)`` /
    ``lon(y,x)``
  * data vars: one per CorrDiff variable found (u10m, v10m, t2m, tp, csnow,
    cicep, cfrzr, crain), each ``(time, stat, y, x)``
  * chunks ``time=1`` (stream the animation), everything else whole

Note on wind speed: ``earth2_to_zarr`` derived ``ws = sqrt(u^2 + v^2)``. That is
NOT carried over, because computing it from ensemble statistics is wrong (the
mean of wind speed != speed of the mean components). To expose a true ensemble
``ws10m``, compute it per-sample in ``corrdiff_predict.py`` and save its stats,
then it flows through here like any other variable.

Usage (host, ephemeral deps):
    uv run --with xarray --with "zarr>=3" --with numpy --with dask \
        python -m icharm.dataset_processing.corrdiff.corrdiff_to_zarr \
            /path/to/OhioRiver/2025-04-04 \
            --out /path/to/backend/datasets/corrdiff_2025-04-04_map.zarr
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
import xarray as xr

# The CorrDiff output grid lat/lon files live alongside this script (see
# README.md in this folder — they're gitignored, copy them from the
# corrdiff-auto pod). They are the fine ~3 km CONUS grid — a 2-D curvilinear
# grid, e.g. (1056, 1792) — so the store's y/x dimensions come straight from
# these files.
DEFAULT_GRID_DIR = Path(__file__).resolve().parent

# CorrDiff output variables, in channel order (for a stable var ordering).
CORRDIFF_VARS = ["u10m", "v10m", "t2m", "tp", "csnow", "cicep", "cfrzr", "crain"]
_VAR_ORDER = {v: i for i, v in enumerate(CORRDIFF_VARS)}

# Per-variable metadata for the store (colour bars/labels live in the viewer).
VAR_ATTRS = {
    "u10m":  {"units": "m/s", "long_name": "10 m U-wind"},
    "v10m":  {"units": "m/s", "long_name": "10 m V-wind"},
    "t2m":   {"units": "K",   "long_name": "2 m temperature"},
    "tp":    {"units": "mm",  "long_name": "total precip (previous 1 h; 3-hr estimate = tp*3)"},
    "csnow": {"units": "",    "long_name": "categorical snow"},
    "cicep": {"units": "",    "long_name": "categorical ice pellets"},
    "cfrzr": {"units": "",    "long_name": "categorical freezing rain"},
    "crain": {"units": "",    "long_name": "categorical rain"},
}

# Filenames: {date}_{var}_H{H}_{stat}.npy where stat is a saved ensemble stat.
# The stat alternation excludes raw "sample##" files by construction.
FILE_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<var>[a-z0-9]+)_H(?P<h>\d+)_"
    r"(?P<stat>ensemble_mean|ensemble_std|ensemble_max|ensemble_min|p\d{2})\.npy$"
)
STAT_LABEL = {
    "ensemble_mean": "mean", "ensemble_std": "std",
    "ensemble_min": "min", "ensemble_max": "max",
}  # percentiles keep their "p##" label


def _stat_sort_key(stat: str):
    """Order stats: mean, std, min, max, then percentiles ascending."""
    base = {"mean": 0, "std": 1, "min": 2, "max": 3}
    if stat in base:
        return (0, base[stat])
    if stat.startswith("p") and stat[1:].isdigit():
        return (1, int(stat[1:]))
    return (2, stat)


def infer_date(pred_dir: Path) -> str:
    """Return the single YYYY-MM-DD present in ``pred_dir``'s filenames."""
    dates = set()
    for f in pred_dir.glob("*.npy"):
        m = re.match(r"(\d{4}-\d{2}-\d{2})_", f.name)
        if m:
            dates.add(m.group(1))
    if not dates:
        raise SystemExit(f"No dated CorrDiff .npy files found in {pred_dir}")
    if len(dates) > 1:
        raise SystemExit(f"Multiple dates in {pred_dir}: {sorted(dates)}; pass --date.")
    return dates.pop()


def load_grid(grid_dir: Path):
    """Load the CorrDiff 2-D output grid from the folder's lat/lon files.

    Uses ``corrdiff_output_lat.npy`` / ``corrdiff_output_lon.npy`` verbatim as the
    y/x dimension reference (the fine ~3 km curvilinear grid, e.g. 1056x1792).
    Native precision (float64) is preserved. Longitudes are normalized to
    -180..180 only if they look like a 0-360 grid, so the globe viewer places
    them correctly.
    """
    lat = np.load(grid_dir / "corrdiff_output_lat.npy")
    lon = np.load(grid_dir / "corrdiff_output_lon.npy")
    if lat.ndim == 1 or lon.ndim == 1:
        lon, lat = np.meshgrid(lon, lat)
    if float(np.nanmax(lon)) > 180.0:  # 0-360 -> -180..180 (handles wrap)
        lon = ((lon + 180.0) % 360.0) - 180.0
    return lat, lon


def assemble_from_npy(pred_dir: Path, date_str: str, grid_dir: Path) -> xr.Dataset:
    """Build a ``(time, stat, y, x)`` Dataset from the saved stat ``.npy`` files."""
    lat, lon = load_grid(grid_dir)
    ny, nx = lat.shape
    print(f"  grid: {ny} x {nx} (from corrdiff_output_lat/lon.npy)")

    # var -> stat -> {hour: path}
    found: dict[str, dict[str, dict[int, Path]]] = {}
    for f in sorted(pred_dir.glob(f"{date_str}_*.npy")):
        m = FILE_RE.match(f.name)
        if not m:
            continue  # skips raw _sample##.npy and anything unexpected
        stat = STAT_LABEL.get(m.group("stat"), m.group("stat"))
        found.setdefault(m.group("var"), {}).setdefault(stat, {})[int(m.group("h"))] = f

    if not found:
        raise SystemExit(f"No ensemble-stat .npy files for {date_str} in {pred_dir}")

    # Shared axes across all variables (one selector dim for the whole store).
    stats = sorted({s for v in found.values() for s in v}, key=_stat_sort_key)
    hours = sorted({h for v in found.values() for s in v.values() for h in s})
    init = np.datetime64(f"{date_str}T00:00")
    time = np.array([init + np.timedelta64(int(h), "h") for h in hours])

    data = {}
    for var in sorted(found, key=lambda v: _VAR_ORDER.get(v, 99)):
        cube = np.empty((len(hours), len(stats), ny, nx), dtype="float32")
        for si, s in enumerate(stats):
            for hi, h in enumerate(hours):
                path = found[var].get(s, {}).get(h)
                if path is None:
                    raise SystemExit(
                        f"Missing {var} stat '{s}' at H{h:03d} — every variable must "
                        "have the same stats/hours to share one 'stat' dimension."
                    )
                field = np.load(path)
                if field.shape != (ny, nx):
                    raise SystemExit(
                        f"Shape mismatch: {path.name} is {field.shape} but the grid "
                        f"(corrdiff_output_lat/lon.npy) is {(ny, nx)}. The grid files "
                        "must match the CorrDiff output resolution."
                    )
                cube[hi, si] = field
        da = xr.DataArray(
            cube, dims=("time", "stat", "y", "x"),
            coords={"time": time, "stat": stats},
        )
        da.attrs = VAR_ATTRS.get(var, {})
        data[var] = da

    ds = xr.Dataset(data)
    ds = ds.assign_coords(lat=(("y", "x"), lat), lon=(("y", "x"), lon))
    ds["stat"].attrs = {"long_name": "ensemble statistic"}
    ds["lat"].attrs = {"units": "degrees_north", "long_name": "latitude"}
    ds["lon"].attrs = {"units": "degrees_east", "long_name": "longitude"}
    return ds


def _finalize(ds: xr.Dataset, dest: Path) -> None:
    """Chunk per-timestep and write a consolidated store (iCHARM format)."""
    ds = ds.chunk({"time": 1, "stat": -1, "y": -1, "x": -1})
    for v in ds.data_vars:  # CF hint that lat/lon are 2-D auxiliary coords
        ds[v].attrs["coordinates"] = "lat lon"

    print(f"Writing {dest}")
    print(f"  dims: {dict(ds.sizes)}  vars: {len(ds.data_vars)}")
    valid = ds["time"].values
    print(f"  time: {valid[0]} .. {valid[-1]} ({len(valid)} steps)")
    print(f"  stat: {list(ds['stat'].values)}")
    ds.to_zarr(dest, mode="w", consolidated=True)
    print("Done.")


def convert(
    pred_dir: str | Path,
    date_str: str | None = None,
    grid_dir: str | Path = DEFAULT_GRID_DIR,
    out: str | Path | None = None,
) -> Path:
    """Assemble a CorrDiff prediction folder into an iCHARM Zarr and write it.

    ``date_str`` defaults to the single date inferred from the filenames;
    ``grid_dir`` defaults to the repo root (holding the grid files); ``out``
    defaults to ``<pred_dir_parent>/corrdiff_<date>_map.zarr``. Returns the
    written store path.
    """
    pred_dir = Path(pred_dir)
    grid_dir = Path(grid_dir)
    if not pred_dir.is_dir():
        raise SystemExit(f"pred_dir not found: {pred_dir}")
    date_str = date_str or infer_date(pred_dir)
    dest = Path(out) if out else pred_dir.parent / f"corrdiff_{date_str}_map.zarr"

    print(f"Assembling CorrDiff {date_str} from {pred_dir}")
    ds = assemble_from_npy(pred_dir, date_str, grid_dir)
    print(f"  in: dims {dict(ds.sizes)}  vars {list(ds.data_vars)}")
    _finalize(ds, dest)
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pred_dir",
                        help="Folder of CorrDiff .npy outputs (the <YYYY-MM-DD> folder).")
    parser.add_argument("--grid-dir", default=str(DEFAULT_GRID_DIR),
                        help="Folder with corrdiff_output_lat.npy / corrdiff_output_lon.npy "
                             f"(default: {DEFAULT_GRID_DIR}).")
    parser.add_argument("--date", help="YYYY-MM-DD (default: inferred from filenames).")
    parser.add_argument("--out", help="Output .zarr path (default: "
                        "<pred_dir_parent>/corrdiff_<date>_map.zarr).")
    args = parser.parse_args()

    convert(args.pred_dir, date_str=args.date, grid_dir=args.grid_dir, out=args.out)


if __name__ == "__main__":
    main()
