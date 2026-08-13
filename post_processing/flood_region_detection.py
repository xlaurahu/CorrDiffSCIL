"""Flood-region detection from CorrDiff ensemble output.

Ports ``FloodRegionDetection.ipynb`` into a reusable script. It builds the 24-hr
storm total from the CorrDiff ensemble, compares it against an April
climatology to get an anomaly **z-score**, then produces a ranked table of
flood-risk regions two ways:

* **Automatic** (default) — flag cells above ``z_threshold`` inside the domain,
  close small gaps, group contiguous cells into regions (connected components),
  and drop regions smaller than ``min_area_km2``.
* **Manual** (``manual=True``) — skip detection and score a user-supplied list of
  lat/lon boxes instead, using the exact same statistics.

Either way every region gets: area, centroid, bounding box, mean/max precip,
mean/max z-score, mean anomaly, and mean ensemble std — written to
``flood_regions.csv`` and (optionally) plotted.

Data layout matches the notebook: per-hour ensemble-mean / sample ``.npy`` files
named ``{prefix}_H{H:03d}_ensemble_mean.npy`` / ``{prefix}_H{H:03d}_sample{i:02d}.npy``
(default ``prefix="04"``). For the newer backend naming pass ``prefix="2025-04-04_tp"``.

Usage (host, ephemeral deps):
    uv run --with numpy --with scipy --with pandas --with xarray --with requests \\
           --with matplotlib --with cartopy --with rioxarray \\
        python backend/flood_region_detection.py \\
            --save-dir /.../OhioRiver --year 2025 --month 4 --day 4

    # Manual regions (name:lat_min,lat_max,lon_min,lon_max ; ...):
        ... --manual --regions "OhioValley:36,39,-89,-85; Cumberland:35.5,37,-88,-85"
"""

from __future__ import annotations

import argparse
import glob
import os
from pathlib import Path

import numpy as np

DEFAULT_HOURS = [3, 6, 9, 12, 15, 18, 21, 24]
CONUS = (24.0, 50.0, -125.0, -66.0)   # lat_min, lat_max, lon_min, lon_max
CELL_KM = 3.0                          # CorrDiff grid spacing


# --------------------------------------------------------------------------- #
# Grid + storm total
# --------------------------------------------------------------------------- #

def load_grid(grid_dir, lat_file="corrdiff_output_lat.npy", lon_file="corrdiff_output_lon.npy"):
    """Load lat/lon grids; longitude converted 0-360 -> -180..180."""
    grid_dir = Path(grid_dir)
    lat = np.load(grid_dir / lat_file)
    lon = np.load(grid_dir / lon_file) - 360.0
    return lat, lon


def build_storm_total(save_dir, hours, prefix, grid_shape):
    """Return (storm_total, ensemble_std) over the forecast windows (mm).

    Each hourly tp is x3 for its 3-hr accumulation; the windows are summed for
    the event total. Ensemble std is the per-cell std of the per-sample totals.
    """
    save_dir = Path(save_dir)
    storm_total = np.zeros(grid_shape)
    for H in hours:
        storm_total += np.load(save_dir / f"{prefix}_H{H:03d}_ensemble_mean.npy") * 3.0

    n_samples = len(glob.glob(str(save_dir / f"{prefix}_H{hours[0]:03d}_sample*.npy")))
    if n_samples == 0:
        return storm_total, np.zeros(grid_shape)

    sample_totals = np.zeros((n_samples, *grid_shape))
    for H in hours:
        for i in range(n_samples):
            sample_totals[i] += np.load(save_dir / f"{prefix}_H{H:03d}_sample{i:02d}.npy") * 3.0
    return storm_total, sample_totals.std(axis=0)


# --------------------------------------------------------------------------- #
# Climatology (PRISM -> NOAA PSL -> spatial fallback), cached
# --------------------------------------------------------------------------- #

def _regrid(src_lats, src_lons, src_vals, dst_lat, dst_lon):
    from scipy.interpolate import RegularGridInterpolator
    if src_lats[0] > src_lats[-1]:
        src_lats = src_lats[::-1]
        src_vals = src_vals[::-1, :]
    src_vals = np.where(np.isnan(src_vals), 0.0, src_vals)
    fn = RegularGridInterpolator((src_lats, src_lons), src_vals,
                                 method="linear", bounds_error=False, fill_value=0.0)
    return fn(np.column_stack([dst_lat.ravel(), dst_lon.ravel()])).reshape(dst_lat.shape)


