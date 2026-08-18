# CorrDiff Automation Pipeline

This is a standalone and unattended pipeline that pulls GEFS input, calls a running CorrDiff NIM, and writes the forecast out as plain Zarr stores. It has no dependency on any particular downstream viewer or database — anything that reads Zarr can consume the output.
The scripts can run headless in your terminal or any python environments. 

A single forecast run
(`corrdiff-forecast`) without Kubernetes, GPU access, or any cluster —
see "Quick start" below. 

**Automated and recurring forecasts needs Kubernetes** in order to deploy `cronjob.yaml` (a Kubernetes CronJob), this is how this package schedules itself. There's no non-Kubernetes scheduler built in, a
self-hosted single-node cluster (`minikube`, `kind`, `k3s`, Docker Desktop's Kubernetes) is enough, since the CronJob only ever makes outbound HTTPS calls to someone's public Ingress. See "Running it" for both paths.


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
works as `python <script>.py` without installing.

**What to run next:** run `corrdiff-fetch-grid` once,
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

3. **A Kubernetes cluster — only if you want automated/recurring forecasts**,
   in this package, `cronjob.yaml` is the only
   scheduling mechanism this package provides, and it's a Kubernetes CronJob
   by definition. It does **not** need to be Nautilus, need GPU access, or
   share a namespace with anyone's NIM — the CronJob only ever makes an
   outbound HTTPS call to a public Ingress, so a self-hosted single-node
   cluster you install yourself (`minikube`, `kind`, `k3s`, Docker Desktop's
   Kubernetes) is a complete, independent way to get one. 


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
   (see its docstring), exits non-zero on failure. This is what the CronJob
   example (and any manual debug-pod run) actually executes.

6. **`corrdiff_forecast.py`** -for a person at a terminal who
   wants one specific forecast right now — see "Running it" below.
   `--keep-raw` skips the scratch-dir cleanup, for feeding into
   `corrdiff-plots`/`corrdiff-flood-regions` afterward. `run_forecast()` is
   also directly importable, returning the written zarr paths.

7. **`corrdiff_plots.py`** / **`flood_region_detection.py`** — post-analysis
   (CONUS/region PNG rendering, automatic extreme-precip region detection).
   Read the *raw* `.npy` files, not the Zarr output — use `corrdiff-forecast
   --keep-raw` (or `corrdiff-predict` directly) to keep those around, since
   `run_daily.py`/plain `corrdiff-forecast` delete them after conversion.

## Running it

From anywhere — your own forecasting system, a laptop, a coworker's machine,
a CI job — with no GPU, kubeconfig, or cluster access required. Two ways,
depending on what you're doing:

:one: **One specific forecast, right now** — `corrdiff-forecast`, plain CLI flags:

```bash
corrdiff-forecast --username alice --date 2026-08-16 \
  --output-dir ./out --grid-dir ./post_processing
```

:two: **Unattended / scheduled, "yesterday's" forecast** — `corrdiff-run-daily`,
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

:three: **Automated / recurring — a Kubernetes CronJob:** neither command above
repeats itself; `cronjob.yaml` is the mechanism this package provides for
that. It's a full worked example (ConfigMap-mounted scripts, no custom image
needed — dependencies install into an `emptyDir` via an `initContainer`).
`kubectl` pointed at *any* cluster works — it doesn't have to be Nautilus or
have GPU access (see Prerequisites #3).

Set these once, then every command below is copy/paste:

```bash
export NAMESPACE=your-namespace       # any namespace you can kubectl apply into
export NIM_USERNAME=alice             # whose public Ingress to call
export PVC_NAME=corrdiff-output       # name for the PVC this creates below
```

1. **Create the namespace**, if it doesn't already exist:

   ```bash
   kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
   ```

2. **Create the output PVC** — where finished Zarr stores and the grid files
   land. Adjust `storage`/`storageClassName` to your cluster (drop
   `storageClassName` entirely to use your cluster's default):

   ```bash
   cat <<EOF | kubectl apply -f -
   apiVersion: v1
   kind: PersistentVolumeClaim
   metadata:
     name: $PVC_NAME
     namespace: $NAMESPACE
   spec:
     accessModes: ["ReadWriteOnce"]
     resources:
       requests:
         storage: 20Gi
   EOF
   ```

3. **Create the ConfigMap** holding the driver scripts (from the repo root):

   ```bash
   kubectl create configmap corrdiff-scripts -n "$NAMESPACE" \
     --from-file=post_processing/corrdiff_predict.py \
     --from-file=post_processing/corrdiff_to_zarr.py \
     --from-file=post_processing/corrdiff_to_latlon_zarr.py \
     --from-file=post_processing/corrdiff_fetch_grid.py \
     --from-file=post_processing/run_daily.py \
     --from-file=post_processing/pyproject.toml
   ```

   Re-run this (delete the ConfigMap first, or add `--dry-run=client -o yaml
   | kubectl apply -f -`) whenever these files change.

4. **Fetch the grid files onto the PVC**, once, with a throwaway pod:

   ```bash
   cat <<EOF | kubectl apply -f -
   apiVersion: v1
   kind: Pod
   metadata:
     name: corrdiff-grid-fetch
     namespace: $NAMESPACE
   spec:
     restartPolicy: Never
     containers:
       - name: fetch-grid
         image: python:3.12-slim
         command: ["sh", "-c",
           "pip install 'git+https://github.com/xlaurahu/CorrDiffSCIL.git#subdirectory=post_processing' && corrdiff-fetch-grid --grid-dir /output/corrdiff-grid"]
         volumeMounts:
           - name: corrdiff-output
             mountPath: /output
     volumes:
       - name: corrdiff-output
         persistentVolumeClaim:
           claimName: $PVC_NAME
   EOF

   kubectl wait --for=condition=Ready --timeout=120s pod/corrdiff-grid-fetch -n "$NAMESPACE" 2>/dev/null
   kubectl logs -f pod/corrdiff-grid-fetch -n "$NAMESPACE"   # watch it finish
   kubectl delete pod corrdiff-grid-fetch -n "$NAMESPACE"    # throwaway -- clean up after
   ```

5. **Fill in and apply `cronjob.yaml`** itself. Either edit its three
   `<placeholder>`s by hand, or substitute them on the fly:

   ```bash
   sed -e "s/<your-namespace>/$NAMESPACE/g" \
       -e "s/<your-username>/$NIM_USERNAME/g" \
       -e "s/<your-output-pvc>/$PVC_NAME/g" \
       post_processing/cronjob.yaml | kubectl apply -f -
   ```

6. **Verify it's scheduled**, and optionally trigger one run immediately
   instead of waiting for `00:00 UTC`:

   ```bash
   kubectl get cronjob corrdiff-daily -n "$NAMESPACE"
   kubectl create job --from=cronjob/corrdiff-daily corrdiff-daily-manual-1 -n "$NAMESPACE"
   kubectl get pods -n "$NAMESPACE" --watch          # find the job's pod
   kubectl logs -f <pod-name> -n "$NAMESPACE"        # follow its output
   ```

Note that even a CronJob running in the *same* namespace as the NIM reaches
it via the public Ingress by default (an unnecessary round-trip out to the
internet and back) — if that matters, set `CORRDIFF_NIM_HOST` in
`cronjob.yaml` to the in-cluster Service URL directly
(`http://corrdiff-nim-service-<username>:8000`) instead of
`CORRDIFF_NIM_USERNAME`, to use the free, fast ClusterIP path.



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
