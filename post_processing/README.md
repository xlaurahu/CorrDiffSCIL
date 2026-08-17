# CorrDiff Automation Pipeline

Standalone, unattended pipeline that pulls GEFS input, calls a running CorrDiff
NIM (NVIDIA's official CorrDiff container,
`nvcr.io/nim/nvidia/corrdiff:1.0.0`, model `earth2-corrdiff-us-gefs-hrrr:1.0.0`),
and writes the forecast out as plain Zarr stores. It has no dependency on any
particular downstream viewer or database — anything that reads Zarr (xarray,
a notebook, a web backend, iCHARM's Data API, ...) can consume the output.
Runs headless (no Jupyter, no prompts) so it can be driven by a scheduler —
this folder includes a worked Kubernetes CronJob example, but any scheduler
that can run a Python process works.

## Quick start

```bash
# 1. Install (from a clone, or straight from GitHub -- see "Install" below)
pip install ./post_processing

# 2. One-time: get the CorrDiff output grid (same for everyone, not tied to
#    any deployment -- see "Prerequisites" below)
corrdiff-fetch-grid --grid-dir ./post_processing

# 3. Run it, pointed at a username whose NIM has a public Ingress
CORRDIFF_NIM_USERNAME=alice \
CORRDIFF_OUTPUT_DIR=./out CORRDIFF_GRID_DIR=./post_processing \
  corrdiff-run-daily
```

That's the whole loop — no GPU, no Kubernetes access, no kubeconfig needed on
your side. `./out` ends up with two Zarr stores (see "Using the output"
below). Everything after this point is reference detail: what each piece
does, every configuration knob, and the Kubernetes-specific paths (running
your own NIM, scheduling as a CronJob) if you need those too.

## Install

This folder is a self-contained, installable package (see `pyproject.toml`) —
you don't need the rest of this repo, or even a local clone, to use it.

```bash
# Straight from GitHub, no clone at all -- the normal way to pull this into
# your own project:
pip install "git+https://github.com/xlaurahu/CorrDiffSCIL.git#subdirectory=post_processing"

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
works as `python <script>.py` without installing, provided the dependencies
in `pyproject.toml` are available.

**What to run next:** `corrdiff-fetch-grid` (once — see "Prerequisites"),
then `corrdiff-forecast` or `corrdiff-run-daily` (see "Running it"). Or just
follow "Quick start" at the top of this file for the full sequence.

**Updating:** for the GitHub-URL install, plain `pip install --upgrade` can
silently no-op if the package version hasn't changed (check what you have
with `pip show corrdiff-post-processing`). To force a real re-pull:

```bash
pip install --upgrade --force-reinstall --no-deps \
  "git+https://github.com/xlaurahu/CorrDiffSCIL.git#subdirectory=post_processing"
```

(`--no-deps` just skips redundantly re-resolving the heavy deps like `torch`
unless those changed too.) You can also pin a specific version by appending
`@<tag>` before the `#subdirectory=...` part, once tagged releases of the
code itself exist (separate from the `grid-v1` data release below).

## Prerequisites

1. **A username whose CorrDiff NIM is running and has a public Ingress.**
   This package reaches a NIM through its owner's public HTTPS endpoint —
   `https://corrdiff-<username>.nrp-nautilus.io` — by default. That's the
   whole integration surface: give `corrdiff_predict.nim_base_url()` (or
   `--username` / `CORRDIFF_NIM_USERNAME`) a username, and it works from
   anywhere — your own forecasting system, a laptop, a CI job — with no
   Kubernetes access, kubeconfig, or GPU required on your side.

   **No built-in authentication** — anyone with the URL can submit inference
   requests against that GPU. Only use a username whose NIM you trust, and
   only stand up your own Ingress if you're prepared to share the URL that
   broadly. The repo root's `corrdiff-nim-deployment.yaml` /
   `corrdiff-nim-ingress.yaml` are a starting point for standing one up.

   If you need to reach a NIM some other way (it's not behind that Ingress
   pattern at all), `--nim-host` / `CORRDIFF_NIM_HOST` is an escape hatch:
   any `host:port` or full `http(s)://...` URL, overriding username entirely.
   Most users won't need this.
2. **The CorrDiff output grid**: `corrdiff_output_lat.npy` /
   `corrdiff_output_lon.npy` (~15 MB each, the native curvilinear grid). These
   are fixed by the model itself (`earth2-corrdiff-us-gefs-hrrr:1.0.0`), not
   specific to any deployment, so they're published once as a
   [GitHub Release](https://github.com/xlaurahu/CorrDiffSCIL/releases/tag/grid-v1)
   rather than being cluster-only. Get them with:

   ```bash
   corrdiff-fetch-grid --grid-dir ./post_processing   # defaults to the current folder
   ```

   Plain HTTPS, no auth, no git, no Kubernetes. Re-run it (with `--force`) to
   pick up a newer grid release later (`--release <tag>` to pin a specific
   one). Not installed the package? A direct download works too:
   `curl -LO https://github.com/xlaurahu/CorrDiffSCIL/releases/download/grid-v1/corrdiff_output_lat.npy`
   (and the same for `corrdiff_output_lon.npy`).

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
   Built to run headless (`run_prediction()` takes plain args, no prompts) —
   doesn't need Jupyter, just internet access to the NIM's public Ingress.
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
   (see its docstring), exits non-zero on failure. This is what the CronJob
   example (and any manual debug-pod run) actually executes.
6. **`corrdiff_forecast.py`** — the ad hoc counterpart to `run_daily.py`: same
   predict-then-convert pipeline, but CLI flags instead of env vars, and an
   explicit `--date` instead of "yesterday". For a person at a terminal who
   wants one specific forecast right now — see "Running it" below.
   `--keep-raw` skips the scratch-dir cleanup, for feeding into
   `corrdiff-plots`/`corrdiff-flood-regions` afterward. `run_forecast()` is
   also directly importable, returning the written zarr paths.
7. **`corrdiff_plots.py`** / **`flood_region_detection.py`** — post-analysis
   (CONUS/region PNG rendering, automatic extreme-precip region detection).
   Read the *raw* `.npy` files, not the Zarr output — use `corrdiff-forecast
   --keep-raw` (or `corrdiff-predict` directly) to keep those around, since
   `run_daily.py`/plain `corrdiff-forecast` delete them after conversion.

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

## Running it

From anywhere — your own forecasting system, a laptop, a coworker's machine,
a CI job — with no GPU, kubeconfig, or cluster access required. Two ways,
depending on what you're doing:

**One specific forecast, right now** — `corrdiff-forecast`, plain CLI flags:

```bash
corrdiff-forecast --username alice --date 2026-08-16 \
  --output-dir ./out --grid-dir ./post_processing
```

**Unattended / scheduled, "yesterday's" forecast** — `corrdiff-run-daily`,
env-var configured (see "Configuration" above):

```bash
CORRDIFF_NIM_USERNAME=alice \
CORRDIFF_OUTPUT_DIR=./out CORRDIFF_GRID_DIR=./post_processing \
  corrdiff-run-daily
```

Both need `alice`'s Ingress to actually be deployed and pointed at a healthy
NIM — see `corrdiff-nim-ingress.yaml` in the repo root. Neither needs
Kubernetes to *run* — that's only relevant if you're deploying your own NIM,
or scheduling `corrdiff-run-daily` as a CronJob (below), not for calling
either command directly.

**On Kubernetes, as a scheduled CronJob:** `cronjob.yaml` is a full worked
example (ConfigMap-mounted scripts, no custom image needed — dependencies
install into an `emptyDir` via an `initContainer`). Every `<placeholder>` in
it needs filling in for your namespace/username/PVC before applying; see the
comments at the top of the file for the exact steps. Note that even a CronJob
running in the *same* namespace as the NIM reaches it via the public Ingress
by default (an unnecessary round-trip out to the internet and back) — if that
matters, set `CORRDIFF_NIM_HOST` to the in-cluster Service URL directly
(`http://corrdiff-nim-service-<username>:8000`) instead of
`CORRDIFF_NIM_USERNAME`, to use the free, fast ClusterIP path.

## Getting output out of a Kubernetes cluster

If you don't have (or don't want) a shared filesystem/PVC mount reachable
from outside the cluster, plain `kubectl cp` works:

```bash
kubectl cp <namespace>/<pod>:/output/corrdiff/corrdiff_<date>_map.zarr \
  ./corrdiff_<date>_map.zarr
```

A **throwaway pod that just mounts the PVC** (`sleep` command, no Job
wrapper) is a convenient way to get a `kubectl cp` source without waiting on
a real run — `kubectl cp` from it, then delete it. (We also tried Globus for
this; free-tier Globus Connect Personal can't transfer between two personal
endpoints without a paid subscription on at least one side, so it's not
included here — plain `kubectl cp` is simpler for a single PVC anyway.)

**Debugging tip:** don't debug through the CronJob/Job itself. Job-managed
pods on many clusters get deleted within seconds of failing, which can lose
the race with `kubectl logs --previous`. A plain debug pod
(`command: ["sleep", "N"]`, same image/mounts/env as the CronJob, `kubectl
exec` in directly) sidesteps this: nothing auto-deletes, output is
immediate, and you can install missing packages / poke around interactively.

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

## Notes on the CronJob example's design

- **No custom-built image.** The `initContainer` approach (`pip install
  --target=/deps ...` into an `emptyDir`, then `PYTHONPATH` picks it up in
  the main container) avoids needing a Dockerfile or somewhere to push one —
  handy if your cluster doesn't have an established internal registry
  pattern. It costs some install time on every run; if that matters, bake a
  custom image with the dependencies (see `pyproject.toml`) pre-installed
  and drop the `initContainer` instead.
- **No GPU on this pod.** Inference happens in the NIM pod; this only calls
  it over HTTP and does CPU-side pre/post-processing.
- **Resource limits:requests ratio.** Some clusters cap this via admission
  policy (e.g. 1.2×) — if `kubectl apply` warns or your job gets rejected,
  check yours before setting requests/limits far apart.

## Still open

- **A `sync` script** to automate "pull the finished zarr out of the
  cluster + register it with a downstream consumer" — today that's a manual
  `kubectl cp` (+, for iCHARM, a manual `metadata.csv` row and `pnpm`
  commands). Not written yet.
