# Using the Hosted CorrDiff Endpoint (No GPU, No Kubernetes Required)

This guide is for people who want to **run CorrDiff inference without deploying their own NIM**.
You don't need `kubectl`, an NGC account, a Kubernetes namespace, or a GPU — the model runs on a
NIM that is already deployed and hosted publicly. All you need is Python.

There are three parts: **1)** one-time setup, **2)** send a forecast to the model and get results
back (Steps 1–4), **3)** plot what you got (Step 5).

> [!NOTE]
> This endpoint is shared and has no authentication. Please be considerate of usage (keep
> `samples`/`steps` reasonable) since it runs on a shared lab GPU that other people are using too.
> It's only reachable while the host has their NIM deployment running — if requests fail to
> connect, it may simply be offline. There is no uptime guarantee.

# Prerequisite

You need to have `git` installed on your system.

**Already have it?** Check first, on any OS (Windows PowerShell, macOS, or Linux terminal) —
if this prints a version number, skip the install below:

```bash
git --version
```

```bash
# Install git on Windows OS
winget install --id Git.Git -e --source winget

# Install git on MacOS using Apple's Xcode Command 
xcode-select --install
# If you have homebrew, you can also use 
brew install git 

# Install on Linux
apt-get install git
```
Visit [git](https://git-scm.com/) for more details.

---
# Setup 

## MacOS/Linux Setup

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) installed — that's it, no
manual `pip install` of anything.

**Already have it?** Check first — if this prints a version number, skip straight to cloning the
repo below:

```bash
uv --version
```

```bash
# Install uv 
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env   # or restart your shell so `uv` is on PATH

# Clone the repo
git clone https://github.com/xlaurahu/CorrDiffSCIL.git
cd CorrDiffSCIL

# Install locked dependencies
uv sync
```

