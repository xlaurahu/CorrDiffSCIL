"""Convert CorrDiff ensemble output into an iCHARM-ingestible Zarr.

Unlike ``corrdiff_to_zarr.py`` (which keeps the native 2-D curvilinear grid for
the globe renderer), iCHARM's **Data API only accepts a regular 1-D lat/lon
grid** (``database_queries.py`` raises "Only 1D lat/lon grids are supported")
and reads **zarr v2** consolidated stores. So this script:

1. reads the saved ``{date}_{var}_H{H}_{stat}.npy`` fields + the 2-D grid,
2. **regrids** the 3 km Lambert field onto a regular 1-D lat/lon mesh
   (nearest-neighbour by default; linear optional),
3. writes a **zarr v2** store with dims ``(time, lat, lon)`` — ``time`` = the
   3-hourly forecast valid times — that drops into ``backend/datasets/`` and is
   registered with one ``metadata.csv`` row.

For ``tp`` the value is the estimated 3-hour accumulation (``tp * 3``), so the
globe animates 3-hourly precipitation. One stat per store (default the ensemble
mean); run again with ``--stat`` for others.

Usage (host, ephemeral deps):
    uv run --with xarray --with "zarr>=3" --with numpy --with scipy --with dask \\
        python -m icharm.dataset_processing.corrdiff.corrdiff_to_icharm_zarr \\
            /path/to/OhioRiver/2025-04-04 --date 2025-04-04 \\
            --resolution 0.05 \\
            --out /path/to/iCharm/backend/datasets/corrdiff_tp_2025-04-04_map.zarr
"""

from __future__ import annotations

import argparse
import glob
import os
import re
from pathlib import Path

import numpy as np

DEFAULT_GRID_DIR = Path(__file__).resolve().parent  # grid files live alongside this script
STAT_SUFFIX = {  # CLI stat name -> filename suffix
    "mean": "ensemble_mean", "std": "ensemble_std",
    "min": "ensemble_min", "max": "ensemble_max",
}


def load_grid(grid_dir):
    """Load the 2-D CorrDiff grid; normalize lon to -180..180."""
    grid_dir = Path(grid_dir)
    lat = np.load(grid_dir / "corrdiff_output_lat.npy").astype("float64")
    lon = np.load(grid_dir / "corrdiff_output_lon.npy").astype("float64")
    if lat.ndim == 1 or lon.ndim == 1:
        lon, lat = np.meshgrid(lon, lat)
    if float(np.nanmax(lon)) > 180.0:
        lon = ((lon + 180.0) % 360.0) - 180.0
    return lat, lon


def _discover_hours(pred_dir, date_str, var, suffix):
    pat = re.compile(rf"^{re.escape(date_str)}_{re.escape(var)}_H(\d+)_{re.escape(suffix)}\.npy$")
    hours = []
    for f in glob.glob(str(Path(pred_dir) / f"{date_str}_{var}_H*_{suffix}.npy")):
        m = pat.match(os.path.basename(f))
        if m:
            hours.append(int(m.group(1)))
    return sorted(hours)


def build_regridder(lat2d, lon2d, target_lat, target_lon, method="nearest"):
    """Return a function ``field2d -> field(target_lat, target_lon)``.

    The source triangulation / KD-tree is built once and reused for every time
    step. ``nearest`` (KD-tree) is fast and robust; ``linear`` (Delaunay) is
    smoother but heavier to build on the full ~1.9M-point grid.
    """
    src_pts = np.column_stack([lon2d.ravel(), lat2d.ravel()])
    tlon, tlat = np.meshgrid(target_lon, target_lat)
    dst_pts = np.column_stack([tlon.ravel(), tlat.ravel()])
    out_shape = tlat.shape

    if method == "nearest":
        from scipy.spatial import cKDTree
        _, idx = cKDTree(src_pts).query(dst_pts, k=1)

        def regrid(field2d):
            return field2d.ravel()[idx].reshape(out_shape)
    elif method == "linear":
        from scipy.spatial import Delaunay
        from scipy.interpolate import LinearNDInterpolator
        tri = Delaunay(src_pts)

        def regrid(field2d):
            interp = LinearNDInterpolator(tri, field2d.ravel())
            return interp(dst_pts).reshape(out_shape)
    else:
        raise ValueError(f"method must be 'nearest' or 'linear', got {method!r}")

    return regrid


