# CorrDiff Automation Pipeline

This is a standalone and unattended pipeline that pulls GEFS input, calls a running CorrDiff NIM, and writes the forecast out as plain Zarr stores. It has no dependency on any particular downstream viewer or database — anything that reads Zarr can consume the output.
The scripts can run headless in your terminal or any python environments. 

A single forecast run
(`corrdiff-forecast`) without Kubernetes, GPU access, or any cluster —
see "Quick start" below.

**Automated and recurring forecasts just need `cron`.** `corrdiff-run-daily`
is a plain executable, so a normal crontab entry on any machine that stays on
is the whole scheduling story — no cluster, no manifests, nothing else to
deploy. See "Running it" for both paths.


## Quick start

```bash
# 1. Install
pip install ./post_processing

# 2. One-time: get the CorrDiff output grid 
corrdiff-fetch-grid --grid-dir ./post_processing

# 3. Run it, pointed at a username whose NIM has a public Ingress
CORRDIFF_NIM_USERNAME=alice \
CORRDIFF_OUTPUT_DIR=./out CORRDIFF_GRID_DIR=./post_processing \
  corrdiff-run-daily
```



## Install Package

This folder is a self-contained, installable package (see `pyproject.toml`) —
you don't need the rest of this repo, or even a local clone, to use it.

```bash
# Install package from the folder
# (pinned to add-post-processing -- this hasn't merged to main yet; omitting
# the branch installs from main, which doesn't have post_processing/ at all)
pip install "git+https://github.com/xlaurahu/CorrDiffSCIL.git@add-post-processing#subdirectory=post_processing"

# From a local clone:
pip install ./post_processing        # from the repo root
pip install .                        # from inside this folder
pip install -e .                     # editable -- picks up `git pull`s live, for development

# Or run ad hoc without installing, e.g.:
uv run --project post_processing corrdiff-run-daily
```

Installing adds these commands: `corrdiff-predict`, `corrdiff-to-zarr`,
`corrdiff-to-latlon-zarr`, `corrdiff-fetch-grid`, `corrdiff-forecast`,
`corrdiff-run-daily`, `corrdiff-plots`, `corrdiff-flood-regions`. Each also
works as `python <script>.py` without installing.

**What to run next:** run `corrdiff-fetch-grid` once,
then `corrdiff-forecast` or `corrdiff-run-daily` (see "Running it"). Or just
follow "Quick start" at the top of this file for the full sequence.

**Updating:** for the GitHub-URL install, plain `pip install --upgrade` can
silently no-op if the package version hasn't changed (check what you have
with `pip show corrdiff-post-processing`). To force a real re-pull:

```bash
pip install --upgrade --force-reinstall --no-deps \
  "git+https://github.com/xlaurahu/CorrDiffSCIL.git@add-post-processing#subdirectory=post_processing"
```

(`--no-deps` just skips redundantly re-resolving the heavy deps like `torch`
unless those changed too.) `@add-post-processing` pins the branch — swap it
for `@main` once this branch merges, or for a specific tag if tagged releases
of the code itself start existing (separate from the `grid-v1` data release
below).

**Windows:** use a conda/Miniconda environment, not a plain venv (a plain
venv on Windows has hit real install/PATH problems -- Microsoft Store
Python hijacking the `python` command, `earth2studio`'s `pygrib`/`eccodes`
deps having no Windows PyPI wheels). Create the environment **with a Python
version pinned explicitly** -- `conda create -n corrdiff` with no `python=`
makes an empty environment (no interpreter, no `pip`), which silently falls
through to whatever other `python`/`pip` happens to be on PATH and installs
there instead:

```bash
conda create -n corrdiff python=3.12
conda activate corrdiff
where python     # sanity check: should point inside .../envs/corrdiff/
python -m pip install "git+https://github.com/xlaurahu/CorrDiffSCIL.git@add-post-processing#subdirectory=post_processing"
```

Using `python -m pip install ...` (rather than a bare `pip install ...`)
guarantees the install goes into *this* env's `python`, not a stray one
elsewhere on PATH.

