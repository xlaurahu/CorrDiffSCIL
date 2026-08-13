"""CorrDiff post-analysis visualization.

Turns the saved CorrDiff predictions (the ``.npy`` files written by
``corrdiff_predict.run_prediction``) into map figures. This is the first, basic
layer of the post-analysis toolkit — more analyses will be added over time, so
the plotting primitives here are meant to be reused.

What it does now
----------------
1. **CONUS maps in Lambert Conformal.** Each ensemble-mean field is drawn on a
   Lambert Conformal projection (matching the CorrDiff/HRRR CONUS grid) using
   the 2-D ``corrdiff_output_lat.npy`` / ``corrdiff_output_lon.npy`` grid files.
   Every figure is titled with the variable's full name, the forecast hour, and
   the initial-condition date/time.

2. **Region masking.** Pass ``region=(lat_min, lat_max, lon_min, lon_max)`` to
   mask everything outside that box and zoom the map to it. The region is shown
   in the title.

Variable handling
-----------------
``tp`` is the previous-1-hour total precipitation (mm). We only have it every
3 h, so the 3-hour accumulation is *estimated* as ``tp * 3`` (the two unobserved
hours are assumed equal to the reported one). ``tp`` uses the GPCP colour scale
and also gets a storm-total figure. Other variables are drawn as-is with a
per-variable colour map.

A small CLI is provided (``python corrdiff_plots.py --help``) for quick testing
from a terminal.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
from pathlib import Path
from typing import Sequence

import numpy as np

import matplotlib
matplotlib.use("Agg")  # headless: render to file, no display needed
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors


# --------------------------------------------------------------------------- #
# Projection (Lambert Conformal, CONUS / HRRR-like)
# --------------------------------------------------------------------------- #
# Defaults follow the HRRR CONUS grid. The data itself is placed by its true
# lat/lon (via a PlateCarree transform), so these only define the display canvas.
DEFAULT_LAMBERT = dict(
    central_longitude=-97.5,
    central_latitude=38.5,
    standard_parallels=(38.5, 38.5),
)


# --------------------------------------------------------------------------- #
# Variable metadata + colour scales
# --------------------------------------------------------------------------- #

GPCP_COLORS = [
    "#dfc2a5", "#7ec9d0", "#5eb35e", "#99cc33", "#f2f22e",
    "#e6e600", "#ff9999", "#ff4d4d", "#cc0000", "#993366", "#1a1a1a",
]
_GPCP_CMAP = mcolors.LinearSegmentedColormap.from_list("gpcp_cont", GPCP_COLORS)

TP_HOUR_LEVELS = np.linspace(0, 40, 11)    # per 3-hr estimate (mm)
TP_TOTAL_LEVELS = np.linspace(0, 155, 11)  # storm total (mm)

# name = human-readable label; units for the colour bar; cmap; kind hints how to
# build the colour normalization ("seq" | "diverging" | "categorical").
VAR_META = {
    "u10m":  {"name": "10 m U-wind",              "units": "m/s", "cmap": "RdBu_r",  "kind": "diverging"},
    "v10m":  {"name": "10 m V-wind",              "units": "m/s", "cmap": "RdBu_r",  "kind": "diverging"},
    "t2m":   {"name": "2 m Temperature",          "units": "K",   "cmap": "coolwarm","kind": "seq"},
    "tp":    {"name": "Total Precipitation",      "units": "mm",  "cmap": None,      "kind": "precip"},
    "csnow": {"name": "Categorical Snow",         "units": "",    "cmap": "Blues",   "kind": "categorical"},
    "cicep": {"name": "Categorical Ice Pellets",  "units": "",    "cmap": "Purples", "kind": "categorical"},
    "cfrzr": {"name": "Categorical Freezing Rain","units": "",    "cmap": "PuRd",    "kind": "categorical"},
    "crain": {"name": "Categorical Rain",         "units": "",    "cmap": "Greens",  "kind": "categorical"},
}


def _gpcp_cmap_norm(levels):
    cmap = mcolors.ListedColormap(_GPCP_CMAP(np.linspace(0, 1, len(levels) - 1)))
    norm = mcolors.BoundaryNorm(levels, ncolors=cmap.N)
    return cmap, norm


def masked_value_range(fields, grid, region):
    """Min/max of field values inside ``region``, ignoring NaNs.

    ``fields`` is an iterable of 2-D arrays (e.g. the per-hour tp fields already
    scaled by 3). If ``region=(lat_min, lat_max, lon_min, lon_max)`` and a grid
    are given, only cells inside the box are considered; otherwise the whole
    field is used. Returns ``(vmin, vmax)`` or ``None`` if there are no finite
    values.
    """
    inside = None
    if region is not None and grid is not None:
        lat2d, lon2d = grid
        lat_min, lat_max, lon_min, lon_max = region
        inside = ((lat2d >= lat_min) & (lat2d <= lat_max)
                  & (lon2d >= lon_min) & (lon2d <= lon_max))

    vmin, vmax = np.inf, -np.inf
    for f in fields:
        v = f[inside] if inside is not None else f
        v = v[np.isfinite(v)]
        if v.size:
            vmin = min(vmin, float(v.min()))
            vmax = max(vmax, float(v.max()))
    return None if vmin > vmax else (vmin, vmax)


def precip_cmap_norm(fields, grid=None, region=None, adapt=True,
                     default_levels=None, n=11):
    """GPCP colormap + norm for a precipitation graph, scaled to the data.

    Use this for **all** precip graphs so they share one consistent colour scale.
    When ``adapt`` is True the GPCP levels span the min..max of ``fields`` (only
    the cells inside ``region`` if a region+grid are given); otherwise it falls
    back to ``default_levels`` (``TP_HOUR_LEVELS`` if unset). ``fields`` is an
    iterable of 2-D arrays. Returns ``(cmap, norm)``.
    """
    default_levels = TP_HOUR_LEVELS if default_levels is None else default_levels
    if adapt:
        rng = masked_value_range(fields, grid, region)
        if rng is not None:
            vmin, vmax = max(0.0, rng[0]), rng[1]   # precipitation floored at 0
            if vmax > vmin:
                return _gpcp_cmap_norm(np.linspace(vmin, vmax, n))
    return _gpcp_cmap_norm(default_levels)


def _meta(var):
    return VAR_META.get(var, {"name": var, "units": "", "cmap": "viridis", "kind": "seq"})


def _cbar_label(var):
    m = _meta(var)
    if var == "tp":
        return "Estimated 3-hr precipitation (mm)"
    return f"{m['name']} ({m['units']})" if m["units"] else m["name"]


def _cmap_norm_for(var, data):
    """Build (cmap, norm) for a non-tp variable from the displayed data range."""
    m = _meta(var)
    finite = data[np.isfinite(data)]
    if finite.size == 0:
        return m["cmap"], None
    if m["kind"] == "diverging":
        vmax = float(np.max(np.abs(finite))) or 1.0
        return m["cmap"], mcolors.Normalize(vmin=-vmax, vmax=vmax)
    if m["kind"] == "categorical":
        return m["cmap"], mcolors.Normalize(vmin=0.0, vmax=1.0)
    return m["cmap"], mcolors.Normalize(vmin=float(finite.min()), vmax=float(finite.max()))


# --------------------------------------------------------------------------- #
# Grid / cartopy helpers
# --------------------------------------------------------------------------- #

def load_grid(grid_dir: str | Path | None):
    """Load the CorrDiff output lat/lon grid, or return None if unavailable.

    Expects ``corrdiff_output_lat.npy`` and ``corrdiff_output_lon.npy`` in
    ``grid_dir`` (the fine ~3 km 2-D curvilinear grid, e.g. 1056x1792). Native
    precision (float64) is preserved. Longitudes are normalized to -180..180 only
    if they look like a 0-360 grid. 1-D inputs are promoted to 2-D so pcolormesh
    can place each cell by its true lat/lon. Kept in sync with
    ``corrdiff_to_zarr.load_grid``.
    """
    if not grid_dir:
        return None
    grid_dir = Path(grid_dir)
    try:
        lat = np.load(grid_dir / "corrdiff_output_lat.npy")
        lon = np.load(grid_dir / "corrdiff_output_lon.npy")
    except Exception:
        return None
    if lat.ndim == 1 or lon.ndim == 1:
        lon, lat = np.meshgrid(lon, lat)
    if float(np.nanmax(lon)) > 180.0:  # 0-360 -> -180..180 (handles wrap)
        lon = ((lon + 180.0) % 360.0) - 180.0
    return lat, lon


def _try_cartopy():
    try:
        import cartopy.crs as ccrs
        import cartopy.feature as cfeature
        return ccrs, cfeature
    except Exception:
        return None, None


def _region_label(region) -> str:
    lat_min, lat_max, lon_min, lon_max = region

    def _lat(v):
        return f"{abs(v):.1f}°{'N' if v >= 0 else 'S'}"

    def _lon(v):
        return f"{abs(v):.1f}°{'E' if v >= 0 else 'W'}"

    return f"Region: {_lat(lat_min)}–{_lat(lat_max)}, {_lon(lon_min)}–{_lon(lon_max)}"


# --------------------------------------------------------------------------- #
# Core renderer
# --------------------------------------------------------------------------- #

def _mark_max_star(ax, plot_field, grid, ccrs):
    """Star + label at the maximum of ``plot_field`` (NaNs ignored).

    Uses geographic coords when ``grid``/cartopy are available, else array
    (col, row) coords for the imshow fallback. Returns the (value, lat, lon) or
    None if the field is empty/all-NaN.
    """
    if not np.any(np.isfinite(plot_field)):
        return None
    r, c = np.unravel_index(np.nanargmax(plot_field), plot_field.shape)
    val = float(plot_field[r, c])

    if grid is not None and ccrs is not None:
        lat2d, lon2d = grid
        x, y = float(lon2d[r, c]), float(lat2d[r, c])
        star_kw = dict(transform=ccrs.PlateCarree())
        label = f"max {val:.1f}\n({y:.2f}, {x:.2f})"
    else:
        x, y = float(c), float(r)
        star_kw = {}
        label = f"max {val:.1f}"

    ax.plot(x, y, marker="*", markersize=20, color="blue",
            markeredgecolor="white", markeredgewidth=1.2, zorder=6, **star_kw)
    ax.text(x, y, "  " + label, color="blue", fontsize=9, fontweight="bold",
            ha="left", va="center", zorder=7,
            bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="blue", alpha=0.8),
            **star_kw)
    return val, y, x


def render_field(
    field: np.ndarray,
    grid,
    title: str,
    cbar_label: str,
    cmap,
    norm=None,
    out_path: str | Path | None = None,
    region=None,
    lambert_params: dict | None = None,
    show_max_star: bool = True,
):
    """Render one 2-D field on a Lambert Conformal CONUS map.

    ``grid`` is ``(lat2d, lon2d)`` from :func:`load_grid`. If ``region`` is given
    as ``(lat_min, lat_max, lon_min, lon_max)`` the field is masked outside the
    box and the map is zoomed to it. If cartopy or the grid is missing, falls
    back to a plain ``imshow`` so something still renders.

    ``show_max_star`` (default True) marks the field's maximum with a labelled
    star; set it False to hide the marker. When a ``region`` is set, the max is
    taken over the visible (masked) region only.

    Returns the saved path (if ``out_path``) else the Matplotlib figure.
    """
    ccrs, cfeature = _try_cartopy()

    lat2d = lon2d = None
    if grid is not None:
        lat2d, lon2d = grid

    plot_field = field
    if region is not None and lat2d is not None:
        lat_min, lat_max, lon_min, lon_max = region
        inside = (
            (lat2d >= lat_min) & (lat2d <= lat_max)
            & (lon2d >= lon_min) & (lon2d <= lon_max)
        )
        plot_field = np.where(inside, field, np.nan)

    if grid is not None and ccrs is not None:
        proj = ccrs.LambertConformal(**(lambert_params or DEFAULT_LAMBERT))
        fig, ax = plt.subplots(figsize=(12, 8), subplot_kw={"projection": proj})

        if region is not None:
            lat_min, lat_max, lon_min, lon_max = region
            ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())
        else:
            ax.set_extent(
                [float(lon2d.min()), float(lon2d.max()),
                 float(lat2d.min()), float(lat2d.max())],
                crs=ccrs.PlateCarree(),
            )

        ax.add_feature(cfeature.LAND, facecolor="lightgray", zorder=0)
        ax.add_feature(cfeature.STATES, linewidth=0.6, edgecolor="black", zorder=3)
        ax.add_feature(cfeature.BORDERS, linewidth=0.9, edgecolor="black", zorder=3)
        ax.add_feature(cfeature.COASTLINE, linewidth=0.8, zorder=3)
        ax.gridlines(draw_labels=False, linewidth=0.3, color="gray", alpha=0.4)

        pcm = ax.pcolormesh(
            lon2d, lat2d, plot_field, cmap=cmap, norm=norm,
            transform=ccrs.PlateCarree(), zorder=1,
        )
    else:
        # Fallback: no cartopy/grid -> plain array view.
        fig, ax = plt.subplots(figsize=(12, 8))
        pcm = ax.imshow(plot_field, origin="lower", cmap=cmap, norm=norm, aspect="auto")
        ax.set_xlabel("grid x")
        ax.set_ylabel("grid y")

    if show_max_star:
        _mark_max_star(ax, plot_field, grid if ccrs is not None else None, ccrs)

    cbar = fig.colorbar(pcm, ax=ax, orientation="vertical", pad=0.02, shrink=0.72)
    cbar.set_label(cbar_label, fontsize=11)
    ax.set_title(title, fontsize=13, fontweight="bold", pad=14)

    fig.tight_layout()
    if out_path is None:
        return fig

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(out_path)


# --------------------------------------------------------------------------- #
# High-level: plot a whole prediction
# --------------------------------------------------------------------------- #

def discover(pred_dir: str | Path, date_str: str) -> dict[str, list[int]]:
    """Scan ``pred_dir`` for ``{date}_{var}_H###_ensemble_mean.npy`` files.

    Returns ``{var: [hours...]}`` sorted, so callers can default to "everything
    that was produced".
    """
    pred_dir = Path(pred_dir)
    pat = re.compile(rf"{re.escape(date_str)}_(?P<var>[a-z0-9]+)_H(?P<h>\d+)_ensemble_mean\.npy$")
    found: dict[str, set] = {}
    for f in glob.glob(str(pred_dir / f"{date_str}_*_ensemble_mean.npy")):
        m = pat.search(os.path.basename(f))
        if m:
            found.setdefault(m.group("var"), set()).add(int(m.group("h")))
    return {v: sorted(hs) for v, hs in sorted(found.items())}


def plot_prediction(
    pred_dir: str | Path,
    date_str: str,
    variables: Sequence[str] | None = None,
    hours: Sequence[int] | None = None,
    out_dir: str | Path = "corrdiff_plots",
    grid_dir: str | Path | None = None,
    region=None,
    ic_label: str | None = None,
    lambert_params: dict | None = None,
    show_max_star: bool = True,
    adaptive_scale: bool | None = None,
) -> list[dict]:
    """Render ensemble-mean maps for a completed prediction.

    ``variables`` / ``hours`` default to whatever is found in ``pred_dir``.
    ``region=(lat_min, lat_max, lon_min, lon_max)`` masks + zooms to a sub-box.
    ``show_max_star`` (default True) marks each field's maximum with a star.

    ``adaptive_scale`` controls the ``tp`` colour scale. ``None`` (default) =
    auto: adapt when a region is set, else fixed domain-wide levels. ``True``
    always adapts; ``False`` always uses the fixed levels. When adapting, the
    scale is computed **per forecast hour** (and per region) from that hour's
    min/max, and the storm-total figure adapts to the total. Returns
    ``{"title", "path", "variable", "hour"}`` dicts.
    """
    pred_dir = Path(pred_dir)
    out_dir = Path(out_dir)
    grid = load_grid(grid_dir)
    ic_label = ic_label or f"{date_str} 00:00 UTC"

    available = discover(pred_dir, date_str)
    if variables is None:
        variables = list(available.keys())

    region_line = f"\n{_region_label(region)}" if region is not None else ""
    images: list[dict] = []

    for var in variables:
        var_hours = hours if hours is not None else available.get(var, [])
        fields = {}
        for H in var_hours:
            f = pred_dir / f"{date_str}_{var}_H{H:03d}_ensemble_mean.npy"
            if f.exists():
                fields[H] = np.load(f)
        if not fields:
            continue

        meta = _meta(var)
        is_tp = var == "tp"
        cbar_label = _cbar_label(var)
        # None -> adapt the scale when zoomed into a region; True/False force it.
        adapt = (region is not None) if adaptive_scale is None else adaptive_scale

        if is_tp:
            fields = {H: v * 3 for H, v in fields.items()}
            cmap = norm = None  # tp: scale is computed per hour below (adapts by hour)
        else:
            # Consistent scale across hours: build norm from all displayed data.
            stack = np.concatenate([v.ravel() for v in fields.values()])
            cmap, norm = _cmap_norm_for(var, stack)

        for H in sorted(fields):
            if is_tp:
                # Adapt the precip scale to THIS hour's data (inside the region).
                cmap, norm = precip_cmap_norm([fields[H]], grid=grid, region=region,
                                              adapt=adapt, default_levels=TP_HOUR_LEVELS)
            title = (
                f"CorrDiff Ensemble Mean — {meta['name']} ({var})\n"
                f"Forecast hour +{H:03d}   |   Initial condition: {ic_label}"
                f"{region_line}"
            )
            out_path = out_dir / f"{date_str}_{var}_H{H:03d}_ensemble_mean.png"
            images.append({
                "variable": var, "hour": H,
                "title": f"{meta['name']} ({var}) — Hour +{H:03d}",
                "path": render_field(
                    fields[H], grid, title, cbar_label, cmap, norm,
                    out_path=out_path, region=region, lambert_params=lambert_params,
                    show_max_star=show_max_star,
                ),
            })

        if is_tp:
            total = np.sum(list(fields.values()), axis=0)
            tcmap, tnorm = precip_cmap_norm([total], grid=grid, region=region,
                                            adapt=adapt, default_levels=TP_TOTAL_LEVELS)
            title = (
                f"CorrDiff Ensemble Mean — {meta['name']} ({var}) — Storm total\n"
                f"Sum of hourly estimates   |   Initial condition: {ic_label}"
                f"{region_line}"
            )
            out_path = out_dir / f"{date_str}_{var}_total.png"
            images.append({
                "variable": var, "hour": None,
                "title": f"{meta['name']} ({var}) — Storm total",
                "path": render_field(
                    total, grid, title, "Estimated total precipitation (mm)",
                    tcmap, tnorm, out_path=out_path, region=region,
                    lambert_params=lambert_params, show_max_star=show_max_star,
                ),
            })

    return images


# --------------------------------------------------------------------------- #
# CLI (for quick terminal testing)
# --------------------------------------------------------------------------- #

def _parse_region(text: str | None):
    if not text:
        return None
    parts = [float(x) for x in text.replace(",", " ").split()]
    if len(parts) != 4:
        raise ValueError("--region needs 4 numbers: lat_min,lat_max,lon_min,lon_max")
    return tuple(parts)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Render CorrDiff ensemble-mean maps.")
    p.add_argument("--pred-dir", required=True,
                   help="Folder with the *_ensemble_mean.npy files (e.g. .../OhioRiver/2025-04-04).")
    p.add_argument("--date", required=True, help="Initial-condition date, YYYY-MM-DD.")
    p.add_argument("--grid-dir", required=True,
                   help="Folder holding corrdiff_output_lat.npy / corrdiff_output_lon.npy.")
    p.add_argument("--variables", help="Comma-separated; default = all found in --pred-dir.")
    p.add_argument("--hours", help="Comma-separated forecast hours; default = all found.")
    p.add_argument("--region",
                   help="Mask+zoom to a box: lat_min,lat_max,lon_min,lon_max "
                        "(e.g. 32,41,-97,-82). Omit for full CONUS.")
    p.add_argument("--out-dir", default="corrdiff_plots", help="Where PNGs are written.")
    p.add_argument("--ic-label", help="Override the initial-condition label in titles.")
    p.add_argument("--no-max-star", action="store_true",
                   help="Hide the star marking each field's maximum (shown by default).")
    args = p.parse_args(argv)

    variables = args.variables.replace(",", " ").split() if args.variables else None
    hours = [int(h) for h in args.hours.replace(",", " ").split()] if args.hours else None

    images = plot_prediction(
        pred_dir=args.pred_dir,
        date_str=args.date,
        variables=variables,
        hours=hours,
        out_dir=args.out_dir,
        grid_dir=args.grid_dir,
        region=_parse_region(args.region),
        ic_label=args.ic_label,
        show_max_star=not args.no_max_star,
    )
    print(f"Wrote {len(images)} figure(s) to {args.out_dir}:")
    for im in images:
        print(f"  {im['title']}  ->  {Path(im['path']).name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
