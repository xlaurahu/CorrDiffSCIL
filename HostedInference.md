# Using the Hosted CorrDiff Endpoint (No GPU, No Kubernetes Required)

This guide is for people who want to **run CorrDiff inference without deploying their own NIM**.
You don't need `kubectl`, an NGC account, a Kubernetes namespace, or a GPU — the model runs on a
NIM that is already deployed and hosted publicly. All you need is Python.

> [!NOTE]
> This endpoint is shared and has no authentication. Please be considerate of usage (keep
> `samples`/`steps` reasonable) since it runs on a shared lab GPU that other people are using too.
> It's only reachable while the host has their NIM deployment running — if requests fail to
> connect, it may simply be offline. There is no uptime guarantee.

## Requirements

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) installed — that's it, no
manual `pip install` of anything. Clone or download this repo, then from its root run:

```bash
uv sync
```

This reads [pyproject.toml](pyproject.toml) and installs everything into a local `.venv`
(`requests`, `numpy`, a **CPU-only** build of `torch`, and `earth2studio[data]`) pinned to the
exact versions in [uv.lock](uv.lock), so it resolves the same way on any machine. None of this
touches a GPU — `earth2studio[data]` is only used to download and format public weather forecast
data (GEFS); the actual CorrDiff model runs remotely on the host's NIM.

Run any of the snippets below with `uv run python your_script.py`, or `uv run python` for an
interactive shell — no need to activate the venv yourself.

# 1. Install uv (installs its own Python too — no separate Python setup needed)
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env   # or restart your shell so `uv` is on PATH

# 2. Clone the repo
git clone https://github.com/xlaurahu/CorrDiffSCIL.git
cd CorrDiffSCIL

# 3. Install locked dependencies (requests, numpy, CPU-only torch, earth2studio[data])
uv sync

# 4. Sanity-check the hosted endpoint (synthetic input, fast)
uv run python test_hosted_endpoint.py https://corrdiff-laurahu.nrp-nautilus.io

**Quick sanity check:** before writing anything, confirm the endpoint itself is reachable and
working with the bundled smoke test (uses a synthetic array, not real weather data):

```bash
uv run python test_hosted_endpoint.py https://corrdiff-laurahu.nrp-nautilus.io
```

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
from datetime import datetime, timedelta
import numpy as np
import torch
from earth2studio.data import GEFS_FX, GEFS_FX_721x1440

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

# Example: forecast initialized 2024-09-26 00Z, 15-hour lead time
time = datetime(2024, 9, 26)
lead_time = timedelta(hours=15)  # must be a multiple of 3 hours, up to 24
input_array = fetch_input_gefs(time, lead_time)
np.save("corrdiff_inputs.npy", input_array)
```

## Step 3 — Send the inference request

```python
r = requests.post(
    f"{BASE_URL}/v1/infer",
    headers={"accept": "application/x-tar"},
    data={
        "samples": 5,   # number of ensemble members
        "steps": 10,    # diffusion steps
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

## Troubleshooting

- **Connection refused / timeout on Step 1** — the host's NIM deployment likely isn't running
  right now. Check with them.
- **413 error on Step 3** — the request body exceeded the ingress upload limit; this is a
  server-side config the host needs to raise, not something you can fix client-side.
- **Request hangs for a long time** — expected. Diffusion inference time scales with
  `samples × steps`. Lower both if you want faster (lower-quality) results.
