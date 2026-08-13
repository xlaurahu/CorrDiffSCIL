"""Unattended daily driver: NIM predict -> iCHARM zarr(s).

Meant to run as a Kubernetes CronJob in the same namespace as the
``corrdiff-nim-{username}`` service, with this folder's other ``corrdiff_*.py``
files and the grid ``.npy`` files available on disk alongside it (mount this
package as a volume; see README.md). No GPU needed here — inference happens in
the NIM pod; this only calls it over HTTP and does CPU-side pre/post-processing.

Writes both zarr variants iCHARM can consume for one initial-condition date:
  * ``corrdiff_<date>_map.zarr``      — curvilinear grid, globe renderer
  * ``corrdiff_tp_<date>_map.zarr``   — regridded 1-D lat/lon, timeseries API

Configuration is via environment variables (CronJob-friendly — no CLI args to
template into a manifest):

    CORRDIFF_NIM_USERNAME      Required. NIM service suffix, e.g. "laurahu"
                                (service is corrdiff-nim-service-<username>).
    CORRDIFF_OUTPUT_DIR        Where finished zarr stores land.
                                Default: /output/corrdiff
    CORRDIFF_GRID_DIR          Folder with corrdiff_output_lat/lon.npy.
                                Default: /output/corrdiff-grid
    CORRDIFF_PRED_ROOT         Scratch folder for raw .npy prediction output
                                (deleted after conversion). Default: /tmp/corrdiff_predictions
    CORRDIFF_HOURS             Comma-separated forecast hours. Default:
                                3,6,9,12,15,18,21,24
    CORRDIFF_VARIABLES         Comma-separated variables to predict/save.
                                Default: tp,t2m
    CORRDIFF_SAMPLES           Ensemble member count. Default: 5
    CORRDIFF_STEPS             Diffusion step count. Default: 10
    CORRDIFF_DATE_OFFSET_DAYS  Days to subtract from "today" (UTC) for the
                                GEFS initial-condition date, to allow for GEFS
                                publication lag. Default: 1

Exits non-zero on any failure (NIM unreachable/unhealthy, missing grid files,
conversion error) so the CronJob/Job shows as Failed rather than silently
no-op'ing.
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import corrdiff_predict as cd
import corrdiff_to_icharm_zarr as c2i
import corrdiff_to_zarr as c2z

SCRIPT_DIR = Path(__file__).resolve().parent


def _env(name: str, default: str) -> str:
    val = os.environ.get(name, "").strip()
    return val if val else default


def _csv_ints(val: str) -> list[int]:
    return [int(x) for x in val.replace(",", " ").split()]


def main() -> int:
    username = os.environ.get("CORRDIFF_NIM_USERNAME", "").strip()
    if not username:
        print("CORRDIFF_NIM_USERNAME is required.", file=sys.stderr)
        return 1

    output_dir = Path(_env("CORRDIFF_OUTPUT_DIR", "/output/corrdiff"))
    grid_dir = Path(_env("CORRDIFF_GRID_DIR", "/output/corrdiff-grid"))
    pred_root = Path(_env("CORRDIFF_PRED_ROOT", "/tmp/corrdiff_predictions"))
    hours = _csv_ints(_env("CORRDIFF_HOURS", "3,6,9,12,15,18,21,24"))
    variables = _env("CORRDIFF_VARIABLES", "tp,t2m").replace(",", " ").split()
    samples = int(_env("CORRDIFF_SAMPLES", "5"))
    steps = int(_env("CORRDIFF_STEPS", "10"))
    offset_days = int(_env("CORRDIFF_DATE_OFFSET_DAYS", "1"))

    for grid_file in ("corrdiff_output_lat.npy", "corrdiff_output_lon.npy"):
        if not (grid_dir / grid_file).exists():
            print(f"Missing grid file: {grid_dir / grid_file}", file=sys.stderr)
            return 1

    print(f"Checking NIM health for user '{username}' ...")
    if not cd.check_nim_health(username):
        print("NIM is not ready; aborting run.", file=sys.stderr)
        return 1

    ic_date = (datetime.now(timezone.utc) - timedelta(days=offset_days)).date()
    date_str = ic_date.strftime("%Y-%m-%d")
    print(f"Running CorrDiff for initial condition {date_str} "
          f"(hours={hours}, vars={variables}, samples={samples}, steps={steps})")

    print("Setting up GEFS data sources ...")
    ds_gefs, ds_gefs_select = cd.setup_data_sources()

    pred_dir = cd.run_prediction(
        username=username,
        year=ic_date.year, month=ic_date.month, day=ic_date.day,
        hours=hours, samples=samples, steps=steps, variables=variables,
        output_root=str(pred_root),
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    print("Converting to curvilinear (globe) zarr ...")
    c2z.convert(
        pred_dir=pred_dir, date_str=date_str, grid_dir=grid_dir,
        out=output_dir / f"corrdiff_{date_str}_map.zarr",
    )

    if "tp" in variables:
        print("Converting to regridded (timeseries) zarr ...")
        c2i.convert(
            pred_dir=pred_dir, date_str=date_str, grid_dir=grid_dir,
            out=output_dir / f"corrdiff_tp_{date_str}_map.zarr",
            var="tp", stat="mean",
        )

    shutil.rmtree(pred_root, ignore_errors=True)
    print(f"Done. Output in {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