def _try_prism(out_dir):
    import requests, zipfile, gzip, tarfile, io
    import rioxarray as rxr
    bil_dir = os.path.join(out_dir, "prism_april")
    os.makedirs(bil_dir, exist_ok=True)
    bil_hits = glob.glob(os.path.join(bil_dir, "*.bil"))
    if not bil_hits:
        print("  Downloading PRISM...")
        r = requests.get("https://services.nacse.org/prism/data/public/normals/800m/ppt/04", timeout=120)
        r.raise_for_status()
        content = r.content
        magic = content[:4]
        if magic[:4] == b"PK\x03\x04":
            zipfile.ZipFile(io.BytesIO(content)).extractall(bil_dir)
        elif magic[:2] == b"\x1f\x8b":
            try:
                with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as tar:
                    tar.extractall(bil_dir)
            except tarfile.TarError:
                open(os.path.join(bil_dir, "prism_ppt.bil"), "wb").write(gzip.decompress(content))
        else:
            raise ValueError(f"Unknown PRISM format: {magic.hex()}")
        bil_hits = glob.glob(os.path.join(bil_dir, "*.bil"))
        if not bil_hits:
            raise FileNotFoundError("No .bil after PRISM extraction.")
    da = rxr.open_rasterio(bil_hits[0]).squeeze()
    da = da.where(da != da.rio.nodata)
    print(f"  PRISM loaded: {os.path.basename(bil_hits[0])}")
    return da["y"].values, da["x"].values, da.values


def _try_noaa_psl(out_dir):
    import requests
    import xarray as xr
    nc_path = os.path.join(out_dir, "prate_ltm.nc")
    if not os.path.exists(nc_path):
        print("  Downloading NOAA PSL LTM (~6 MB)...")
        r = requests.get(
            "https://downloads.psl.noaa.gov/Datasets/ncep.reanalysis.derived/surface_gauss/prate.sfc.mon.ltm.1991-2020.nc",
            timeout=180)
        r.raise_for_status()
        open(nc_path, "wb").write(r.content)
    ds = xr.open_dataset(nc_path)
    prate = ds["prate"].isel(time=3)          # April
    lats = ds["lat"].values
    lons = ds["lon"].values
    lons = np.where(lons > 180, lons - 360, lons)
    idx = np.argsort(lons)
    vals = prate.values[:, idx] * 86400 * 30  # kg/m^2/s -> mm/month
    print(f"  NOAA PSL loaded; April {np.nanmin(vals):.1f}-{np.nanmax(vals):.1f} mm/month")
    return lats, lons[idx], vals


def get_climatology(out_dir, lat_grid, lon_grid, domain, cache=True):
    """Return (climo_24hr, climo_std): the April 24-hr mean precip on the grid."""
    cache_path = os.path.join(out_dir, "climo_24hr.npy")
    if cache and os.path.exists(cache_path):
        print(f"Loading cached climatology: {cache_path}")
        climo_24hr = np.load(cache_path)
    else:
        src = None
        for name, fn in [("PRISM", _try_prism), ("NOAA PSL", _try_noaa_psl)]:
            print(f"Trying {name}...")
            try:
                src = fn(out_dir)
                print(f"Source: {name}")
                break
            except Exception as e:  # noqa: BLE001
                print(f"  {name} failed: {e}")

        if src is not None:
            climo_24hr = _regrid(*src, lat_grid, lon_grid) / 30.0   # month -> per-day
        else:
            print("Using hardcoded spatial estimate.")
            lat_min, lat_max, lon_min, lon_max = domain
            sw, se, nw, ne = 4.5, 3.8, 2.8, 2.5
            lat_f = np.clip((lat_grid - lat_min) / (lat_max - lat_min), 0, 1)
            lon_f = np.clip((lon_grid - lon_min) / (lon_max - lon_min), 0, 1)
            climo_24hr = (sw * (1 - lat_f) * (1 - lon_f) + se * (1 - lat_f) * lon_f
                          + nw * lat_f * (1 - lon_f) + ne * lat_f * lon_f)
        np.save(cache_path, climo_24hr)
        print(f"Cached -> {cache_path}")

    climo_std = np.where(climo_24hr > 0.5, climo_24hr * 0.5, 0.5)
    return climo_24hr, climo_std


# --------------------------------------------------------------------------- #
# Region selection
# --------------------------------------------------------------------------- #

