# CorrDiff pipeline

Gets forecasts out of the live `corrdiff-nim-laurahu` NIM (NVIDIA's official
CorrDiff container, `nvcr.io/nim/nvidia/corrdiff:1.0.0`, model
`earth2-corrdiff-us-gefs-hrrr:1.0.0`) running on an L40 GPU in the
`sdsu-shen-climate-lab` namespace on Nautilus, and into iCHARM's
`backend/datasets/` + `metadata.csv`. Scripts originated in the
`corrdiff-auto` project inside the `jupyter-deployment-laurahu` pod and were
copied here; the pipeline has been run successfully end-to-end from this repo
(see "Proven working" below), but is not yet fully automated.

## Data flow

```
GEFS (NOAA, public S3)
        │
        ▼
corrdiff-nim-laurahu (NIM, GPU, in-cluster only)  ◄── corrdiff_predict.py calls /v1/infer
        │
        ▼
raw .npy files (per hour/variable/sample/stat), ephemeral scratch dir
        │
        ├──► corrdiff_to_zarr.py       ──► corrdiff_<date>_map.zarr       (curvilinear, globe view)
        └──► corrdiff_to_icharm_zarr.py ──► corrdiff_tp_<date>_map.zarr   (regridded 1-D, timeseries/regional API)
                        │
                        ▼
        corrdiff-output PVC (/output/corrdiff/, in-cluster)
                        │
                        ▼ kubectl cp (manual — see "Getting output out" below)
                        │
        backend/datasets/*.zarr  (this repo, docker-compose host)
                        │
                        ▼
        metadata.csv row (frontend/src/lib/db/metadata.csv) — one per store
                        │
                        ▼ pnpm run db:push && pnpm run db:seed  (NOT YET RUN)
                        │
        iCHARM data-api / frontend can serve it
```

## Components

1. **`corrdiff_predict.py`** — calls the NIM (`corrdiff-nim-service-{username}:8000/v1/infer`,
   ClusterIP, in-cluster only). Fetches GEFS input via `earth2studio`,
   sends the inference request, saves raw output as `.npy` files:
   `<output_root>/<YYYY-MM-DD>/<date>_<var>_H<HHH>_{sample<i>|ensemble_<stat>}.npy`.
   Built to run headless (`run_prediction()` takes plain args, no prompts) —
   doesn't need Jupyter, just cluster network access.
2. **`corrdiff_to_zarr.py`** — packs the `.npy` files into a curvilinear-grid
   Zarr (`(time, stat, y, x)`, 2-D lat/lon coords). Matches what
   `dataset_local.py`'s `_open_curvilinear_zarr` reads for the globe
   renderer.
3. **`corrdiff_to_icharm_zarr.py`** — regrids the same `.npy` files onto a
   regular 1-D lat/lon mesh (nearest-neighbor, 0.05°) because iCHARM's
   timeseries/regional-average query path only accepts 1-D lat/lon, not
   curvilinear. `tp` gets scaled ×3 here (raw value is a 1-hour accumulation;
   this estimates the 3-hour total). `corrdiff_to_zarr.py` does **not** apply
   this scaling — the two stores' `tp` units are not directly comparable.
4. **`run_daily.py`** — unattended driver: calls `run_prediction()` for
   "yesterday" (`CORRDIFF_DATE_OFFSET_DAYS`, GEFS publication lag), runs both
   conversions, writes to `$CORRDIFF_OUTPUT_DIR`. Fully env-var configured
   (see its docstring), exits non-zero on failure. This is what both the
   CronJob and the manual debug-pod runs actually execute.
5. **`corrdiff_plots.py`** / **`flood_region_detection.py`** — post-analysis
   (CONUS/region PNG rendering, automatic extreme-precip region detection).
   Read the *raw* `.npy` files, not the Zarr output — and `run_daily.py`
   deletes those after conversion. Not wired into the automated path; useful
   standalone if you keep the scratch dir around.

Both zarr scripts need `corrdiff_output_lat.npy` / `corrdiff_output_lon.npy`
(~15 MB each, the native curvilinear grid) in `--grid-dir`. **Gitignored** —
they live on the `corrdiff-output` PVC at `/output/corrdiff-grid/` (copied
there once via a throwaway pod, not through git).

## Cluster resources (all in `sdsu-shen-climate-lab`)