**macOS/Linux:** none of the above applies — conda is not needed. A plain
venv works fine, since `pygrib`/`eccodes` publish real PyPI wheels for both
platforms (it's specifically Windows that lacks them), and there's no
Windows-style App Execution Alias hijacking `python`:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install "git+https://github.com/xlaurahu/CorrDiffSCIL.git@add-post-processing#subdirectory=post_processing"
```

## Prerequisites

1. **A username whose CorrDiff NIM is running and has a public Ingress.**
   This package reaches a NIM through its owner's public HTTPS endpoint —
   `https://corrdiff-<username>.nrp-nautilus.io` — by default. That's the
   whole integration surface: give `corrdiff_predict.nim_base_url()` (or
   `--username` / `CORRDIFF_NIM_USERNAME`) a username, and it works from
   anywhere.

 
2. **The CorrDiff output grid**: `corrdiff_output_lat.npy` /
   `corrdiff_output_lon.npy` (~15 MB each, the native curvilinear grid). These
   are fixed by the model itself, not
   specific to any deployment, so they're published once as a
   [GitHub Release](https://github.com/xlaurahu/CorrDiffSCIL/releases/tag/grid-v1)
  . Get them with:

   ```bash
   corrdiff-fetch-grid --grid-dir ./post_processing   # defaults to the current folder
   ```

   A direct download without `git` works too:

   ```bash
   curl -LO https://github.com/xlaurahu/CorrDiffSCIL/releases/download/grid-v1/corrdiff_output_lat.npy`

   ```
   and the same for `corrdiff_output_lon.npy`.

3. **Nothing else — if you want automated/recurring forecasts, any machine
   with `cron`** (or Task Scheduler, systemd timers, etc.) that stays on and
   has network access to the NIM's public Ingress is a complete scheduling
   setup. No cluster, no extra software to install.


## Data flow

```
GEFS (NOAA, public S3)
        │
        ▼
https://corrdiff-<username>.nrp-nautilus.io  ◄── corrdiff_predict.py calls /v1/infer
(someone's NIM, GPU, behind their public Ingress)
        │
        ▼
raw .npy files (per hour/variable/sample/stat), scratch dir
        │
        ├──► corrdiff_to_zarr.py        ──► corrdiff_<date>_map.zarr      (curvilinear, globe-style view)
        └──► corrdiff_to_latlon_zarr.py ──► corrdiff_tp_<date>_map.zarr   (regridded 1-D, timeseries/regional queries)
                        │
                        ▼
                 wherever you point --out / $CORRDIFF_OUTPUT_DIR
                        │
                        ▼
        any Zarr consumer: xarray, a notebook, your own backend,
        iCHARM's Data API, ... (see "Using the output" below)
```

## Components

1. **`corrdiff_predict.py`** — calls the NIM (by default,
   `https://corrdiff-{username}.nrp-nautilus.io/v1/infer`). Fetches GEFS input
   via `earth2studio`, sends the inference request, saves raw output as
   `.npy` files: `<output_root>/<YYYY-MM-DD>/<date>_<var>_H<HHH>_{sample<i>|ensemble_<stat>}.npy`.

2. **`corrdiff_to_zarr.py`** — packs the `.npy` files into a curvilinear-grid
   Zarr (`(time, stat, y, x)`, 2-D lat/lon coords). Good for globe-style
   renderers that can handle a curvilinear grid directly.

3. **`corrdiff_to_latlon_zarr.py`** — regrids the same `.npy` files onto a
   regular 1-D lat/lon mesh (nearest-neighbor, 0.05° by default) for
   consumers whose query path only accepts 1-D lat/lon (e.g. simple
   timeseries/regional-average lookups). `tp` gets scaled ×3 here (raw value
   is a 1-hour accumulation; this estimates the 3-hour total).
   `corrdiff_to_zarr.py` does **not** apply this scaling — the two stores'
   `tp` units are not directly comparable.

4. **`corrdiff_fetch_grid.py`** — one-time (or re-run-to-update) download of
   the grid files above from the GitHub Release into `--grid-dir`.

5. **`run_daily.py`** — unattended driver: calls `run_prediction()` for
   "yesterday" (`CORRDIFF_DATE_OFFSET_DAYS`, GEFS publication lag), runs both
   conversions, writes to `$CORRDIFF_OUTPUT_DIR`. Fully env-var configured
   (see its docstring), exits non-zero on failure. This is what the crontab
   example below actually executes.

6. **`corrdiff_forecast.py`** — for a person at a terminal who
   wants one specific forecast right now — see "Running it" below.
   `--plot` renders ensemble-mean map PNGs right after conversion (runs
   before the raw-file cleanup, so it works with or without `--keep-raw`);
   `--plot-dir` picks where they land (default `<output-dir>/plots`);
   `--region lat_min,lat_max,lon_min,lon_max` masks + zooms *those plots* to
   a sub-box — it does **not** crop the Zarr/data output, and it's a literal
   lat/lon box, not a named place (`--region` for "the Gulf Coast" isn't a
   thing; you'd need the actual coordinates). `--keep-raw` skips the
   scratch-dir cleanup entirely, for feeding into `corrdiff-plots`/
   `corrdiff-flood-regions` separately afterward. `run_forecast()` is also
   directly importable, returning the written zarr (and, if requested, plot)
   paths.

7. **`corrdiff_plots.py`** / **`flood_region_detection.py`** — post-analysis
   (CONUS/region PNG rendering, automatic extreme-precip region detection).
   Read the *raw* `.npy` files, not the Zarr output. `corrdiff-forecast
   --plot` already covers the common case (ensemble-mean maps, optionally
   zoomed via `--region`); reach for these directly for anything `--plot`
   doesn't cover, using `corrdiff-forecast --keep-raw` (or `corrdiff-predict`
   directly) to keep the raw files around for it, since `run_daily.py`/plain
   `corrdiff-forecast` delete them after conversion.

## Running it

From anywhere — your own forecasting system, a laptop, a coworker's machine,
a CI job — with no GPU, kubeconfig, or cluster access required. Two ways,
depending on what you're doing:

:new: **One specific forecast, right now** — `corrdiff-forecast`, plain CLI flags:

```bash
corrdiff-forecast --username alice --date 2026-08-16 \
  --output-dir ./out --grid-dir ./post_processing
```

Add `--plot` to also get PNGs out of the same run (default
`<output-dir>/plots`; use `--plot-dir` to put them elsewhere), and `--region
lat_min,lat_max,lon_min,lon_max` to zoom those PNGs to a sub-box (e.g. the
Gulf Coast is `--region 24,31,-98,-80`, not a name `corrdiff-forecast`
understands) — `--region` only changes the rendered images, not the
underlying Zarr data:

```bash
corrdiff-forecast --username alice --date 2026-08-16 \
  --output-dir ./out --grid-dir ./post_processing \
  --plot --region 24,31,-98,-80
```

Or a single-hour run — e.g. just hour 3 of the August 30th initial condition,
with plots:

```bash
corrdiff-forecast --username alice --date 2026-08-30 --hours 3 \
  --output-dir ./out --grid-dir ./post_processing \
  --plot
```

:repeat_one: **Unattended / scheduled, "yesterday's" forecast** — `corrdiff-run-daily`,
env-var configured (see "Configuration" above):

```bash
CORRDIFF_NIM_USERNAME=alice \
CORRDIFF_OUTPUT_DIR=./out CORRDIFF_GRID_DIR=./post_processing \
  corrdiff-run-daily
```

Both need `alice`'s Ingress to actually be deployed and pointed at a healthy
NIM — see `corrdiff-nim-ingress.yaml` in the repo root. Neither needs
Kubernetes for anything — that's only relevant if you're deploying your own
NIM, which is a separate concern from running this package.

:repeat: **Automated / recurring — a crontab entry:** neither command above
repeats itself. `corrdiff-run-daily` is a plain executable, so a normal
crontab entry is the whole mechanism — no cluster, no manifests, nothing
else to install. Runs on whatever machine you set it up on, as long as that
machine stays on and has network access to the NIM's public Ingress:

```bash
# crontab -e
0 0 * * * CORRDIFF_NIM_USERNAME=alice \
  CORRDIFF_OUTPUT_DIR=/home/you/corrdiff_output \
  CORRDIFF_GRID_DIR=/home/you/corrdiff_grid \
  /path/to/venv/bin/corrdiff-run-daily >> /var/log/corrdiff.log 2>&1
```

Use absolute paths for the installed command and for `CORRDIFF_OUTPUT_DIR`/
`CORRDIFF_GRID_DIR` — cron runs with a minimal environment and no working
directory to assume. `>> ... 2>&1` keeps a log you can check after the fact,
since cron won't show you failures otherwise. To test it immediately instead
of waiting for `00:00 UTC`, just run the same line by hand once.

If you're already on Kubernetes for other reasons (deploying your own NIM,
say) and would rather schedule this as a CronJob there instead of via
`cron`, the env vars are the same — point a CronJob's container at
`corrdiff-run-daily` with this section's variables set, same as any other
scheduler.



## Configuration

`run_daily.py` is configured entirely via environment variables (scheduler-
friendly — nothing to template into a manifest):

| Variable | Default | Meaning |
|---|---|---|
| `CORRDIFF_NIM_USERNAME` | *(required unless `CORRDIFF_NIM_HOST` is set)* | NIM owner's username, e.g. `alice` — reaches their public Ingress (`https://corrdiff-<username>.nrp-nautilus.io`). No built-in auth on that endpoint; only use one you trust. |
| `CORRDIFF_NIM_HOST` | unset | Explicit NIM host/URL, overriding `CORRDIFF_NIM_USERNAME` entirely. Escape hatch for a NIM reached some other way; most users won't need this. |
| `CORRDIFF_OUTPUT_DIR` | `./corrdiff_output` | Where finished zarr stores land. |
| `CORRDIFF_GRID_DIR` | `/output/corrdiff-grid` | Folder with `corrdiff_output_lat/lon.npy`. |
| `CORRDIFF_PRED_ROOT` | `/tmp/corrdiff_predictions` | Scratch folder for raw `.npy` prediction output (deleted after conversion). |
| `CORRDIFF_HOURS` | `3,6,9,12,15,18,21,24` | Comma-separated forecast hours. |
| `CORRDIFF_VARIABLES` | `tp,t2m` | Comma-separated variables to predict/save. |
| `CORRDIFF_SAMPLES` | `5` | Ensemble member count. |
| `CORRDIFF_STEPS` | `10` | Diffusion step count. |
| `CORRDIFF_DATE_OFFSET_DAYS` | `1` | Days to subtract from "today" (UTC) for the GEFS initial-condition date, to allow for GEFS publication lag. |

The individual scripts (`corrdiff_predict.py`, `corrdiff_to_zarr.py`,
`corrdiff_to_latlon_zarr.py`) also work standalone with CLI args — run any of
them with `--help` (or `corrdiff-predict --help` etc. once installed).


## Using the output

The two Zarr stores are plain, self-describing Zarr — read them with
`xarray.open_zarr(...)` from anywhere. Two integration paths:

- **Generic**: just read the store with xarray/zarr in your own code. No
  other setup needed.
- **iCHARM**: iCHARM's Data API needs zarr v2 + a regular 1-D lat/lon grid,
  which is exactly what `corrdiff_to_latlon_zarr.py` produces. Drop the
  resulting `.zarr` into iCHARM's `backend/datasets/`, add a row to
  `frontend/src/lib/db/metadata.csv`, then `pnpm run db:push && pnpm run
  db:seed` to register it.

## Still open

- **A downstream-registration step** ("take the finished zarr and tell some
  consumer about it") is deliberately *not* part of this package — see
  "Using the output" above. It belongs in whatever repo owns that consumer,
  calling into `corrdiff_forecast.run_forecast()` and then doing its own
  registration (e.g. iCHARM's `metadata.csv` row + `pnpm run db:push &&
  db:seed`), not here.