def domain_mask(lat_grid, lon_grid, domain):
    lat_min, lat_max, lon_min, lon_max = domain
    return ((lat_grid >= lat_min) & (lat_grid <= lat_max)
            & (lon_grid >= lon_min) & (lon_grid <= lon_max))


def detect_regions_auto(z_score, in_box, z_threshold=2.0, close_radius=20,
                        min_area_km2=1000, cell_km=CELL_KM):
    """Auto-detect flood regions as connected components of z > threshold.

    Returns a list of ``(name, mask)`` — name is None (ranked later).
    """
    from scipy.ndimage import label, binary_closing
    flagged = in_box & (z_score > z_threshold)
    struct = np.ones((close_radius * 2 + 1, close_radius * 2 + 1), dtype=bool)
    closed = binary_closing(flagged, structure=struct)
    labeled, n_raw = label(closed, structure=np.ones((3, 3)))
    print(f"Raw connected regions: {n_raw}")

    min_cells = max(1, int(min_area_km2 // (cell_km ** 2)))
    masks = []
    for rid in range(1, n_raw + 1):
        m = labeled == rid
        if m.sum() >= min_cells:
            masks.append((None, m))
    print(f"Regions after size filter (>= {min_area_km2} km^2): {len(masks)}")
    return masks


def _parse_box(box):
    """Accept a dict or (name?, lat_min, lat_max, lon_min, lon_max) tuple/list."""
    if isinstance(box, dict):
        return (box.get("name"),
                float(box["lat_min"]), float(box["lat_max"]),
                float(box["lon_min"]), float(box["lon_max"]))
    box = list(box)
    if len(box) == 5:
        name, lat_min, lat_max, lon_min, lon_max = box
    elif len(box) == 4:
        name, (lat_min, lat_max, lon_min, lon_max) = None, box
    else:
        raise ValueError(f"Region box needs 4 or 5 values, got {box!r}")
    return name, float(lat_min), float(lat_max), float(lon_min), float(lon_max)


def regions_from_boxes(boxes, lat_grid, lon_grid, in_box=None):
    """Build ``(name, mask)`` for user-supplied lat/lon boxes (manual mode)."""
    masks = []
    for k, box in enumerate(boxes, start=1):
        name, lat_min, lat_max, lon_min, lon_max = _parse_box(box)
        m = ((lat_grid >= lat_min) & (lat_grid <= lat_max)
             & (lon_grid >= lon_min) & (lon_grid <= lon_max))
        if in_box is not None:
            m &= in_box
        if m.sum() == 0:
            print(f"  warning: manual region {name or k} has no grid cells in-domain.")
        masks.append((name or f"region_{k}", m))
    return masks


def regions_dataframe(masks, lat_grid, lon_grid, storm_total, z_score, anomaly,
                      ensemble_std, cell_km=CELL_KM, rank_by="max_precip_mm"):
    """Compute per-region stats -> (DataFrame, masks_sorted aligned to rows)."""
    import pandas as pd
    records = []
    for i, (name, mask) in enumerate(masks, start=1):
        if mask.sum() == 0:
            continue
        clats, clons = lat_grid[mask], lon_grid[mask]
        cprec, czsc = storm_total[mask], z_score[mask]
        canom, cstd = anomaly[mask], ensemble_std[mask]
        records.append({
            "region_id": i,
            "name": name or f"region_{i}",
            "area_km2": int(mask.sum() * cell_km ** 2),
            "centroid_lat": round(float(clats.mean()), 3),
            "centroid_lon": round(float(clons.mean()), 3),
            "lat_min": round(float(clats.min()), 3),
            "lat_max": round(float(clats.max()), 3),
            "lon_min": round(float(clons.min()), 3),
            "lon_max": round(float(clons.max()), 3),
            "mean_precip_mm": round(float(cprec.mean()), 1),
            "max_precip_mm": round(float(cprec.max()), 1),
            "mean_zscore": round(float(czsc.mean()), 2),
            "max_zscore": round(float(czsc.max()), 2),
            "mean_anomaly_mm": round(float(canom.mean()), 1),
            "mean_ens_std_mm": round(float(cstd.mean()), 1),
            "_mask": mask,
        })
    if not records:
        return pd.DataFrame(), []

    records.sort(key=lambda r: r[rank_by], reverse=True)
    masks_sorted = [r.pop("_mask") for r in records]
    df = pd.DataFrame(records).reset_index(drop=True)
    df.index += 1  # 1-indexed rank
    return df, masks_sorted


def region_boxes(df, named=False):
    """Region bounding boxes as a sequence for ``corrdiff_plots.plot_prediction``.

    Each box is ``(lat_min, lat_max, lon_min, lon_max)`` — exactly the
    ``region=`` format ``plot_prediction`` expects. With ``named=True`` each is
    ``(name, lat_min, lat_max, lon_min, lon_max)`` (what ``plot_regions`` accepts).
    Returned in the DataFrame's ranked order.
    """
    boxes = []
    for _, row in df.iterrows():
        box = (float(row["lat_min"]), float(row["lat_max"]),
               float(row["lon_min"]), float(row["lon_max"]))
        boxes.append((str(row["name"]), *box) if named else box)
    return boxes


# --------------------------------------------------------------------------- #
# Plots (optional; mirror the notebook)
# --------------------------------------------------------------------------- #

def _add_map(ax, domain, ccrs, cfeature):
    lat_min, lat_max, lon_min, lon_max = domain
    ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())
    ax.add_feature(cfeature.LAND, facecolor="#f0f0f0", zorder=0)
    ax.add_feature(cfeature.STATES, linewidth=0.5, edgecolor="black")
    ax.add_feature(cfeature.BORDERS, linewidth=0.8, edgecolor="black")
    ax.add_feature(cfeature.COASTLINE, linewidth=0.7)


def plot_overview(df, masks_sorted, lat_grid, lon_grid, storm_total, z_score,
                  domain, out_path, date_label, z_threshold):
    import matplotlib.pyplot as plt
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    from matplotlib.patches import Rectangle

    in_box = domain_mask(lat_grid, lon_grid, domain)
    display_mask = np.full(lat_grid.shape, np.nan)
    for rank, m in enumerate(masks_sorted, start=1):
        display_mask[m] = rank

    fig, axes = plt.subplots(1, 2, figsize=(20, 6), subplot_kw={"projection": ccrs.PlateCarree()})
    ax = axes[0]
    _add_map(ax, domain, ccrs, cfeature)
    pcm = ax.pcolormesh(lon_grid, lat_grid, storm_total, cmap="Blues",
                        vmin=0, vmax=float(storm_total[in_box].max()),
                        transform=ccrs.PlateCarree(), alpha=0.5)
    plt.colorbar(pcm, ax=ax, label="24-hr storm total (mm)", shrink=0.7)
    if len(df):
        ax.pcolormesh(lon_grid, lat_grid, np.ma.masked_invalid(display_mask),
                      cmap="tab10", vmin=0.5, vmax=len(df) + 0.5,
                      transform=ccrs.PlateCarree(), alpha=0.8)
        for rank, row in df.iterrows():
            # Bounding box of the region, drawn + labelled with its actual bounds.
            ax.add_patch(Rectangle(
                (row["lon_min"], row["lat_min"]),
                row["lon_max"] - row["lon_min"], row["lat_max"] - row["lat_min"],
                linewidth=1.5, edgecolor="red", facecolor="none",
                transform=ccrs.PlateCarree(), zorder=6))
            ax.plot(row["centroid_lon"], row["centroid_lat"], "*", ms=16, color="red",
                    markeredgecolor="white", markeredgewidth=1,
                    transform=ccrs.PlateCarree(), zorder=6)
            ax.text(row["centroid_lon"], row["lat_max"] + 0.15,
                    f"#{rank} {row['max_precip_mm']:.0f}mm\n"
                    f"lat [{row['lat_min']:.2f}, {row['lat_max']:.2f}]\n"
                    f"lon [{row['lon_min']:.2f}, {row['lon_max']:.2f}]",
                    fontsize=7, fontweight="bold", ha="center", va="bottom",
                    transform=ccrs.PlateCarree(), zorder=7,
                    bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="red", alpha=0.85))
    ax.set_title(f"Flood-risk regions - {date_label}")

    ax = axes[1]
    _add_map(ax, domain, ccrs, cfeature)
    pcm2 = ax.pcolormesh(lon_grid, lat_grid, z_score, cmap="RdYlBu_r", vmin=-1, vmax=6,
                         transform=ccrs.PlateCarree())
    plt.colorbar(pcm2, ax=ax, label="Z-score (sigma)", shrink=0.7)
    ax.set_title(f"Z-score (threshold = {z_threshold} sigma)")

    plt.tight_layout()
    plt.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return out_path


