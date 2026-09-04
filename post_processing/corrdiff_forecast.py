"""Ad hoc CorrDiff forecast run: predict + convert, for one explicit date.

This is the interactive counterpart to ``run_daily.py``. ``run_daily.py`` is
env-var configured and always targets "today minus N days" -- built for
unattended/scheduled runs (a crontab entry is the normal way; a Kubernetes
CronJob works too if you're already on one). This script is for a person at
a terminal who wants one specific date, right now, with plain CLI flags.
Neither this nor ``run_daily.py`` needs Kubernetes, cron, or any scheduler
to run directly -- both are just Python processes; scheduling is only about
triggering ``run_daily.py`` on a timer, which is optional.

Talks to a NIM the same way as everything else in this package (see
``corrdiff_predict.nim_base_url()``): a username reaching that NIM owner's
public Ingress by default, or ``--nim-host`` to point somewhere else.

Usage (installed, see this folder's pyproject.toml):
    corrdiff-forecast --username alice --date 2026-08-16 \\
        --hours 3,6,9,12 --variables tp,t2m \\
        --output-dir ./out --grid-dir ./post_processing

``run_forecast()`` is also importable directly -- the building block a future
higher-level API (e.g. an ``open_forecast()``-style helper) would call.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import date as date_cls
from datetime import datetime
from pathlib import Path

import corrdiff_predict as cd
import corrdiff_plots as cp
import corrdiff_to_latlon_zarr as c2l
import corrdiff_to_zarr as c2z

DEFAULT_GRID_DIR = Path(__file__).resolve().parent
DEFAULT_HOURS = [3, 6, 9, 12, 15, 18, 21, 24]
DEFAULT_VARIABLES = ["tp", "t2m"]


def run_forecast(
    date: str | date_cls,
    username: str | None = None,
    host: str | None = None,
    hours=DEFAULT_HOURS,
    variables=DEFAULT_VARIABLES,
    samples: int = 5,
    steps: int = 10,
    output_dir: str | Path = "./corrdiff_output",
    grid_dir: str | Path = DEFAULT_GRID_DIR,
    pred_root: str | Path = "/tmp/corrdiff_predictions",
    keep_raw: bool = False,
    skip_health_check: bool = False,
    plot: bool = False,
    plot_dir: str | Path | None = None,
    region=None,
) -> dict[str, Path]:
    """Run predict + convert for one explicit initial-condition ``date``.

    ``date`` is a ``YYYY-MM-DD`` string or a ``datetime.date`` -- an actual
    date, not an offset from "today" (that's what ``run_daily.py`` is for).

    Returns a dict of the zarr stores written, e.g.::

        {"map": Path(".../corrdiff_2026-08-16_map.zarr"),
         "timeseries": Path(".../corrdiff_tp_2026-08-16_map.zarr")}   # only if "tp" requested

    Raises on any failure (bad grid dir, unhealthy NIM unless
    ``skip_health_check``, inference/conversion errors) -- this does not
    catch and print-and-exit the way the CLI ``main()`` does.
    """
    if isinstance(date, str):
        ic_date = datetime.strptime(date, "%Y-%m-%d").date()
    else:
        ic_date = date
    date_str = ic_date.strftime("%Y-%m-%d")

    output_dir = Path(output_dir)
    grid_dir = Path(grid_dir)
    pred_root = Path(pred_root)

    for grid_file in ("corrdiff_output_lat.npy", "corrdiff_output_lon.npy"):
        if not (grid_dir / grid_file).exists():
            raise FileNotFoundError(
                f"Missing grid file: {grid_dir / grid_file} "
                "(run `corrdiff-fetch-grid` first)."
            )

    if not skip_health_check:
        print(f"Checking NIM health ({cd.nim_base_url(username, host)}) ...")
        if not cd.check_nim_health(username, host=host):
            raise RuntimeError("NIM is not ready.")

    print(f"Running CorrDiff for initial condition {date_str} "
          f"(hours={hours}, vars={variables}, samples={samples}, steps={steps})")
    print("Setting up GEFS data sources ...")
    ds_gefs, ds_gefs_select = cd.setup_data_sources()

    pred_dir = cd.run_prediction(
        username=username,
        year=ic_date.year, month=ic_date.month, day=ic_date.day,
        hours=hours, samples=samples, steps=steps, variables=variables,
        output_root=str(pred_root),
        host=host,
        ds_gefs=ds_gefs, ds_gefs_select=ds_gefs_select,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    print("Converting to curvilinear (globe) zarr ...")
    written["map"] = c2z.convert(
        pred_dir=pred_dir, date_str=date_str, grid_dir=grid_dir,
        out=output_dir / f"corrdiff_{date_str}_map.zarr",
    )

    if "tp" in variables:
        print("Converting to regridded (timeseries) zarr ...")
        written["timeseries"] = c2l.convert(
            pred_dir=pred_dir, date_str=date_str, grid_dir=grid_dir,
            out=output_dir / f"corrdiff_tp_{date_str}_map.zarr",
            var="tp", stat="mean",
        )

    if plot:
        _plot_dir = Path(plot_dir) if plot_dir else output_dir / "plots"
        print(f"Generating plots in {_plot_dir} ...")
        images = cp.plot_prediction(
            pred_dir=pred_dir,
            date_str=date_str,
            variables=variables,
            hours=hours,
            out_dir=_plot_dir,
            grid_dir=grid_dir,
            region=region,
        )
        print(f"Wrote {len(images)} figure(s).")
        written["plots"] = _plot_dir

    if keep_raw:
        print(f"Raw .npy files kept at {pred_dir} (for corrdiff-plots / corrdiff-flood-regions).")
    else:
        shutil.rmtree(pred_root, ignore_errors=True)

    print(f"Done. Output in {output_dir}")
    return written


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--username", help="NIM owner's username (their public Ingress).")
    parser.add_argument("--nim-host", help="Explicit NIM host/URL, overriding --username.")
    parser.add_argument("--date", required=True, help="Initial-condition date, YYYY-MM-DD.")
    parser.add_argument("--hours", default=",".join(map(str, DEFAULT_HOURS)),
                        help=f"Comma-separated forecast hours (default {DEFAULT_HOURS}).")
    parser.add_argument("--variables", default=",".join(DEFAULT_VARIABLES),
                        help=f"Comma-separated variables (default {DEFAULT_VARIABLES}).")
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--steps", type=int, default=10)
    parser.add_argument("--output-dir", default="./corrdiff_output")
    parser.add_argument("--grid-dir", default=str(DEFAULT_GRID_DIR))
    parser.add_argument("--pred-root", default="/tmp/corrdiff_predictions")
    parser.add_argument("--keep-raw", action="store_true",
                        help="Don't delete the raw .npy scratch dir after conversion "
                             "-- needed if you also want to run corrdiff-plots / "
                             "corrdiff-flood-regions on this run's output.")
    parser.add_argument("--skip-health-check", action="store_true")
    parser.add_argument("--plot", action="store_true",
                        help="Generate ensemble-mean map figures after conversion.")
    parser.add_argument("--plot-dir",
                        help="Where to write PNGs (default: <output-dir>/plots).")
    parser.add_argument("--region",
                        help="Mask + zoom plots to a lat/lon box: "
                             "lat_min,lat_max,lon_min,lon_max (e.g. 32,41,-97,-82).")
    args = parser.parse_args(argv)

    if not args.username and not args.nim_host:
        print("--username or --nim-host is required.", file=sys.stderr)
        return 1

    region = None
    if args.region:
        parts = [float(x) for x in args.region.replace(",", " ").split()]
        if len(parts) != 4:
            print("--region needs 4 numbers: lat_min,lat_max,lon_min,lon_max", file=sys.stderr)
            return 1
        region = tuple(parts)

    try:
        run_forecast(
            date=args.date,
            username=args.username,
            host=args.nim_host,
            hours=[int(h) for h in args.hours.replace(",", " ").split()],
            variables=args.variables.replace(",", " ").split(),
            samples=args.samples,
            steps=args.steps,
            output_dir=args.output_dir,
            grid_dir=args.grid_dir,
            pred_root=args.pred_root,
            keep_raw=args.keep_raw,
            skip_health_check=args.skip_health_check,
            plot=args.plot,
            plot_dir=args.plot_dir,
            region=region,
        )
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