**Sanity check** — before writing any code, confirm the endpoint itself is reachable with the
bundled smoke test (sends a synthetic array, not real weather data, so it's quick):

```bash
uv run python test_hosted_endpoint.py https://corrdiff-laurahu.nrp-nautilus.io
```

If that prints `SUCCESS`, you're ready for Steps 1–5 below.

## Windows Setup (via conda)

**1. Install Miniconda.** Already have conda (or Anaconda) installed? Check first, from any
terminal that has it on `PATH` — if this prints a version number, skip straight to step 2:

```powershell
conda --version
```

Otherwise, open PowerShell and run:

```powershell
winget install -e --id Anaconda.Miniconda3
```

(No winget? Download the installer directly from
[anaconda.com/download](https://www.anaconda.com/download) — either Miniconda or full Anaconda
works.) Once installed, open **"Anaconda Prompt (miniconda3)"** from the Start menu — that's the
terminal to use for everything below; it has `conda` on `PATH` automatically.

**2. Clone the repo and create the environment:**

```powershell

# Clone the repo 
git clone https://github.com/xlaurahu/CorrDiffSCIL.git
cd CorrDiffSCIL

# Activate the environment 
conda env create -f environment.yml
conda activate corrdiff-hosted-client
```

This resolves and installs everything — `pygrib`, `cfgrib`, `eccodes`, and `cartopy` from
conda-forge, plus `torch` (CPU-only), `earth2studio`, `matplotlib`, `jupyter`, and the rest via
pip inside that same conda environment. Expect this to take a few minutes the first time.


> [!TIP]
> `environment.yml` automatically sets `KMP_DUPLICATE_LIB_OK=TRUE` whenever you run `conda activate`. This prevents an import-time crash such as:
>
> ```text
> OMP: Error #15: Initializing libomp.dll, but found libiomp5md.dll already initialized
> ```
>
> The error occurs because pip's `torch` and the conda-forge scientific stack load different OpenMP runtimes.
>
> If you created the environment **before** this configuration was added, run the following once, then reactivate the environment:
>
> ```powershell
> conda env config vars set KMP_DUPLICATE_LIB_OK=TRUE -n corrdiff-hosted-client
> conda deactivate
> conda activate corrdiff-hosted-client
> ```


**3. Run the sanity check:**

```powershell
python test_hosted_endpoint.py https://corrdiff-laurahu.nrp-nautilus.io
```

If it prints `SUCCESS`, you're fully set up.

> [!IMPORTANT]
> For the rest of this guide, run everything from this same Anaconda Prompt with
> `corrdiff-hosted-client` activated (`conda activate corrdiff-hosted-client` if you open a new
> terminal). Wherever the guide says `uv run python ...`, use plain `python ...` instead — same
> for `uv run jupyter notebook` → plain `jupyter notebook`. Everything else (the code in each
> step) is identical.

# Running Forecasts

First, we want to pick where to run the code

| | Command | Then |
|---|---|---|
| **Notebook** | `uv run jupyter notebook` or just `jupyter notebook` in conda | Opens Jupyter in your browser. Open [RunningForecasts.ipynb](RunningForecasts.ipynb) — Steps 1–5 are already written as cells, nothing to copy. Run All and you're done. |
| **Script / REPL** | `uv run python your_script.py`, or just `uv run python` | Paste the steps below into a `.py` file, or run them one at a time in the interactive shell. |

If you're not sure which to pick: the notebook is easier for exploring and re-plotting results
(Step 5) without re-running the whole request each time.

> [!TIP]
> The steps below walk through exactly what's in `RunningForecasts.ipynb`, cell by cell — read
> them if you want to understand what the code does or adapt it into your own script. If you just
> want results, you don't need to copy any of it: open the notebook and run it.

## Step 1 — Check the endpoint is up

```python
import requests

BASE_URL = "https://corrdiff-laurahu.nrp-nautilus.io"

r = requests.get(f"{BASE_URL}/v1/health/ready")
print(r.status_code, r.text)
```

## Step 2 — Build an input tensor

CorrDiff expects a specific stack of GEFS variables cropped to a CONUS bounding box, plus a
lead-time field. This runs entirely on CPU.

```python
import os
import posixpath
from datetime import datetime, timedelta
import numpy as np
import torch
from earth2studio.data import GEFS_FX, GEFS_FX_721x1440

# Windows-only fix: earth2studio's GEFS source builds S3 object keys with os.path.join,
# which uses backslashes on Windows -> invalid keys -> 404 FileNotFoundError. This makes
# it use forward slashes instead, without touching os.path anywhere else in the process.
# No-op on Mac/Linux, where os.path.join already produces forward slashes.
if os.name == "nt":
    import earth2studio.data.gefs as _gefs_module

    class _PosixOSProxy:
        path = posixpath
        def __getattr__(self, name):
            return getattr(os, name)

    _gefs_module.os = _PosixOSProxy()

GEFS_SELECT_VARIABLES = ["u10m", "v10m", "t2m", "r2m", "sp", "msl", "tcwv"]
GEFS_VARIABLES = [
    "u1000", "u925", "u850", "u700", "u500", "u250",
    "v1000", "v925", "v850", "v700", "v500", "v250",
    "z1000", "z925", "z850", "z700", "z500", "z200",
    "t1000", "t925", "t850", "t700", "t500", "t100",
    "r1000", "r925", "r850", "r700", "r500", "r100",
]

ds_gefs = GEFS_FX(cache=True)
ds_gefs_select = GEFS_FX_721x1440(cache=True, member="gec00")

def fetch_input_gefs(time: datetime, lead_time: timedelta, content_dtype: str = "float32"):
    dtype = np.dtype(getattr(np, content_dtype))
    g = np.array(9.80665, dtype=dtype)

    select_data = ds_gefs_select(time, lead_time, GEFS_SELECT_VARIABLES).values
    select_data = select_data[:, 0, :, 148:277, 900:1201].astype(dtype)

    pressure_data = ds_gefs(time, lead_time, GEFS_VARIABLES)
    pressure_data = torch.nn.functional.interpolate(
        torch.as_tensor(pressure_data.values),
        (len(GEFS_VARIABLES), 721, 1440),
        mode="nearest",
    ).numpy()
    pressure_data = pressure_data[:, 0, :, 148:277, 900:1201].astype(dtype)

    z_vars = {"z1000", "z925", "z850", "z700", "z500", "z200"}
    z_indices = [i for i, v in enumerate(GEFS_VARIABLES) if v in z_vars]
    pressure_data[:, z_indices, :, :] /= g

    lead_hour = (int(lead_time.total_seconds() // (3 * 60 * 60))
                 * np.ones((1, 1, 129, 301), dtype=dtype))

    return np.concatenate([select_data, pressure_data, lead_hour], axis=1)[None]

# Example: Tennessee Valley flood, forecast initialized 2025-04-04 00Z, 24-hour lead time
time = datetime(2025, 4, 4)
lead_time = timedelta(hours=24)  # must be a multiple of 3 hours, up to 24
input_array = fetch_input_gefs(time, lead_time)
np.save("corrdiff_inputs.npy", input_array)
```

## Step 3 — Send the inference request

```python
r = requests.post(
    f"{BASE_URL}/v1/infer",
    headers={"accept": "application/x-tar"},
    data={
        "samples": 2,   # number of ensemble members
        "steps": 8,    # diffusion steps
        "seed": 0,
    },
    files={"input_array": ("input_array", open("corrdiff_inputs.npy", "rb"))},
    timeout=3000,
)
if r.status_code != 200:
    raise Exception(r.content)

with open("output.tar", "wb") as f:
    f.write(r.content)
```

## Step 4 — Read the output

Each ensemble member comes back as a `.npy` file inside the tar archive.

```python
import io, tarfile
import numpy as np

samples = []
with tarfile.open("output.tar") as tar:
    for member in tar.getmembers():
        buf = io.BytesIO(tar.extractfile(member).read())
        buf.seek(0)
        samples.append(np.load(buf))

print(len(samples), samples[0].shape)
```

Output channel order is `["u10m", "v10m", "t2m", "tp", "csnow", "cicep", "cfrzr", "crain"]`.
For lat/lon coordinates to map the CONUS output grid, download `corrdiff_output_lat.npy` and
`corrdiff_output_lon.npy` from this repo.

## Step 5 — Visualize a variable

`matplotlib` and `cartopy` are included via `uv sync`. Here's the ensemble-mean precipitation
plotted on a Lambert Conformal projection over CONUS:

```python
import numpy as np
import matplotlib.pyplot as plt
import matplotlib as mpl
import matplotlib.colors as mcolors
import cartopy.crs as ccrs
import cartopy.feature as cfeature

lats = np.load("corrdiff_output_lat.npy")
lons = np.load("corrdiff_output_lon.npy") - 360

precp = np.mean([s[0, 0, 3] for s in samples], axis=0)

# Zoom to the Tennessee Valley region
LAT_MIN, LAT_MAX = 32.0, 41.0
LON_MIN, LON_MAX = -97.0, -82.0

# GPCP-style precipitation color scale
gpcp_colors = [
    '#dfc2a5', '#7ec9d0', '#5eb35e', '#99cc33', '#f2f22e',
    '#e6e600', '#ff9999', '#ff4d4d', '#cc0000', '#993366', '#1a1a1a'
]
continuous_cmap = mcolors.LinearSegmentedColormap.from_list("gpcp_cont", gpcp_colors)
hour_levels = np.linspace(0, 40, 11)  # single lead time, not an accumulated total
custom_cmap = mcolors.ListedColormap(continuous_cmap(np.linspace(0, 1, len(hour_levels) - 1)))
norm = mcolors.BoundaryNorm(hour_levels, ncolors=custom_cmap.N)

projection = ccrs.LambertConformal(
    central_longitude=(LON_MIN + LON_MAX) / 2,
    central_latitude=(LAT_MIN + LAT_MAX) / 2,
    standard_parallels=(33, 45),
)

fig = plt.figure(figsize=(12, 8))
ax = fig.add_subplot(1, 1, 1, projection=projection)
ax.set_extent([LON_MIN, LON_MAX, LAT_MIN, LAT_MAX], crs=ccrs.PlateCarree())
c = ax.pcolormesh(lons, lats, precp, transform=ccrs.PlateCarree(), cmap=custom_cmap, norm=norm)
ax.coastlines(linewidth=1)
ax.add_feature(cfeature.STATES, linewidth=0.5, edgecolor="white")

ax.set_title(
    f"CorrDiff Ensemble-Mean Precipitation \n{time:%Y-%m-%d %HZ} + {int(lead_time.total_seconds() // 3600)}h",
    fontsize=13, fontweight = 'bold'
)
fig.colorbar(c, ax=ax, shrink=0.6, ticks=hour_levels[::2], label="Total Hourly Precipitation mm")
fig.savefig("precp.png", dpi=150)
plt.show()  # displays inline if you're in a notebook; no-op otherwise
```

> [!NOTE]
> The first time `cfeature.STATES`/`.coastlines()` run, cartopy downloads Natural Earth map
> data from the internet and caches it locally — expect a short delay on first use only.

---

# Troubleshooting

- **Connection refused / timeout on Step 1** — the host's NIM deployment likely isn't running
  right now. Check with them.
- **413 error on Step 3** — the request body exceeded the ingress upload limit; this is a
  server-side config the host needs to raise, not something you can fix client-side.
- **Request hangs for a long time** — expected. Diffusion inference time scales with
  `samples × steps`. Lower both if you want faster (lower-quality) results.
- **Map shows a colorbar but no data (Step 5)** — if you added `ax.gridlines(draw_labels=True, ...)`
  on top of the `LambertConformal` example, some Cartopy/Matplotlib version combos throw partway
  through label placement, which can silently drop the map layer while the colorbar (drawn
  separately) still renders. Either drop `draw_labels=True`, or add gridlines without labels.
- **`uv sync` fails with `eccodeslib ... doesn't have a source distribution or wheel for the
  current platform` (Windows)** — expected, see [Windows Setup via conda](#windows-setup-via-conda).
- **Jupyter kernel dies with no traceback, specifically on `from earth2studio.data import ...`
  (Windows/conda)** — this is `OMP: Error #15`, a duplicate-OpenMP-runtime crash between pip's
  `torch` and conda-forge's scientific stack. It's silent in Jupyter because it's a native crash,
  not a Python exception — to see the real error, run the same import in a plain `python` REPL
  from Anaconda Prompt instead. Fixed automatically in `environment.yml` going forward; if your
  environment predates that, see the `KMP_DUPLICATE_LIB_OK` fix in
  [Windows Setup via conda](#windows-setup-via-conda).
- **`FileNotFoundError` / 404 fetching a `.idx` file with a backslash in the path (Windows,
  Step 2)**, e.g. `noaa-gefs-pds\gefs.20240926/00\atmos\...` — this is a real bug in
  `earth2studio`'s GEFS source: it builds S3 object keys with `os.path.join`, which uses `\` on
  Windows, producing a key that doesn't exist. It's a genuine upstream issue, not anything in this
  guide's setup. The `_PosixOSProxy` block already included at the top of Step 2 above works
  around it — if you're seeing this error, you're likely running an older copy of Step 2 without
  that fix; re-copy the current version.