def plot_region_detail(df, lat_grid, lon_grid, storm_total, ensemble_std,
                       out_path, date_label, n_show=4):
    import matplotlib.pyplot as plt
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    from matplotlib.patches import Rectangle

    n_show = min(len(df), n_show)
    if n_show == 0:
        return None
    fig, axes = plt.subplots(n_show, 2, figsize=(14, 4.5 * n_show),
                             subplot_kw={"projection": ccrs.PlateCarree()})
    if n_show == 1:
        axes = axes[np.newaxis, :]

    for i, (rank, row) in enumerate(df.head(n_show).iterrows()):
        pad = 1.0
        ext = [row["lon_min"] - pad, row["lon_max"] + pad,
               row["lat_min"] - pad, row["lat_max"] + pad]
        for j, (field, label_txt, vmax_val, cmap) in enumerate([
            (storm_total, "24-hr storm total (mm)", row["max_precip_mm"], "Blues"),
            (ensemble_std, "Ensemble std (mm)", None, "Oranges"),
        ]):
            ax = axes[i, j]
            ax.set_extent(ext, crs=ccrs.PlateCarree())
            ax.add_feature(cfeature.STATES, linewidth=0.7, edgecolor="black")
            ax.add_feature(cfeature.LAND, facecolor="#f5f5f5", zorder=0)
            pcm = ax.pcolormesh(lon_grid, lat_grid, field, cmap=cmap, vmin=0, vmax=vmax_val,
                                transform=ccrs.PlateCarree())
            plt.colorbar(pcm, ax=ax, label=label_txt, shrink=0.75)
            ax.add_patch(Rectangle((row["lon_min"], row["lat_min"]),
                                   row["lon_max"] - row["lon_min"], row["lat_max"] - row["lat_min"],
                                   linewidth=2, edgecolor="red", facecolor="none",
                                   transform=ccrs.PlateCarree(), zorder=5))
            ax.set_title(f"Region #{rank} ({row['name']}) | {row['area_km2']:,} km^2 | "
                         f"peak {row['max_precip_mm']:.0f}mm z{row['max_zscore']:.1f}\n"
                         f"lat [{row['lat_min']:.2f}, {row['lat_max']:.2f}]  "
                         f"lon [{row['lon_min']:.2f}, {row['lon_max']:.2f}]", fontsize=9)

    plt.suptitle(f"Top-{n_show} Flood-Risk Regions - {date_label}", fontsize=14, fontweight="bold", y=1.01)
    plt.tight_layout()
    plt.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return out_path


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def run_flood_detection(
    save_dir,
    year, month, day,
    hours=DEFAULT_HOURS,
    grid_dir=".",
    prefix="04",
    domain=CONUS,
    z_threshold=2.0,
    min_area_km2=1000,
    close_radius=20,
    manual=False,
    regions=None,
    out_dir=None,
    make_plots=True,
):
    """Run flood-region detection (auto or manual) and return the regions table."""
    out_dir = out_dir or save_dir
    os.makedirs(out_dir, exist_ok=True)
    date_label = f"{year:04d}-{month:02d}-{day:02d}"

    lat_grid, lon_grid = load_grid(grid_dir)
    print(f"Grid {lat_grid.shape} | lat {lat_grid.min():.1f}..{lat_grid.max():.1f} "
          f"| lon {lon_grid.min():.1f}..{lon_grid.max():.1f}")

    storm_total, ensemble_std = build_storm_total(save_dir, hours, prefix, lat_grid.shape)
    print(f"Storm total max {storm_total.max():.1f} mm | ens std max {ensemble_std.max():.1f} mm")

    climo_24hr, climo_std = get_climatology(out_dir, lat_grid, lon_grid, domain)
    anomaly = storm_total - climo_24hr
    z_score = anomaly / climo_std
    in_box = domain_mask(lat_grid, lon_grid, domain)

    if manual:
        if not regions:
            raise ValueError("manual=True requires regions=[...] (lat/lon boxes).")
        print(f"Manual mode: scoring {len(regions)} user region(s).")
        masks = regions_from_boxes(regions, lat_grid, lon_grid, in_box)
    else:
        print(f"Auto mode: z > {z_threshold}, close_radius {close_radius}, min_area {min_area_km2} km^2.")
        masks = detect_regions_auto(z_score, in_box, z_threshold, close_radius, min_area_km2)

    df, masks_sorted = regions_dataframe(masks, lat_grid, lon_grid, storm_total,
                                         z_score, anomaly, ensemble_std)
    print(f"\n{len(df)} region(s):")
    if len(df):
        cols = ["name", "area_km2", "centroid_lat", "centroid_lon",
                "max_precip_mm", "mean_precip_mm", "max_zscore", "mean_anomaly_mm", "mean_ens_std_mm"]
        print(df[cols].to_string())

        # Report the actual bounds and attach a plot_prediction-ready sequence.
        df.attrs["boxes"] = region_boxes(df)                  # (lat_min,lat_max,lon_min,lon_max)
        df.attrs["named_boxes"] = region_boxes(df, named=True)
        print("\nRegion bounds  (lat_min, lat_max, lon_min, lon_max) "
              "-- pass straight to corrdiff_plots.plot_prediction(region=...):")
        for rank, row in df.iterrows():
            print(f"  #{rank} {row['name']}: "
                  f"({row['lat_min']}, {row['lat_max']}, {row['lon_min']}, {row['lon_max']})")
        print(f"\n  df.attrs['boxes'] = {df.attrs['boxes']}")

    csv_path = os.path.join(out_dir, "flood_regions.csv")
    df.to_csv(csv_path, index=True)
    print(f"\nSaved -> {csv_path}")

    if make_plots and len(df):
        try:
            plot_overview(df, masks_sorted, lat_grid, lon_grid, storm_total, z_score,
                          domain, os.path.join(out_dir, "flood_regions_map.png"),
                          date_label, z_threshold)
            plot_region_detail(df, lat_grid, lon_grid, storm_total, ensemble_std,
                               os.path.join(out_dir, "flood_regions_detail.png"), date_label)
            print("Saved plots -> flood_regions_map.png, flood_regions_detail.png")
        except Exception as e:  # noqa: BLE001
            print(f"Plotting skipped ({type(e).__name__}: {e}).")

    return df


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _parse_regions_arg(text):
    """Parse 'name:lat_min,lat_max,lon_min,lon_max; ...' into box tuples."""
    boxes = []
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, nums = (chunk.split(":", 1) + [None])[:2] if ":" in chunk else (None, chunk)
        vals = [float(x) for x in nums.replace(",", " ").split()]
        if len(vals) != 4:
            raise ValueError(f"Region '{chunk}' needs lat_min,lat_max,lon_min,lon_max")
        boxes.append((name, *vals))
    return boxes