def convert(
    pred_dir,
    date_str,
    out,
    grid_dir=DEFAULT_GRID_DIR,
    var="tp",
    stat="mean",
    resolution=0.05,
    method="nearest",
    scale_3h=None,
):
    """Regrid a CorrDiff variable/stat to a 1-D lat/lon zarr v2 store for iCHARM."""
    import xarray as xr

    suffix = STAT_SUFFIX.get(stat, stat)
    pred_dir = Path(pred_dir)
    hours = _discover_hours(pred_dir, date_str, var, suffix)
    if not hours:
        raise SystemExit(f"No {var} {suffix} files for {date_str} in {pred_dir}")
    if scale_3h is None:
        scale_3h = (var == "tp")  # tp: 1-hr total -> 3-hr estimate

    lat2d, lon2d = load_grid(grid_dir)
    # Regular target grid covering the source extent.
    target_lat = np.arange(np.floor(lat2d.min()), np.ceil(lat2d.max()) + resolution, resolution)
    target_lon = np.arange(np.floor(lon2d.min()), np.ceil(lon2d.max()) + resolution, resolution)
    print(f"Source grid {lat2d.shape} -> target {len(target_lat)} x {len(target_lon)} "
          f"@ {resolution} deg ({method})")

    regrid = build_regridder(lat2d, lon2d, target_lat, target_lon, method)

    init = np.datetime64(f"{date_str}T00:00")
    times = np.array([init + np.timedelta64(int(h), "h") for h in hours])
    cube = np.empty((len(hours), len(target_lat), len(target_lon)), dtype="float32")
    for i, h in enumerate(hours):
        field = np.load(pred_dir / f"{date_str}_{var}_H{h:03d}_{suffix}.npy").astype("float64")
        if scale_3h:
            field = field * 3.0
        cube[i] = regrid(field).astype("float32")
        print(f"  H{h:03d}: max {np.nanmax(cube[i]):.1f}")

    units = "mm" if var == "tp" else ""
    long_name = ("estimated 3-hr total precipitation" if var == "tp" else var) + f" ({stat})"
    da = xr.DataArray(
        cube, dims=("time", "lat", "lon"),
        coords={"time": times, "lat": target_lat.astype("float64"),
                "lon": target_lon.astype("float64")},
        name=var, attrs={"units": units, "long_name": long_name},
    )
    da["lat"].attrs = {"units": "degrees_north", "long_name": "latitude"}
    da["lon"].attrs = {"units": "degrees_east", "long_name": "longitude"}
    ds = da.to_dataset()
    ds.attrs = {"title": f"CorrDiff {var} forecast {date_str}",
                "source": "Earth2 CorrDiff US (GEFS->HRRR)", "stat": stat}

    # Chunk (time=1, full lat/lon) like iCHARM's *_map.zarr, via encoding so we
    # don't need dask just to write the store.
    encoding = {var: {"chunks": (1, len(target_lat), len(target_lon))}}
    dest = Path(out)
    print(f"Writing zarr v2 -> {dest}")
    ds.to_zarr(dest, mode="w", consolidated=True, zarr_format=2, encoding=encoding)
    print(f"Done. dims={dict(ds.sizes)}  time {times[0]}..{times[-1]} ({len(times)} steps)")
    return dest


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("pred_dir", help="Folder with the CorrDiff .npy outputs (the <YYYY-MM-DD> folder).")
    p.add_argument("--date", required=True, help="YYYY-MM-DD.")
    p.add_argument("--out", required=True, help="Output .zarr path (put it in iCHARM's backend/datasets/).")
    p.add_argument("--grid-dir", default=str(DEFAULT_GRID_DIR),
                   help="Folder with corrdiff_output_lat/lon.npy.")
    p.add_argument("--var", default="tp")
    p.add_argument("--stat", default="mean", help="mean|std|min|max|p## (default mean).")
    p.add_argument("--resolution", type=float, default=0.05, help="Target grid spacing in degrees.")
    p.add_argument("--method", default="nearest", choices=["nearest", "linear"])
    p.add_argument("--no-scale-3h", action="store_true",
                   help="Keep raw 1-hr tp instead of the x3 3-hr estimate.")
    args = p.parse_args(argv)

    convert(
        pred_dir=args.pred_dir, date_str=args.date, out=args.out, grid_dir=args.grid_dir,
        var=args.var, stat=args.stat, resolution=args.resolution, method=args.method,
        scale_3h=(False if args.no_scale_3h else None),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