| Resource | Name | Purpose |
|---|---|---|
| PVC | `corrdiff-output` (20Gi, RWO, `rook-ceph-block-tide`) | Dedicated output volume — deliberately *not* the shared `fourcastnet-training-data` PVC, which has an inconsistent root/gridftp-owned permission model not worth fighting for a single-purpose job. Holds `/output/corrdiff/` (finished zarr stores) and `/output/corrdiff-grid/` (grid files). |
| ConfigMap | `corrdiff-scripts` | The 4 driver scripts (`corrdiff_predict.py`, `corrdiff_to_zarr.py`, `corrdiff_to_icharm_zarr.py`, `run_daily.py`), mounted at `/app/corrdiff`. Recreate it (`kubectl delete configmap corrdiff-scripts -n sdsu-shen-climate-lab` then the `kubectl create configmap` command in `cronjob.yaml`'s header) whenever these files change. |
| CronJob | `corrdiff-daily` | Applied, schedule `0 0 * * *`, **not suspended** — but see "Known broken" below, it will fail when it fires until that's fixed. |

## Proven working (2026-07-27/28, manual debug-pod run)

Ran the full predict → NIM inference → convert pipeline successfully by hand:
a plain debug pod (`sleep` command, same image/mounts as the CronJob),
`kubectl exec`'d into directly, running `run_daily.py` with a minimal config
(`CORRDIFF_HOURS=3 CORRDIFF_VARIABLES=tp CORRDIFF_SAMPLES=2 CORRDIFF_STEPS=8`).
Confirmed: NIM call succeeded (`Received 2 sample(s)`), both zarr stores
written correctly. Output pulled to this repo and metadata rows added:

- `backend/datasets/corrdiff_2026-07-27_map.zarr` (81M, curvilinear) →
  `metadata.csv` row `corrdiff_tp_20260727_map`
- `backend/datasets/corrdiff_tp_2026-07-27_map.zarr` (2.9M, regridded) →
  `metadata.csv` row `corrdiff_tp_20260727_ts`

**Not yet done:** `pnpm run db:push && pnpm run db:seed` (needs the
docker-compose Postgres running, which isn't available right now) — so these
two rows aren't in the live database yet, and won't show up in the app until
that's run.

## Known broken: the CronJob will fail as scheduled

Root cause, found via the debug-pod session: **the container image
(`henrylisdsu/nvidia-cuda-12.4:v2.5`) does not include `earth2studio`.** It's
only `pip install --user`'d into `/home/jovyan/.local/` on the Jupyter pod's
own PVC (`jupyter-volume-laurahu`) — nothing else mounts that. Every earlier
CronJob/Job attempt crash-looped near-instantly on
`corrdiff_predict.setup_data_sources()`'s `from earth2studio.data import ...`
— consistent with a fast import error, not a flaky/environmental failure.

Confirmed fix: `pip install --user "earth2studio[data]" aiobotocore` inside a
pod using the same image works (installs `earth2studio-0.9.0`, `torch-2.13.0`,
~90s). This is what unblocked the successful manual run above.

**`cronjob.yaml` does not have this fix yet** — it'll still fail when it next
fires (next `00:00 UTC`). Options, not yet decided:
- Add an `initContainer` (or wrap the main command) that `pip install`s
  `earth2studio[data]` + `aiobotocore` before running `run_daily.py`. Simple,
  but adds ~90s + a real network dependency to every run.
- Build a custom image with `earth2studio` baked in, point the CronJob at
  that instead. Faster/more reliable per-run, more upfront work (Dockerfile,
  somewhere to push it — this namespace's images so far come from Docker Hub
  (`henrylisdsu/...`) or `nvcr.io`/`gitlab-registry.nrp-nautilus.io`, no
  existing internal registry pattern to reuse).

Also worth fixing while touching `cronjob.yaml`: the resource
`limits:requests` ratio was already tightened once (cluster admission policy
caps it at 1.2×) — re-check after any other spec changes, `kubectl apply`
warns if it's violated but doesn't hard-block.

## Getting output out of the cluster: `kubectl cp`, not Globus

Globus was tried first — both sides got real Globus Connect Personal
endpoints registered and working (in-cluster `globus-connect-laurahu`
deployment since torn down; Mac-side collection
`9dcd9649-8a17-11f1-8374-02ce27bde401` still installed locally, harmless to
leave). Hit a hard wall: **transfers between two Globus Connect Personal
collections require a paid Globus subscription** on at least one side — true
of both endpoints here, since the pre-existing
`globus-connect-climate-lab-service` is also just GCP, not a full Globus
Connect Server, despite living in a pod. Free tier doesn't allow it.

So: plain `kubectl cp`, proven working twice now (grid files, then the actual
forecast output):

```bash
kubectl cp sdsu-shen-climate-lab/<pod>:/output/corrdiff/corrdiff_<date>_map.zarr \
  backend/datasets/corrdiff_<date>_map.zarr
kubectl cp sdsu-shen-climate-lab/<pod>:/output/corrdiff/corrdiff_tp_<date>_map.zarr \
  backend/datasets/corrdiff_tp_<date>_map.zarr
```

Since Job-controller pods (`OnFailure`/backoff) get cleaned up by this
cluster very fast after finishing — faster than default Kubernetes behavior,
consistent with other custom admission policies here — the practical pattern
so far has been a **throwaway pod that just mounts the PVC** (`sleep`
command, no Job wrapper), `kubectl cp` from that, then delete it. See
`corrdiff-output-shell` pattern used above (not currently running — was
deleted after use, recreate from the same spec if needed).

## Debugging tip: don't debug through the CronJob/Job

Job-managed pods get deleted within seconds of failing on this cluster —
`kubectl logs --previous` routinely lost the race even with `kubectl wait`
and background log-followers. A plain debug pod (`command: ["sleep", "N"]`,
same image/mounts/env as the CronJob, `kubectl exec` in directly) sidesteps
this entirely: nothing auto-deletes, output is immediate, and you can install
missing packages / poke around interactively. This is how the `earth2studio`
root cause above was actually found.

## Still open

- **Bake the `earth2studio` fix into `cronjob.yaml`** (see "Known broken").
  Until this happens, don't rely on the schedule firing successfully.
- **`pnpm run db:push && pnpm run db:seed`** — needed before the two new rows
  show up in the app. Blocked on docker-compose being runnable again.
- **`sync_corrdiff.py`** — script to automate the `kubectl cp` +
  `metadata.csv`-append steps done manually above, for future runs. Not
  written yet.
- **Image size for `corrdiff-daily-manual-*` runs**: default `run_daily.py`
  config (8 hours × 2 vars × 5 samples × 10 steps) takes long enough that it
  was killed mid-run during testing in favor of a 1-hour/1-var/2-sample/8-step
  smoke test. Fine for proving the pipeline; a real production run should use
  the full config (or something between the two) once the CronJob is fixed.