def main(argv=None):
    p = argparse.ArgumentParser(description="Detect (or score) CorrDiff flood regions.")
    p.add_argument("--save-dir", required=True, help="Folder with the CorrDiff .npy outputs.")
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--month", type=int, required=True)
    p.add_argument("--day", type=int, required=True)
    p.add_argument("--hours", default=",".join(map(str, DEFAULT_HOURS)),
                   help="Comma-separated forecast hours.")
    p.add_argument("--grid-dir", default=".", help="Folder with corrdiff_output_lat/lon.npy.")
    p.add_argument("--prefix", default="04",
                   help="Filename prefix before _H###_ (default '04'; new naming: '<date>_tp').")
    p.add_argument("--domain", default=",".join(map(str, CONUS)),
                   help="lat_min,lat_max,lon_min,lon_max (default CONUS).")
    p.add_argument("--z-threshold", type=float, default=2.0)
    p.add_argument("--min-area-km2", type=int, default=1000)
    p.add_argument("--close-radius", type=int, default=20)
    p.add_argument("--manual", action="store_true",
                   help="Score user regions from --regions instead of auto-detecting.")
    p.add_argument("--regions", help="Manual boxes: 'name:lat_min,lat_max,lon_min,lon_max; ...'.")
    p.add_argument("--out-dir", help="Output folder (default = --save-dir).")
    p.add_argument("--no-plots", action="store_true")
    args = p.parse_args(argv)

    hours = [int(h) for h in args.hours.replace(",", " ").split()]
    domain = tuple(float(x) for x in args.domain.replace(",", " ").split())
    regions = _parse_regions_arg(args.regions) if args.regions else None
    if args.manual and not regions:
        p.error("--manual requires --regions")

    run_flood_detection(
        save_dir=args.save_dir, year=args.year, month=args.month, day=args.day,
        hours=hours, grid_dir=args.grid_dir, prefix=args.prefix, domain=domain,
        z_threshold=args.z_threshold, min_area_km2=args.min_area_km2,
        close_radius=args.close_radius, manual=args.manual, regions=regions,
        out_dir=args.out_dir, make_plots=not args.no_plots,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
